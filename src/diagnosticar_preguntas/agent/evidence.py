"""Turn the answers of a round request into effective answers and evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from diagnosticar_preguntas.api.schemas import (
    MAX_NOTE_SUMMARY_CHARS,
    AnswerIn,
    InterpretedAnswer,
    RoundRequest,
)
from diagnosticar_preguntas.domain import AnswerValue, Evidence
from diagnosticar_preguntas.llm.schemas import NoteReading


def needs_reading(answer: AnswerIn) -> bool:
    """Notes are read once: an answer the caller sends back with its ``reading`` is not reread."""
    return answer.note is not None and answer.reading is None


def interpret_answers(
    answers: Sequence[AnswerIn], readings: Mapping[str, NoteReading]
) -> list[InterpretedAnswer]:
    """Apply echoed readings and this round's note readings to the buttons.

    A note can only downgrade an answer to "no sé", when it contradicts the
    button or expresses doubt. It never turns a "sí" into a "no" or back: the
    button is the only explicit statement the user made. ``readings`` are
    only used for answers that ``needs_reading``.
    """
    interpreted = []
    for answer in answers:
        if answer.reading is not None:
            effective = answer.reading.effective_answer
            summary = answer.reading.note_summary or ""
        else:
            reading = readings.get(answer.hpo_id) if needs_reading(answer) else None
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
    """Merge the reported findings with the effective answers.

    The request's validation guarantees that answers never repeat a reported
    finding, so the two sources never disagree about a term.
    """

    def answered(value: AnswerValue) -> frozenset[str]:
        return frozenset(a.hpo_id for a in interpreted if a.effective_answer is value)

    return Evidence(
        present=frozenset(request.present_hpo_ids) | answered(AnswerValue.YES),
        absent=frozenset(request.absent_hpo_ids) | answered(AnswerValue.NO),
        unknown=answered(AnswerValue.UNKNOWN),
        asked=frozenset(a.hpo_id for a in interpreted),
    )
