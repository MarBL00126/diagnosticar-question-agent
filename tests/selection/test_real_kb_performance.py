"""Timing of a realistic round on the real knowledge base; skipped when it is not built."""

from __future__ import annotations

import math
import time
from pathlib import Path

import pytest

from diagnosticar_preguntas.domain import Evidence, ModelRanking
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
from diagnosticar_preguntas.selection.selector import InformationGainSelector

REAL_KB = Path(__file__).resolve().parents[2] / "data" / "kb.sqlite"
PRESENT = ("HP:0001250", "HP:0001263", "HP:0000252", "HP:0001252")
BUDGET_SECONDS = 0.2

pytestmark = pytest.mark.skipif(not REAL_KB.is_file(), reason="data/kb.sqlite not built")


def _synthetic_top50(kb: KnowledgeBase, likelihood: LikelihoodModel) -> ModelRanking:
    """Fifty genes whose diseases best explain the present terms, with softmax-like scores."""
    fit: dict[str, float] = {}
    for gene in kb.genes():
        diseases = kb.annotated_diseases_for_gene(gene)
        if diseases:
            fit[gene] = max(
                sum(math.log(likelihood.probability(d, t)) for t in PRESENT) for d in diseases
            )
    top = sorted(fit, key=lambda gene: (-fit[gene], gene))[:50]
    total = sum(math.exp(fit[gene]) for gene in top)
    return ModelRanking(
        scores={gene: math.exp(fit[gene]) / total for gene in top}, query_hpo_ids=PRESENT
    )


def test_a_round_over_the_top_50_genes_fits_the_latency_budget() -> None:
    kb = KnowledgeBase.open(REAL_KB)
    likelihood = LikelihoodModel(kb)
    ranking = _synthetic_top50(kb, LikelihoodModel(kb))
    reranker = BayesianReranker(kb, likelihood)
    selector = InformationGainSelector(kb, likelihood)
    evidence = Evidence(present=frozenset(PRESENT), absent=frozenset({"HP:0000256"}))

    timings = []
    for _ in range(3):
        started = time.perf_counter()
        shortlist = selector.shortlist(reranker.rerank(ranking, evidence), evidence, k=8)
        timings.append(time.perf_counter() - started)

    assert len(shortlist) == 8
    assert min(timings) < BUDGET_SECONDS
