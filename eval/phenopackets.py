"""Real patients from phenopacket-store, the Monarch Initiative's collection of case reports.

Each GA4GH phenopacket in the store was curated from a published article: the
individual's observed HPO features, the features the article explicitly rules
out (``excluded``), the causative variants with their gene, and the article's
PMID. The release is pinned by tag and sha256; ``locate_or_download`` finds
the archive under ``data/phenopacket-store/<tag>/`` (git-ignored) or
downloads it, and refuses a file with another hash.

A phenopacket becomes a case when it has exactly one causative gene, the gene
is in the model's vocabulary, and at least ``MIN_OBSERVED`` observed features
resolve to phenotypic-abnormality terms of the knowledge base. What the family
reports before any question is 3 to 6 mutually unrelated observed features,
drawn uniformly with a fixed seed, plus one excluded feature when the record
has one unrelated to them. Every question is then answered from the record
(``RecordedPatient``): "sí" when the asked term or a descendant was observed,
"no" when it or an ancestor was excluded, and "no sé" otherwise, because an
article that does not mention a finding does not say the patient lacks it.
"""

from __future__ import annotations

import hashlib
import json
import random
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from eval.cases import Case, sample_present
from eval.harness import REPO_ROOT, TOP_K, stable_seed

from diagnosticar_preguntas.domain import AnswerValue, ModelClient
from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase
from diagnosticar_preguntas.ranking.model_client import NoKnownHpoIdsError

RELEASE = "0.1.27"
ASSET = "all_phenopackets.zip"
URL = f"https://github.com/monarch-initiative/phenopacket-store/releases/download/{RELEASE}/{ASSET}"
SHA256 = "d0c70005bb09b87035087516252b6b539fbc0415be7c664b73c9cde7eedf205a"
DATA_DIR = REPO_ROOT / "data" / "phenopacket-store"
SEED = 20260928
MIN_OBSERVED = 6
PRESENT_RANGE = (3, 6)

EXCLUSION_REASONS = (
    "no_causative_gene",
    "several_causative_genes",
    "gene_not_in_model_vocabulary",
    "fewer_observed_features",
    "fewer_unrelated_features",
    "model_knows_no_reported_term",
)


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def locate_or_download(directory: Path = DATA_DIR) -> Path:
    """Path to the pinned release archive, downloading it on first use."""
    path = directory / RELEASE / ASSET
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        with httpx.stream("GET", URL, follow_redirects=True, timeout=120.0) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        partial.replace(path)
    actual = sha256_of(path)
    if actual != SHA256:
        raise ChecksumError(f"{path}: sha256 {actual}, expected {SHA256}")
    return path


@dataclass(frozen=True, slots=True)
class Phenopacket:
    """The parts of a phenopacket the evaluation uses; HPO ids as written in the file."""

    id: str
    disease_id: str | None
    genes: frozenset[str]
    observed: tuple[str, ...]
    excluded: tuple[str, ...]
    pmids: frozenset[str]


def parse(data: dict[str, Any]) -> Phenopacket:
    genes: set[str] = set()
    disease_id = None
    for interpretation in data.get("interpretations", ()):
        diagnosis = interpretation.get("diagnosis", {})
        disease_id = disease_id or diagnosis.get("disease", {}).get("id")
        for genomic in diagnosis.get("genomicInterpretations", ()):
            if genomic.get("interpretationStatus") != "CAUSATIVE":
                continue
            descriptor = genomic.get("variantInterpretation", {}).get("variationDescriptor", {})
            symbol = descriptor.get("geneContext", {}).get("symbol") or genomic.get("gene", {}).get(
                "symbol"
            )
            if symbol:
                genes.add(symbol)
    features = data.get("phenotypicFeatures", ())
    references = data.get("metaData", {}).get("externalReferences", ())
    return Phenopacket(
        id=data["id"],
        disease_id=disease_id,
        genes=frozenset(genes),
        observed=tuple(f["type"]["id"] for f in features if not f.get("excluded")),
        excluded=tuple(f["type"]["id"] for f in features if f.get("excluded")),
        pmids=frozenset(r["id"] for r in references if r.get("id", "").startswith("PMID:")),
    )


def read_archive(path: Path) -> Iterator[Phenopacket]:
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith(".json"):
                yield parse(json.loads(archive.read(name)))


def phenotypic_terms(kb: KnowledgeBase, hpo_ids: Iterable[str]) -> tuple[str, ...]:
    """Current ids of the phenotypic-abnormality terms among ``hpo_ids``, sorted and unique."""
    resolved = {r for hpo_id in hpo_ids if (r := kb.resolve(hpo_id)) is not None}
    return tuple(sorted(t for t in resolved if kb.is_phenotypic_abnormality(t)))


