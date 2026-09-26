"""Turn the answers of a round request into effective answers and evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from diagnosticar_preguntas.api.schemas import AnswerIn, InterpretedAnswer, RoundRequest
from diagnosticar_preguntas.domain import AnswerValue, Evidence
from diagnosticar_preguntas.llm.schemas import NoteReading

MAX_NOTE_SUMMARY_CHARS = 200


def interpret_answers(
    answers: Sequence[AnswerIn], readings: Mapping[str, NoteReading]
) -> list[InterpretedAnswer]:
    """Apply note readings to the buttons.

    A note can only downgrade an answer to "no sé", when it contradicts the
    button or expresses doubt. It never turns a "sí" into a "no" or back: the
    button is the only explicit statement the user made.
    """
    interpreted = []
    for answer in answers:
        reading = readings.get(answer.hpo_id)
        effective = answer.answer
        if reading is not None and not reading.consistent:
            effective = AnswerValue.UNKNOWN
        summary = (reading.summary or "").strip() if reading is not None else ""
        interpreted.append(
            InterpretedAnswer(
                hpo_id=answer.hpo_id,
                answer=answer.answer,
                effective_answer=effective,
                note_summary=summary[:MAX_NOTE_SUMMARY_CHARS] or None,
            )
        )
    return interpreted


def build_evidence(request: RoundRequest, interpreted: Sequence[InterpretedAnswer]) -> Evidence:
    """Merge the extracted findings with the effective answers.

    An answer overrides the extraction for the same term, since it is the more
    recent and explicit statement.
    """
    effective = {a.hpo_id: a.effective_answer for a in interpreted}

    def answered(value: AnswerValue) -> set[str]:
        return {hpo_id for hpo_id, v in effective.items() if v is value}

    present = {h for h in request.present_hpo_ids if h not in effective}
    absent = {h for h in request.absent_hpo_ids if h not in effective}
    return Evidence(
        present=frozenset(present | answered(AnswerValue.YES)),
        absent=frozenset(absent | answered(AnswerValue.NO)),
        unknown=frozenset(answered(AnswerValue.UNKNOWN)),
    )
