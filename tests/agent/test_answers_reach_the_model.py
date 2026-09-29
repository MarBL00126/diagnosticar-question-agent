"""By default the ranking is the model's own, for the reported findings and every "sí"."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
from diagnosticar_preguntas.selection.selector import InformationGainSelector
from diagnosticar_preguntas.wiring import patient_vocabulary, rerank_config
from fixtures.fake_model import FakeModelClient
from fixtures.tiny_kb import (
    BLINDNESS,
    GENE_A,
    GENE_B,
    GENE_C,
    HYPOTONIA,
    MICROCEPHALY,
    SEIZURE,
)
from fixtures.tiny_kb import tiny_kb as tiny_kb

WITH_SEIZURE = {GENE_A: 0.6, GENE_C: 0.3, GENE_B: 0.1}
WITHOUT_SEIZURE = {GENE_B: 0.5, GENE_C: 0.3, GENE_A: 0.2}


def _scores(query: tuple[str, ...]) -> dict[str, float]:
    return WITH_SEIZURE if SEIZURE in query else WITHOUT_SEIZURE


def _run(kb: KnowledgeBase, answers: Sequence[dict[str, Any]], absent: Sequence[str]) -> Any:
    settings = Settings(_env_file=None, llm_provider=LLMProviderName.TEMPLATE)  # type: ignore[call-arg]
    likelihood = LikelihoodModel(kb)
    model = FakeModelClient(_scores)
    service = RoundService(
        model=model,
        reranker=BayesianReranker(kb, likelihood, rerank_config(settings)),
        selector=InformationGainSelector(kb, likelihood),
        labels=kb,
        llm=TemplateProvider(),
        settings=settings,
        kb_version=kb.version,
        patient_vocabulary=patient_vocabulary(kb),
    )
    request = RoundRequest.model_validate(
        {
            "mode": Mode.CLINICIAN,
            "round": 2,
            "present_hpo_ids": [MICROCEPHALY],
            "absent_hpo_ids": list(absent),
            "answers": list(answers),
        }
    )
    return service.run(request), model


def _order(response: RoundResponse) -> list[tuple[str, float, int]]:
    return [(g.gene_symbol, round(g.score, 12), g.model_rank) for g in response.ranking]


def test_the_ranking_is_the_models_order_for_the_reported_findings_and_every_yes(
    tiny_kb: KnowledgeBase,
) -> None:
    answers = [{"hpo_id": SEIZURE, "answer": "si"}, {"hpo_id": HYPOTONIA, "answer": "no"}]

    response, model = _run(tiny_kb, answers, absent=[BLINDNESS])

    assert model.calls[0] == ((MICROCEPHALY, SEIZURE), 50)
    assert _order(response) == [(GENE_A, 0.6, 1), (GENE_C, 0.3, 2), (GENE_B, 0.1, 3)]
    assert [g.model_score for g in response.ranking] == [0.6, 0.3, 0.1]


def test_no_answers_and_negated_findings_leave_the_ranking_untouched(
    tiny_kb: KnowledgeBase,
) -> None:
    yes = [{"hpo_id": SEIZURE, "answer": "si"}]
    with_negatives, _ = _run(tiny_kb, [*yes, {"hpo_id": HYPOTONIA, "answer": "no"}], [BLINDNESS])
    without_negatives, _ = _run(tiny_kb, yes, absent=[])

    assert _order(with_negatives) == _order(without_negatives)


def test_known_and_answered_terms_are_never_asked_again_and_no_moves_nothing(
    tiny_kb: KnowledgeBase,
) -> None:
    answers = [{"hpo_id": HYPOTONIA, "answer": "no"}]

    response, _ = _run(tiny_kb, answers, absent=[BLINDNESS])

    asked = {q.hpo_id for q in response.questions}
    assert asked and asked.isdisjoint({MICROCEPHALY, BLINDNESS, HYPOTONIA})
    for question in response.questions:
        assert question.genes_if_no == []
        assert question.genes_if_yes
        assert question.rationale == f"Típico de {', '.join(question.genes_if_yes)}."