def _related(kb: KnowledgeBase, first: str, second: str) -> bool:
    return first == second or first in kb.ancestors(second) or second in kb.ancestors(first)


@dataclass(frozen=True, slots=True)
class RealCase:
    """A phenopacket as a ``Case``, with the full record the answers come from."""

    case: Case
    observed: tuple[str, ...]
    excluded: tuple[str, ...]
    pmids: frozenset[str]


def to_case(kb: KnowledgeBase, packet: Phenopacket, gene: str, seed: int = SEED) -> RealCase:
    observed = phenotypic_terms(kb, packet.observed)
    excluded = tuple(t for t in phenotypic_terms(kb, packet.excluded) if t not in observed)
    rng = random.Random(stable_seed(seed, "phenopacket", packet.id))
    uniform = [Annotation(hpo_id=t, negated=False, frequency=None, onset=None) for t in observed]
    present = sample_present(kb, uniform, rng, rng.randint(*PRESENT_RANGE))
    candidates = [e for e in excluded if not any(_related(kb, e, p) for p in present)]
    absent = (rng.choice(candidates),) if candidates else ()
    disease_id = packet.disease_id or ""
    return RealCase(
        case=Case(
            case_id=packet.id,
            split="phenopacket",
            source="phenopacket-store",
            disease_id=disease_id,
            disease_name=kb.disease_name(disease_id),
            gene=gene,
            present=present,
            absent=absent,
        ),
        observed=observed,
        excluded=excluded,
        pmids=packet.pmids,
    )


@dataclass(slots=True)
class Selection:
    """Eligible cases grouped by gene, and why every other phenopacket was left out."""

    by_gene: dict[str, list[RealCase]]
    excluded: Counter[str] = field(default_factory=Counter)
    total: int = 0

    def per_gene(self, cap: int, seed: int = SEED) -> list[RealCase]:
        """At most ``cap`` cases per gene, the same seeded draw for every cap (cap 1 ⊂ cap 5)."""
        chosen: list[RealCase] = []
        for gene in sorted(self.by_gene):
            cases = sorted(self.by_gene[gene], key=lambda c: c.case.case_id)
            random.Random(stable_seed(seed, "gene", gene)).shuffle(cases)
            chosen.extend(cases[:cap])
        return chosen


def select(
    kb: KnowledgeBase,
    packets: Iterable[Phenopacket],
    model_genes: frozenset[str],
    model: ModelClient,
    seed: int = SEED,
) -> Selection:
    selection = Selection(by_gene=defaultdict(list))
    for packet in packets:
        selection.total += 1
        reason = _ineligible(kb, packet, model_genes)
        if reason is None:
            (gene,) = packet.genes
            real = to_case(kb, packet, gene, seed)
            if len(real.case.present) < PRESENT_RANGE[0]:
                selection.excluded["fewer_unrelated_features"] += 1
                continue
            try:
                model.rank(sorted(real.case.present), TOP_K)
            except NoKnownHpoIdsError:
                reason = "model_knows_no_reported_term"
            else:
                selection.by_gene[gene].append(real)
                continue
        selection.excluded[reason] += 1
    return selection


def _ineligible(kb: KnowledgeBase, packet: Phenopacket, model_genes: frozenset[str]) -> str | None:
    if not packet.genes:
        return "no_causative_gene"
    if len(packet.genes) > 1:
        return "several_causative_genes"
    if next(iter(packet.genes)) not in model_genes:
        return "gene_not_in_model_vocabulary"
    if len(phenotypic_terms(kb, packet.observed)) < MIN_OBSERVED:
        return "fewer_observed_features"
    return None


class RecordedPatient:
    """Answers from a real record. ``unrecorded`` is the answer when the record is silent."""

    def __init__(
        self,
        kb: KnowledgeBase,
        observed: Sequence[str],
        excluded: Sequence[str],
        unrecorded: AnswerValue = AnswerValue.UNKNOWN,
    ) -> None:
        self._kb = kb
        self._implied = frozenset(t for o in observed for t in (o, *kb.ancestors(o)))
        self._excluded = frozenset(excluded)
        self._unrecorded = unrecorded

    def answer(self, hpo_id: str) -> AnswerValue:
        if hpo_id in self._implied:
            return AnswerValue.YES
        if hpo_id in self._excluded or not self._excluded.isdisjoint(self._kb.ancestors(hpo_id)):
            return AnswerValue.NO
        return self._unrecorded
