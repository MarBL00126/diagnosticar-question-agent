from __future__ import annotations

import pytest

from diagnosticar_preguntas.domain import NO_CASE, Evidence, PatientCase
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodConfig, LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import AnswerWeighting, BayesianReranker, RerankConfig
from diagnosticar_preguntas.ranking.strategy import RerankStrategy, model_query
from fixtures.fake_model import FakeModelClient
from fixtures.tiny_kb import GENE_A, GENE_B, GENE_C, HYPOTONIA, MICROCEPHALY, SEIZURE

REPORTED = [MICROCEPHALY]
AFTER_ANSWERS = Evidence(present=frozenset({MICROCEPHALY, SEIZURE}), absent=frozenset({HYPOTONIA}))


def test_model_sees_present_strategy_sends_every_present_term() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query.hpo_ids == (MICROCEPHALY, SEIZURE)


def test_fixed_prior_strategy_sends_only_the_reported_terms() -> None:
    query = model_query(
        RerankStrategy.FIXED_PRIOR, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query.hpo_ids == (MICROCEPHALY,)


def test_model_sees_present_returns_the_effective_no_answers_as_excluded_terms() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT,
        reported_present=REPORTED,
        evidence=AFTER_ANSWERS,
        answered_no=[HYPOTONIA],
    )

    assert query.excluded_hpo_ids == (HYPOTONIA,)


def test_excluded_terms_default_to_none() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query.excluded_hpo_ids == ()


def test_a_term_that_is_also_present_is_not_excluded() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT,
        reported_present=REPORTED,
        evidence=AFTER_ANSWERS,
        answered_no=[SEIZURE, HYPOTONIA],
    )

    assert query.excluded_hpo_ids == (HYPOTONIA,)


def test_fixed_prior_never_excludes_anything() -> None:
    query = model_query(
        RerankStrategy.FIXED_PRIOR,
        reported_present=REPORTED,
        evidence=AFTER_ANSWERS,
        answered_no=[HYPOTONIA],
    )

    assert query.excluded_hpo_ids == ()


@pytest.mark.parametrize("strategy", list(RerankStrategy))
def test_every_strategy_carries_the_case(strategy: RerankStrategy) -> None:
    case = PatientCase(sex="male", age_years=4.0)

    query = model_query(strategy, reported_present=REPORTED, evidence=AFTER_ANSWERS, case=case)

    assert query.case == case


def test_the_case_defaults_to_unknown() -> None:
    query = model_query(
        RerankStrategy.MODEL_SEES_PRESENT, reported_present=REPORTED, evidence=AFTER_ANSWERS
    )

    assert query.case == NO_CASE


def _scores(query: tuple[str, ...]) -> dict[str, float]:
    boost = 3.0 if SEIZURE in query else 1.0
    return {GENE_A: 0.2 * boost, GENE_B: 0.4, GENE_C: 0.4}


def _score_of_gene_a(
    kb: KnowledgeBase,
    strategy: RerankStrategy,
    weighting: AnswerWeighting = AnswerWeighting.CALIBRATED,
) -> tuple[float, FakeModelClient]:
    client = FakeModelClient(_scores)
    likelihood = LikelihoodModel(kb, LikelihoodConfig(background=0.05, missing_frequency=0.5))
    reranker = BayesianReranker(
        kb, likelihood, RerankConfig(temperature=1.0, answer_weighting=weighting)
    )
    evidence = Evidence(present=frozenset({MICROCEPHALY, SEIZURE}))
    query = model_query(strategy, reported_present=REPORTED, evidence=evidence)

    posterior = reranker.rerank(client.rank(query.hpo_ids, 50), evidence)

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


def test_asymmetric_model_sees_present_counts_a_yes_only_through_the_model(
    tiny_kb: KnowledgeBase,
) -> None:
    score, _ = _score_of_gene_a(
        tiny_kb, RerankStrategy.MODEL_SEES_PRESENT, AnswerWeighting.ASYMMETRIC
    )

    # The model saw the "sí"; only GENE_B's exclusion of seizures still applies.
    assert score == pytest.approx(0.6 / (0.6 + 0.4 * 0.02 + 0.4))


def test_asymmetric_fixed_prior_rewards_a_yes_once(tiny_kb: KnowledgeBase) -> None:
    score, _ = _score_of_gene_a(tiny_kb, RerankStrategy.FIXED_PRIOR, AnswerWeighting.ASYMMETRIC)

    gene_a, gene_b, gene_c = 0.2 * (1 + 0.3 * 1.0), 0.4 * 0.02, 0.4 * (1 + 0.3 * 0.8 + 1) / 2
    assert score == pytest.approx(gene_a / (gene_a + gene_b + gene_c))
