"""A whole round with the real re-ranker and the Q2b selector, over the parity world."""

from __future__ import annotations

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.config import Settings
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
from diagnosticar_preguntas.wiring import build_selector, patient_vocabulary
from fixtures.fake_model import FakeModelClient
from fixtures.q2b_scenarios import build_scenarios, build_world

WORLD = build_world()
SCENARIO = build_scenarios(WORLD)[0]


def service(**settings: object) -> RoundService:
    config = Settings(_env_file=None, llm_provider="template", **settings)  # type: ignore[call-arg]
    return RoundService(
        model=FakeModelClient(SCENARIO.scores),
        reranker=BayesianReranker(WORLD.kb, WORLD.likelihood),
        selector=build_selector(config, WORLD.kb, WORLD.likelihood),
        labels=WORLD.kb,
        llm=TemplateProvider(),
        settings=config,
        kb_version="test",
        patient_vocabulary=patient_vocabulary(WORLD.kb),
    )


def run(rounds: RoundService, **fields: object) -> RoundResponse:
    return rounds.run(
        RoundRequest.model_validate(
            {
                "mode": Mode.CLINICIAN,
                "round": 1,
                "present_hpo_ids": [],
                "absent_hpo_ids": [],
                **fields,
            }
        )
    )


def known_terms() -> list[str]:
    return [t for t in WORLD.annotated if int(t[3:]) >= 1000]


def test_a_round_asks_three_questions_and_the_next_never_repeats_them() -> None:
    rounds = service()
    reported = known_terms()[:1]
    first = run(rounds, present_hpo_ids=reported)
    asked = [q.hpo_id for q in first.questions]
    answers = [{"hpo_id": t, "answer": "no"} for t in asked]

    second = run(rounds, round=2, present_hpo_ids=reported, answers=answers)

    assert first.status == "questions"
    assert len(asked) == 3
    assert second.status == "questions"
    assert len(second.questions) == 3
    assert not set(asked) & {q.hpo_id for q in second.questions}


def test_the_early_stop_still_applies() -> None:
    rounds = service(min_information_gain=5.0)

    response = run(rounds, present_hpo_ids=known_terms()[:1])

    assert response.finish_reason == "no_useful_questions"
