"""The evaluation harness: stop replay, metrics, bootstrap, caching, answerer and baselines."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from eval.answerer import AnswererConfig, SimulatedPatient
from eval.cases import Case, sample_present
from eval.harness import (
    CALIBRATED_DEFAULTS,
    AgentConfig,
    CachedModelClient,
    build_pipeline,
    settings_for,
    stable_seed,
)
from eval.metrics import NO_STOPS, StopRules, apply_stops, paired_bootstrap, summarize
from eval.policies import CaseTrace, MostFrequentSelector, RandomAskableSelector, RoundTrace
from tests.fixtures.tiny_kb import (
    BLINDNESS,
    DISEASE_A,
    DISEASE_B,
    FOCAL_SEIZURE,
    GENE_A,
    GENE_B,
    GENE_C,
    HYPOTONIA,
    MICROCEPHALY,
    NERVOUS_PHYSIOLOGY,
    SEIZURE,
    VISUAL_IMPAIRMENT,
)

from diagnosticar_preguntas.domain import AnswerValue, Evidence, ModelRanking, Posterior
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.model_client import NoKnownHpoIdsError
from diagnosticar_preguntas.ranking.reranker import AnswerWeighting, BayesianReranker
from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from diagnosticar_preguntas.selection.selector import InformationGainSelector, SelectionConfig


def _trace(
    ranks: Sequence[int | None], scores: Sequence[float], gains: Sequence[float | None]
) -> CaseTrace:
    rounds = [
        RoundTrace(
            round=i + 1,
            true_rank=rank,
            top_score=score,
            max_gain=gain,
            finish_reason="max_rounds" if i == 3 else None,
            asked=["HP:1", "HP:2", "HP:3"] if i < 3 else [],
        )
        for i, (rank, score, gain) in enumerate(zip(ranks, scores, gains, strict=True))
    ]
    return CaseTrace("c1", "agent", rounds)


def test_replay_without_stops_keeps_every_round() -> None:
    trace = _trace([5, 3, 2, 1], [0.3, 0.5, 0.7, 0.95], [0.4, 0.3, 0.2, None])

    trajectory = apply_stops(trace, NO_STOPS, uses_gain=True)

    assert trajectory.ranks == (5, 3, 2, 1)
    assert trajectory.questions == (0, 3, 6, 9)
    assert trajectory.stop_reason == "max_rounds"


def test_a_confident_round_freezes_the_ranking() -> None:
    trace = _trace([5, 3, 2, 1], [0.3, 0.85, 0.9, 0.95], [0.4, 0.3, 0.2, None])

    trajectory = apply_stops(trace, StopRules(0.8, 0.0), uses_gain=True)

    assert trajectory.ranks == (5, 3, 3, 3)
    assert trajectory.questions == (0, 3, 3, 3)
    assert trajectory.stop_reason == "confident"


def test_the_gain_rule_uses_the_rescaled_gain_and_only_for_the_agent() -> None:
    trace = _trace([5, 3, 2, 1], [0.3, 0.4, 0.5, 0.6], [0.4, 0.06, 0.2, None])
    rules = StopRules(1.0, 0.05, gain_scale=0.5)

    assert apply_stops(trace, rules, uses_gain=True).ranks == (5, 3, 3, 3)
    assert apply_stops(trace, rules, uses_gain=True).stop_reason == "no_useful_questions"
    assert apply_stops(trace, rules, uses_gain=False).ranks == (5, 3, 2, 1)


def test_the_model_only_policy_is_flat() -> None:
    trace = CaseTrace("c1", "none", [RoundTrace(1, 4, 0.5, None, "no_questions")])

    trajectory = apply_stops(trace, NO_STOPS, uses_gain=False)

    assert trajectory.ranks == (4, 4, 4, 4)
    assert trajectory.questions == (0, 0, 0, 0)


def test_summary_counts_hits_and_rank_changes() -> None:
    better = apply_stops(_trace([5, 3, 2, 1], [0.1] * 4, [1.0] * 4), NO_STOPS, uses_gain=True)
    lost = apply_stops(_trace([2, 9, None, None], [0.1] * 4, [1.0] * 4), NO_STOPS, uses_gain=True)

    summary = summarize([better, lost])
    final = summary["by_round"]["3"]  # type: ignore[index]

    assert final["top1"] == 0.5
    assert final["mrr"] == 0.5
    assert final["worse"] == 0.5
    assert final["better"] == 0.5
    assert final["median_rank"] == (1 + 51) / 2


def test_paired_bootstrap_of_a_constant_difference_has_a_degenerate_interval() -> None:
    first = [
        apply_stops(_trace([1] * 4, [0.1] * 4, [1.0] * 4), NO_STOPS, uses_gain=True)
        for _ in range(3)
    ]
    second = [
        apply_stops(_trace([2] * 4, [0.1] * 4, [1.0] * 4), NO_STOPS, uses_gain=True)
        for _ in range(3)
    ]

    result = paired_bootstrap(first, second, lambda t: 1.0 / (t.ranks[-1] or 1), resamples=50)

    assert result["delta"] == pytest.approx(0.5)
    assert result["low"] == pytest.approx(0.5)
    assert result["high"] == pytest.approx(0.5)


class _CountingModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        self.calls.append(tuple(hpo_ids))
        if "HP:0000000" in hpo_ids:
            raise NoKnownHpoIdsError(hpo_ids)
        return ModelRanking(scores={"G1": 0.7, "G2": 0.3}, unknown_hpo_ids=("HP:0000002",))


def test_the_model_cache_ignores_order_and_keeps_the_query(tmp_path: Path) -> None:
    inner = _CountingModel()
    model = CachedModelClient(inner, cache_dir=tmp_path)

    first = model.rank(["HP:0000002", "HP:0000001"], 50)
    second = model.rank(["HP:0000001", "HP:0000002"], 50)

    assert len(inner.calls) == 1
    assert dict(second.scores) == dict(first.scores)
    assert second.unknown_hpo_ids == ("HP:0000002",)
    assert second.query_hpo_ids == ("HP:0000001", "HP:0000002")
    model.rank(["HP:0000001", "HP:0000002"], 10)
    assert len(inner.calls) == 2


def test_the_model_cache_remembers_unknown_queries(tmp_path: Path) -> None:
    inner = _CountingModel()
    model = CachedModelClient(inner, cache_dir=tmp_path)

    for _ in range(2):
        with pytest.raises(NoKnownHpoIdsError):
            model.rank(["HP:0000000"], 50)

    assert len(inner.calls) == 1


def test_stable_seed_depends_only_on_its_parts() -> None:
    assert stable_seed("a", 1) == stable_seed("a", 1)
    assert stable_seed("a", 1) != stable_seed("a", 2)


EXACT = AnswererConfig(
    name="exact", spurious_yes=0.0, flip=0.0, p_unknown=0.0, p_unknown_testing=0.0
)


def _case(disease: str, gene: str, present: tuple[str, ...]) -> Case:
    return Case("t-000", "test", "OMIM", disease, None, gene, present, ())


def test_the_answerer_follows_the_ontology(tiny_kb: KnowledgeBase) -> None:
    patient = SimulatedPatient(tiny_kb, _case(DISEASE_A, GENE_A, (SEIZURE,)), EXACT)

    assert patient.answer(SEIZURE) is AnswerValue.YES
    assert patient.answer(NERVOUS_PHYSIOLOGY) is AnswerValue.YES
    assert patient.answer(FOCAL_SEIZURE) is AnswerValue.NO
    assert patient.answer(MICROCEPHALY) is AnswerValue.NO


def test_an_excluded_finding_is_never_a_spurious_yes(tiny_kb: KnowledgeBase) -> None:
    always_spurious = AnswererConfig(
        name="spurious", spurious_yes=1.0, flip=0.0, p_unknown=0.0, p_unknown_testing=0.0
    )
    patient = SimulatedPatient(tiny_kb, _case(DISEASE_B, GENE_B, (MICROCEPHALY,)), always_spurious)

    assert patient.answer(SEIZURE) is AnswerValue.NO
    assert patient.answer(FOCAL_SEIZURE) is AnswerValue.NO
    assert patient.answer(BLINDNESS) is AnswerValue.YES


def test_answers_do_not_depend_on_the_order_of_questions(tiny_kb: KnowledgeBase) -> None:
    case = _case(DISEASE_A, GENE_A, (SEIZURE,))
    noisy = AnswererConfig(name="noisy", flip=0.3, p_unknown=0.3)
    terms = [HYPOTONIA, BLINDNESS, MICROCEPHALY, VISUAL_IMPAIRMENT]

    forward = [SimulatedPatient(tiny_kb, case, noisy).answer(t) for t in terms]
    patient = SimulatedPatient(tiny_kb, case, noisy)
    backward = [patient.answer(t) for t in reversed(terms)]

    assert forward == list(reversed(backward))


def test_present_terms_are_unrelated(tiny_kb: KnowledgeBase) -> None:
    annotations = tiny_kb.annotations("ORPHA:300")

    present = sample_present(tiny_kb, annotations, random.Random(1), 3)

    assert set(present) <= {BLINDNESS, VISUAL_IMPAIRMENT, HYPOTONIA}
    assert not ({BLINDNESS, VISUAL_IMPAIRMENT} <= set(present))


def _posterior_and_selector(
    tiny_kb: KnowledgeBase, p_unknown: float = 0.2
) -> tuple[InformationGainSelector, Evidence, Posterior]:
    likelihood = LikelihoodModel(tiny_kb)
    reranker = BayesianReranker(tiny_kb, likelihood)
    selector = InformationGainSelector(tiny_kb, likelihood, SelectionConfig(p_unknown=p_unknown))
    evidence = Evidence(present=frozenset({HYPOTONIA}))
    ranking = ModelRanking(
        scores={GENE_A: 0.5, GENE_B: 0.3, GENE_C: 0.2}, query_hpo_ids=(HYPOTONIA,)
    )
    return selector, evidence, reranker.rerank(ranking, evidence)


def test_p_unknown_only_rescales_the_gains(tiny_kb: KnowledgeBase) -> None:
    low, evidence, posterior = _posterior_and_selector(tiny_kb, 0.1)
    high, _, _ = _posterior_and_selector(tiny_kb, 0.3)

    first = low.shortlist(posterior, evidence, k=8)
    second = high.shortlist(posterior, evidence, k=8)

    assert first
    assert [c.hpo_id for c in first] == [c.hpo_id for c in second]
    for a, b in zip(first, second, strict=True):
        assert b.information_gain == pytest.approx(a.information_gain * 0.7 / 0.9)


def test_baselines_draw_unrelated_terms_from_the_selector_pool(tiny_kb: KnowledgeBase) -> None:
    selector, evidence, posterior = _posterior_and_selector(tiny_kb)
    likelihood = LikelihoodModel(tiny_kb)
    profiles = [likelihood.profile(h.disease_id) for h in posterior.hypotheses]
    pool = set(selector._candidates(profiles, evidence))

    for baseline in (
        RandomAskableSelector(tiny_kb, selector, seed=3),
        MostFrequentSelector(tiny_kb, selector),
    ):
        picks = [c.hpo_id for c in baseline.shortlist(posterior, evidence, k=3)]
        assert picks
        assert set(picks) <= pool
        assert all(a not in tiny_kb.ancestors(b) for a in picks for b in picks)


def test_most_frequent_asks_the_top_gene_first(tiny_kb: KnowledgeBase) -> None:
    selector, evidence, posterior = _posterior_and_selector(tiny_kb)
    top_gene = posterior.genes[0].gene_symbol
    top_terms = {
        a.hpo_id
        for d in tiny_kb.annotated_diseases_for_gene(top_gene)
        for a in tiny_kb.annotations(d)
        if not a.negated
    }

    picks = MostFrequentSelector(tiny_kb, selector).shortlist(posterior, evidence, k=1)

    assert [c.hpo_id for c in picks] and picks[0].hpo_id in top_terms


TUNED = {
    "strategy": "fixed_prior",
    "temperature": 1.0,
    "background": 0.1,
    "missing_frequency": 0.3,
    "epsilon": 0.02,
    "p_unknown": 0.2,
    "confident_threshold": 0.9,
    "min_information_gain": 0.05,
}


def test_the_code_defaults_send_the_answers_to_the_model_and_keep_the_tuned_rest() -> None:
    assert AgentConfig().to_dict() == {
        **TUNED,
        "strategy": "model_sees_present",
        "answer_weighting": "none",
        "answer_weight": 0.3,
        "exclusion_factor": 0.02,
    }


def test_a_config_saved_before_the_weighting_existed_reads_as_calibrated() -> None:
    config = AgentConfig.from_dict(TUNED)

    assert config == CALIBRATED_DEFAULTS
    assert AgentConfig.from_dict(config.to_dict()) == config
    assert AgentConfig.from_dict(AgentConfig().to_dict()) == AgentConfig()


def test_the_pipeline_reranks_with_the_configured_weighting(tiny_kb: KnowledgeBase) -> None:
    asymmetric = replace(AgentConfig(), answer_weighting=AnswerWeighting.ASYMMETRIC)
    pipeline = build_pipeline(tiny_kb, replace(asymmetric, answer_weight=0.5))

    assert build_pipeline(tiny_kb, AgentConfig()).reranker.config.answer_weighting is (
        AnswerWeighting.NONE
    )
    assert pipeline.reranker.config.answer_weighting is AnswerWeighting.ASYMMETRIC
    assert pipeline.reranker.config.answer_weight == 0.5
    calibrated = build_pipeline(tiny_kb, CALIBRATED_DEFAULTS).reranker.config
    assert calibrated.answer_weighting is AnswerWeighting.CALIBRATED


def test_the_strategy_reaches_the_service_through_its_settings() -> None:
    config = AgentConfig(strategy=RerankStrategy.MODEL_SEES_PRESENT)

    assert (
        settings_for(config, early_stop=True).rerank_strategy is RerankStrategy.MODEL_SEES_PRESENT
    )
