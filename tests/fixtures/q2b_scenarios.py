"""Hand-built inputs for the Q2b parity tests: a small ontology, annotations, rankings and answers.

Everything is derived from a seed with ``random.Random.random`` only, so the scenarios
are the same on every Python version. ``expected_picks.json`` freezes what the
EXP-Q-001 harness (``diagnostic_ar_training.eval.questioning.Q2b``, lambda 0.6) picked on them.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

from diagnosticar_preguntas.domain import Evidence, ModelRanking, Posterior
from diagnosticar_preguntas.kb.ontology import PHENOTYPIC_ABNORMALITY, ROOT, depths
from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase, Term
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker

N_ORGANS = 4
MIDS_PER_ORGAN = 2
LEAVES_PER_MID = 4
N_GENES = 16
N_SCENARIOS = 80
FREQUENCIES = (0.9, 0.5, 0.3, 0.1, None)


def _id(number: int) -> str:
    return f"HP:{number:07d}"


def build_parents() -> dict[str, tuple[str, ...]]:
    """root > phenotypic abnormality > organ systems > mid terms > leaves, plus two cross-links."""
    parents: dict[str, tuple[str, ...]] = {ROOT: (), PHENOTYPIC_ABNORMALITY: (ROOT,)}
    number = 1000
    for organ in range(N_ORGANS):
        organ_id = _id(200 + organ)
        parents[organ_id] = (PHENOTYPIC_ABNORMALITY,)
        for _ in range(MIDS_PER_ORGAN):
            mid_id = _id(number)
            number += 1
            parents[mid_id] = (organ_id,)
            for _ in range(LEAVES_PER_MID):
                parents[_id(number)] = (mid_id,)
                number += 1
    leaves = [t for t in parents if int(t[3:]) >= 1000 and t not in _mids(parents)]
    parents[leaves[0]] = (*parents[leaves[0]], _mids(parents)[-1])
    parents[leaves[5]] = (*parents[leaves[5]], _mids(parents)[2])
    return parents


def _mids(parents: Mapping[str, tuple[str, ...]]) -> list[str]:
    organs = {t for t, ps in parents.items() if ps == (PHENOTYPIC_ABNORMALITY,)}
    return sorted(t for t, ps in parents.items() if set(ps) & organs)


def closure(parents: Mapping[str, tuple[str, ...]]) -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}

    def up(term: str) -> frozenset[str]:
        if term not in found:
            ancestors: set[str] = set()
            for parent in parents[term]:
                ancestors |= {parent} | up(parent)
            found[term] = frozenset(ancestors)
        return found[term]

    for term in parents:
        up(term)
    return found


@dataclass(frozen=True)
class World:
    kb: KnowledgeBase
    likelihood: LikelihoodModel
    parents: dict[str, tuple[str, ...]]
    genes: tuple[str, ...]
    annotated: tuple[str, ...]


def build_world() -> World:
    rng = random.Random(7)
    parents = build_parents()
    ancestors = closure(parents)
    depth = depths({t: frozenset(ps) for t, ps in parents.items()})
    terms = {t: Term(t, t, f"es {t}", False, None, depth.get(t), False) for t in parents}
    leaves = sorted(t for t in parents if int(t[3:]) >= 1000 and t not in _mids(parents))
    annotatable = sorted(t for t in parents if int(t[3:]) >= 1000)
    twin_a, twin_b = leaves[8], leaves[9]
    annotations: dict[str, tuple[Annotation, ...]] = {}
    gene_diseases: dict[str, tuple[str, ...]] = {}
    genes = tuple(f"GENE{i:02d}" for i in range(N_GENES))
    for index, gene in enumerate(genes):
        if index >= N_GENES - 2:
            continue
        diseases = []
        for d in range(1 + int(rng.random() * 3)):
            disease = f"D:{index:02d}{d}"
            diseases.append(disease)
            chosen = {annotatable[int(rng.random() * len(annotatable))] for _ in range(6)}
            if rng.random() < 0.5:
                chosen |= {twin_a, twin_b}
            rows = []
            for term in sorted(chosen):
                frequency = FREQUENCIES[int(rng.random() * len(FREQUENCIES))]
                if term in (twin_a, twin_b):
                    frequency = 0.5
                rows.append(Annotation(term, False, frequency, None))
            annotations[disease] = tuple(rows)
        gene_diseases[gene] = tuple(diseases)
    for gene in genes[-2:]:
        gene_diseases[gene] = (f"D:none-{gene}",)
    kb = KnowledgeBase(
        meta={"hpo_version": "test", "kb_schema_version": "test"},
        terms=terms,
        alt_ids={},
        parents={t: frozenset(ps) for t, ps in parents.items()},
        ancestors=ancestors,
        disease_names=dict.fromkeys(annotations),
        annotations=annotations,
        gene_diseases=gene_diseases,
    )
    return World(kb, LikelihoodModel(kb), parents, genes, tuple(annotatable))


@dataclass(frozen=True)
class Scenario:
    name: str
    scores: dict[str, float]
    reported: tuple[str, ...]
    answers: dict[str, str]

    def evidence(self) -> Evidence:
        def answered(value: str) -> frozenset[str]:
            return frozenset(t for t, a in self.answers.items() if a == value)

        return Evidence(
            present=frozenset(self.reported) | answered("yes"),
            absent=answered("no"),
            unknown=answered("unknown"),
            asked=frozenset(self.answers),
        )

    def posterior(self, world: World) -> Posterior:
        reranker = BayesianReranker(world.kb, world.likelihood)
        return reranker.rerank(ModelRanking(scores=self.scores), self.evidence())


def build_scenarios(world: World) -> list[Scenario]:
    rng = random.Random(11)
    scenarios = []
    for n in range(N_SCENARIOS):
        raw = [rng.random() ** 3 + 1e-3 for _ in world.genes]
        total = sum(raw)
        scores = {g: r / total for g, r in zip(world.genes, raw, strict=True)}
        picked: list[str] = []
        for _ in range(int(rng.random() * 3) + int(rng.random() * 6)):
            term = world.annotated[int(rng.random() * len(world.annotated))]
            if term not in picked:
                picked.append(term)
        reported = tuple(sorted(picked[: int(rng.random() * 3)]))
        answers = {
            t: ("yes", "no", "unknown")[int(rng.random() * 3)]
            for t in picked[len(reported) :]
            if t not in reported
        }
        scenarios.append(Scenario(f"s{n:02d}", scores, reported, answers))
    return scenarios
