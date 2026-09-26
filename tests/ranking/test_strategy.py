from __future__ import annotations

import pytest

from diagnosticar_preguntas.domain import Evidence
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker, RerankConfig
from diagnosticar_preguntas.ranking.strategy import RerankStrategy, model_query
from fixtures.fake_model import FakeModelClient
from fixtures.tiny_kb import GENE_A, GENE_B, GENE_C, HYPOTONIA, MICROCEPHALY, SEIZURE

REPORTED = [MICROCEPHALY]
AFTER_ANSWERS = Evidence(present=frozenset({MICROCEPHALY, SEIZURE}), absent=frozenset({HYPOTONIA}))


def test_model_sees_present_strategy_sends_every_present_term() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query == (MICROCEPHALY, SEIZURE)


def test_fixed_prior_strategy_sends_only_the_reported_terms() -> None:
    query = model_query(
        RerankStrategy.FIXED_PRIOR, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query == (MICROCEPHALY,)


def _scores(query: tuple[str, ...]) -> dict[str, float]:
    boost = 3.0 if SEIZURE in query else 1.0
    return {GENE_A: 0.2 * boost, GENE_B: 0.4, GENE_C: 0.4}


def _score_of_gene_a(kb: KnowledgeBase, strategy: RerankStrategy) -> tuple[float, FakeModelClient]:
    client = FakeModelClient(_scores)
    reranker = BayesianReranker(kb, LikelihoodModel(kb), RerankConfig(temperature=1.0))
    evidence = Evidence(present=frozenset({MICROCEPHALY, SEIZURE}))
    query = model_query(strategy, reported_present=REPORTED, evidence=evidence)

    posterior = reranker.rerank(client.rank(query, 50), evidence)

    return next(g.score for g in posterior.genes if g.gene_symbol == GENE_A), client


def test_model_sees_present_leaves_a_yes_answer_to_the_model(tiny_kb: KnowledgeBase) -> None:
    score, client = _score_of_gene_a(tiny_kb, RerankStrategy.MODEL_SEES_PRESENT)

    assert client.calls == [((MICROCEPHALY, SEIZURE), 50)]
    assert score == pytest.approx(0.6 / 1.4)


def test_fixed_prior_applies_a_yes_answer_through_the_likelihood(tiny_kb: KnowledgeBase) -> None:
    score, client = _score_of_gene_a(tiny_kb, RerankStrategy.FIXED_PRIOR)

    gene_a, gene_b, gene_c = 0.2 * 0.98, 0.4 * 0.02, 0.4 * (0.8 + 0.05) / 2
    assert client.calls == [((MICROCEPHALY,), 50)]
    assert score == pytest.approx(gene_a / (gene_a + gene_b + gene_c))
