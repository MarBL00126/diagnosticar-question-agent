"""Validation rules of the round contract that no single endpoint test covers alone."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from diagnosticar_preguntas.api.schemas import AnswerIn, QuestionOut, RoundRequest

BASE: dict[str, Any] = {
    "mode": "paciente",
    "round": 2,
    "present_hpo_ids": ["HP:0002205"],
    "absent_hpo_ids": ["HP:0001250"],
}


def answer(value: str, effective: str) -> dict[str, Any]:
    return {"hpo_id": "HP:0001738", "answer": value, "reading": {"effective_answer": effective}}


@pytest.mark.parametrize(
    ("value", "effective"),
    [("si", "si"), ("si", "no_se"), ("no", "no"), ("no", "no_se"), ("no_se", "no_se")],
)
def test_a_reading_keeps_the_button_or_downgrades_it(value: str, effective: str) -> None:
    assert AnswerIn.model_validate(answer(value, effective)).reading is not None


@pytest.mark.parametrize(("value", "effective"), [("si", "no"), ("no", "si"), ("no_se", "si")])
def test_a_reading_never_flips_or_upgrades_an_answer(value: str, effective: str) -> None:
    with pytest.raises(ValidationError):
        AnswerIn.model_validate(answer(value, effective))


def test_a_reading_summary_is_capped_like_the_agents_own() -> None:
    long = {"effective_answer": "si", "note_summary": "a" * 201}

    with pytest.raises(ValidationError):
        AnswerIn.model_validate({"hpo_id": "HP:0001738", "answer": "si", "reading": long})


def test_answers_about_unreported_terms_are_valid() -> None:
    body = {**BASE, "answers": [{"hpo_id": "HP:0001738", "answer": "si"}]}

    assert RoundRequest.model_validate(body).answers[0].reading is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"absent_hpo_ids": ["HP:0002205"]},
        {"answers": [{"hpo_id": "HP:0002205", "answer": "no"}]},
        {"answers": [{"hpo_id": "HP:0001250", "answer": "si"}]},
    ],
)
def test_reported_findings_never_overlap_each_other_or_the_answers(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as caught:
        RoundRequest.model_validate({**BASE, **overrides})

    assert "HP:" not in caught.value.errors()[0]["msg"]


def test_a_question_lists_at_most_five_genes_per_answer() -> None:
    fields: dict[str, Any] = {
        "id": "r1-HP:0001738",
        "hpo_id": "HP:0001738",
        "label": "x",
        "text": "¿x?",
    }

    with pytest.raises(ValidationError):
        QuestionOut(**fields, genes_if_yes=["G1", "G2", "G3", "G4", "G5", "G6"])
