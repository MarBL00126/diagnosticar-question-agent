from __future__ import annotations

import pytest
from round_fakes import request

from diagnosticar_preguntas.agent.evidence import build_evidence, interpret_answers, needs_reading
from diagnosticar_preguntas.api.schemas import AnswerIn, AnswerReading
from diagnosticar_preguntas.domain import AnswerValue
from diagnosticar_preguntas.llm.schemas import NoteReading

YES, NO, UNKNOWN = AnswerValue.YES, AnswerValue.NO, AnswerValue.UNKNOWN


def answer(value: AnswerValue, note: str | None = "nota") -> AnswerIn:
    return AnswerIn(hpo_id="HP:0001738", answer=value, note=note)


def reading(consistent: bool, summary: str | None = None) -> dict[str, NoteReading]:
    return {"HP:0001738": NoteReading(hpo_id="HP:0001738", consistent=consistent, summary=summary)}


@pytest.mark.parametrize("value", [YES, NO])
def test_an_inconsistent_note_downgrades_yes_or_no_to_no_se(value: AnswerValue) -> None:
    [result] = interpret_answers([answer(value)], reading(consistent=False))

    assert result.answer is value
    assert result.effective_answer is UNKNOWN


@pytest.mark.parametrize("value", [YES, NO, UNKNOWN])
def test_a_consistent_note_keeps_the_button(value: AnswerValue) -> None:
    [result] = interpret_answers([answer(value)], reading(consistent=True))

    assert result.effective_answer is value


@pytest.mark.parametrize("value", [YES, NO, UNKNOWN])
@pytest.mark.parametrize("consistent", [True, False])
def test_a_note_never_flips_yes_and_no(value: AnswerValue, consistent: bool) -> None:
    [result] = interpret_answers([answer(value)], reading(consistent))

    assert result.effective_answer in (value, UNKNOWN)


def test_an_answer_without_a_reading_keeps_the_button() -> None:
    [result] = interpret_answers([answer(YES, note=None)], {})

    assert result.effective_answer is YES
    assert result.note_summary is None


def test_the_note_summary_is_trimmed_and_capped() -> None:
    [result] = interpret_answers([answer(YES)], reading(True, "  " + "a" * 500 + "  "))

    assert result.note_summary == "a" * 200


def test_a_blank_note_summary_becomes_none() -> None:
    [result] = interpret_answers([answer(YES)], reading(True, "   "))

    assert result.note_summary is None


def test_evidence_merges_extraction_with_effective_answers() -> None:
    req = request(
        answers=[
            {"hpo_id": "HP:0001738", "answer": "si"},
            {"hpo_id": "HP:0001696", "answer": "no"},
            {"hpo_id": "HP:0012236", "answer": "no_se"},
        ]
    )

    evidence = build_evidence(req, interpret_answers(req.answers, {}))

    assert evidence.present == {"HP:0002205", "HP:0004322", "HP:0001738"}
    assert evidence.absent == {"HP:0001250", "HP:0001696"}
    assert evidence.unknown == {"HP:0012236"}


def test_a_downgraded_answer_is_neither_present_nor_absent() -> None:
    req = request(answers=[{"hpo_id": "HP:0001738", "answer": "si", "note": "no sé"}])

    evidence = build_evidence(req, interpret_answers(req.answers, reading(consistent=False)))

    assert "HP:0001738" in evidence.unknown
    assert "HP:0001738" not in evidence.present | evidence.absent


def echoed(value: AnswerValue, effective: AnswerValue, summary: str | None = "Duda.") -> AnswerIn:
    return AnswerIn(
        hpo_id="HP:0001738",
        answer=value,
        note="creo que no",
        reading=AnswerReading(effective_answer=effective, note_summary=summary),
    )


@pytest.mark.parametrize(("value", "effective"), [(YES, UNKNOWN), (YES, YES), (NO, UNKNOWN)])
def test_an_echoed_reading_is_used_as_sent(value: AnswerValue, effective: AnswerValue) -> None:
    [result] = interpret_answers([echoed(value, effective)], {})

    assert (result.answer, result.effective_answer) == (value, effective)
    assert result.note_summary == "Duda."


def test_an_echoed_reading_wins_over_a_new_reading_of_the_same_note() -> None:
    [result] = interpret_answers([echoed(YES, YES, None)], reading(consistent=False, summary="x"))

    assert result.effective_answer is YES
    assert result.note_summary is None


def test_only_notes_without_a_reading_need_one() -> None:
    assert needs_reading(answer(YES))
    assert not needs_reading(answer(YES, note=None))
    assert not needs_reading(echoed(YES, UNKNOWN))


def test_an_echoed_downgrade_is_unknown_evidence() -> None:
    req = request(
        round=3,
        answers=[
            {
                "hpo_id": "HP:0001738",
                "answer": "si",
                "note": "creo que no",
                "reading": {"effective_answer": "no_se", "note_summary": "Duda."},
            }
        ],
    )

    evidence = build_evidence(req, interpret_answers(req.answers, {}))

    assert "HP:0001738" in evidence.unknown
    assert "HP:0001738" not in evidence.present
