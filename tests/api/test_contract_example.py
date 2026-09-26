"""The documented example of ``POST /v1/rounds`` must stay valid against the schemas.

The platform copies it into its frontend mocks, so a drift here would ship as
a mock the real service can no longer produce.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase
from fixtures.real_kb import real_kb_path

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "contrato" / "rondas-ejemplo.json"

pytestmark = pytest.mark.skipif(not EXAMPLE.is_file(), reason="docs/ is not in the image")


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return body


@pytest.mark.parametrize("key", ["round1_request", "round2_request"])
def test_example_requests_validate(example: dict[str, Any], key: str) -> None:
    RoundRequest.model_validate(example[key])


@pytest.mark.parametrize("key", ["round1_response", "round1_response_medico", "round2_response"])
def test_example_responses_validate(example: dict[str, Any], key: str) -> None:
    RoundResponse.model_validate(example[key])


def test_example_answers_are_the_questions_of_round_one(example: dict[str, Any]) -> None:
    asked = [q["hpo_id"] for q in example["round1_response"]["questions"]]

    assert [a["hpo_id"] for a in example["round2_request"]["answers"]] == asked


def test_example_shows_an_answer_with_and_one_without_a_reading(example: dict[str, Any]) -> None:
    answers = RoundRequest.model_validate(example["round2_request"]).answers

    assert any(a.note and a.reading for a in answers)
    assert any(a.note and a.reading is None for a in answers)


def test_example_echoed_readings_come_back_unchanged(example: dict[str, Any]) -> None:
    request = RoundRequest.model_validate(example["round2_request"])
    response = RoundResponse.model_validate(example["round2_response"])
    interpreted = {a.hpo_id: a for a in response.interpreted_answers}

    for answer in request.answers:
        if answer.reading is not None:
            echoed = interpreted[answer.hpo_id]
            assert echoed.effective_answer == answer.reading.effective_answer
            assert echoed.note_summary == answer.reading.note_summary


def test_example_responses_are_in_their_requests_mode(example: dict[str, Any]) -> None:
    for round_number in (1, 2):
        request = RoundRequest.model_validate(example[f"round{round_number}_request"])
        response = RoundResponse.model_validate(example[f"round{round_number}_response"])
        assert response.mode is request.mode


def test_example_genes_per_answer_only_in_clinician_mode(example: dict[str, Any]) -> None:
    for key in ("round1_response", "round1_response_medico"):
        response = RoundResponse.model_validate(example[key])
        clinician = response.mode is Mode.CLINICIAN
        for question in response.questions:
            assert (question.genes_if_yes is not None) is clinician
            assert (question.rationale is not None) is clinician


def test_example_specialties_and_confidence_do_not_change_between_rounds(
    example: dict[str, Any],
) -> None:
    first, last = example["round1_response"], example["round2_response"]

    assert (first["specialties"], first["confidence"]) == (last["specialties"], last["confidence"])


@pytest.mark.skipif(not real_kb_path().is_file(), reason="data/kb.sqlite not built")
def test_example_labels_are_the_official_spanish_labels(example: dict[str, Any]) -> None:
    kb = KnowledgeBase.open(real_kb_path())

    for key in ("round1_response", "round1_response_medico"):
        for question in example[key]["questions"]:
            assert question["label"] == kb.label_es(question["hpo_id"])
