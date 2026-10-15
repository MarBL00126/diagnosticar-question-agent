"""Simulated patients sampled from the knowledge base.

A case is a disease with a single associated gene the diagnostic.ar model can
rank, plus the findings a family would report before any question:

* 3 to 6 present terms, drawn without replacement with weights equal to the
  annotation frequency (``missing_frequency`` when the source states none)
  and never an ancestor or descendant of a term already drawn;
* with probability ``p_negated`` one negated term: a finding annotated to some
  other disease that the true disease does not support at all.

Diseases linked to several genes are skipped, since then no single gene is the
right answer. Sampling is stratified by source (OMIM, ORPHA), one gene appears
in at most one case, and the list is split into a tuning set and a held-out
test set. ``python -m eval.cases`` writes ``eval/data/cases.json``.
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from eval.harness import CASES_PATH, open_kb, real_model, stable_seed

from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase

SEED = 20260926
MIN_POSITIVE_ANNOTATIONS = 8
SOURCES = ("OMIM", "ORPHA")
TUNING_PER_SOURCE = 100
TEST_PER_SOURCE = 150
MISSING_FREQUENCY = 0.5
P_NEGATED = 0.5
VOCAB_TOP_K = 5229


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    split: str
    source: str
    disease_id: str
    disease_name: str | None
    gene: str
    present: tuple[str, ...]
    absent: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Case:
        return cls(**{**data, "present": tuple(data["present"]), "absent": tuple(data["absent"])})


def positive_annotations(kb: KnowledgeBase, disease_id: str) -> list[Annotation]:
    """Non-negated phenotypic annotations with a non-zero (or unstated) frequency."""
    return [
        a
        for a in kb.annotations(disease_id)
        if not a.negated
        and (a.frequency is None or a.frequency > 0)
        and kb.is_phenotypic_abnormality(a.hpo_id)
    ]


def eligible_diseases(kb: KnowledgeBase, model_genes: frozenset[str]) -> dict[str, list[str]]:
    """Eligible disease ids by source, sorted for reproducibility."""
    diseases: set[str] = set()
    for gene in kb.genes():
        diseases.update(kb.diseases_for_gene(gene))
    by_source: dict[str, list[str]] = {source: [] for source in SOURCES}
    for disease_id in sorted(diseases):
        source = disease_id.split(":", 1)[0]
        genes = kb.genes_for_disease(disease_id)
        if source not in by_source or len(genes) != 1 or genes[0] not in model_genes:
            continue
        distinct = {a.hpo_id for a in positive_annotations(kb, disease_id)}
        if len(distinct) >= MIN_POSITIVE_ANNOTATIONS:
            by_source[source].append(disease_id)
    return by_source


def _related(kb: KnowledgeBase, first: str, second: str) -> bool:
    return first == second or first in kb.ancestors(second) or second in kb.ancestors(first)


def sample_present(
    kb: KnowledgeBase, annotations: Sequence[Annotation], rng: random.Random, n: int
) -> tuple[str, ...]:
    """Weighted draw without replacement of ``n`` mutually unrelated terms."""
    weights: dict[str, float] = {}
    for a in annotations:
        frequency = MISSING_FREQUENCY if a.frequency is None else a.frequency
        weights[a.hpo_id] = max(weights.get(a.hpo_id, 0.0), frequency)
    pool = sorted(weights)
    chosen: list[str] = []
    while pool and len(chosen) < n:
        pick = rng.choices(pool, weights=[weights[t] for t in pool], k=1)[0]
        chosen.append(pick)
        pool = [t for t in pool if not _related(kb, t, pick)]
    return tuple(sorted(chosen))


def supported_terms(kb: KnowledgeBase, disease_id: str) -> frozenset[str]:
    """Terms annotated to the disease, their ancestors, and anything excluded by a NOT."""
    supported: set[str] = set()
    for a in kb.annotations(disease_id):
        supported.add(a.hpo_id)
        supported |= kb.ancestors(a.hpo_id)
        if a.negated:
            supported |= kb.descendants(a.hpo_id)
    return frozenset(supported)


def sample_negated(
    kb: KnowledgeBase,
    disease_id: str,
    present: Iterable[str],
    candidates: Sequence[str],
    rng: random.Random,
) -> tuple[str, ...]:
    if rng.random() >= P_NEGATED:
        return ()
    blocked = supported_terms(kb, disease_id)
    present = tuple(present)
    for _ in range(100):
        term = rng.choice(candidates)
        if term not in blocked and not any(_related(kb, term, p) for p in present):
            return (term,)
    return ()


def annotated_phenotypes(kb: KnowledgeBase, diseases: Iterable[str]) -> list[str]:
    """Every phenotypic term with a positive annotation somewhere, a realistic negation pool."""
    terms = {a.hpo_id for d in diseases for a in positive_annotations(kb, d)}
    return sorted(terms)


def model_vocabulary() -> frozenset[str]:
    """Genes the model can output: all of them come back for ``top_k`` = vocabulary size."""
    ranking = real_model().rank(["HP:0001250"], VOCAB_TOP_K)
    return frozenset(ranking.scores)


def generate(kb: KnowledgeBase, model_genes: frozenset[str], seed: int = SEED) -> list[Case]:
    by_source = eligible_diseases(kb, model_genes)
    negation_pool = annotated_phenotypes(kb, (d for ds in by_source.values() for d in ds))
    rng = random.Random(seed)
    cases: list[Case] = []
    used_genes: set[str] = set()
    for source in SOURCES:
        order = list(by_source[source])
        rng.shuffle(order)
        picked: list[str] = []
        for disease_id in order:
            gene = kb.genes_for_disease(disease_id)[0]
            if gene in used_genes:
                continue
            used_genes.add(gene)
            picked.append(disease_id)
            if len(picked) == TUNING_PER_SOURCE + TEST_PER_SOURCE:
                break
        for index, disease_id in enumerate(picked):
            split = "tuning" if index < TUNING_PER_SOURCE else "test"
            case_rng = random.Random(stable_seed(seed, disease_id))
            present = sample_present(
                kb, positive_annotations(kb, disease_id), case_rng, case_rng.randint(3, 6)
            )
            absent = sample_negated(kb, disease_id, present, negation_pool, case_rng)
            cases.append(
                Case(
                    case_id=f"{split}-{source.lower()}-{index:03d}",
                    split=split,
                    source=source,
                    disease_id=disease_id,
                    disease_name=kb.disease_name(disease_id),
                    gene=kb.genes_for_disease(disease_id)[0],
                    present=present,
                    absent=absent,
                )
            )
    return cases


def save(cases: Sequence[Case], meta: dict[str, Any], path: Path = CASES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "cases": [c.to_dict() for c in cases]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load(split: str | None = None, path: Path = CASES_PATH) -> list[Case]:
    cases = [Case.from_dict(c) for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]
    return [c for c in cases if split is None or c.split == split]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=CASES_PATH)
    args = parser.parse_args()

    kb = open_kb()
    model_genes = model_vocabulary()
    by_source = eligible_diseases(kb, model_genes)
    cases = generate(kb, model_genes, args.seed)
    meta = {
        "seed": args.seed,
        "kb_version": kb.version,
        "model_vocabulary_genes": len(model_genes),
        "kb_genes": sum(1 for _ in kb.genes()),
        "kb_genes_in_model_vocabulary": len(set(kb.genes()) & model_genes),
        "eligible_diseases": {s: len(ds) for s, ds in by_source.items()},
        "rules": {
            "min_positive_annotations": MIN_POSITIVE_ANNOTATIONS,
            "single_gene_diseases_only": True,
            "gene_in_model_vocabulary": True,
            "one_case_per_gene": True,
            "present_terms": "3-6, weighted by frequency, missing frequency 0.5",
            "negated_terms": f"one with probability {P_NEGATED}, unsupported by the disease",
        },
    }
    save(cases, meta, args.out)
    print(f"{len(cases)} cases -> {args.out}")
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
