"""Structured inputs of the three LLM tasks.

Prompts are rendered from these, and the template provider answers from them
directly, so both paths see exactly the same case data.
"""

from __future__ import annotations

from dataclasses import dataclass

from diagnosticar_preguntas.domain import AnswerValue, CandidateTerm, GeneScore, Mode


@dataclass(frozen=True, slots=True)
class LabeledTerm:
    hpo_id: str
    label: str


@dataclass(frozen=True, slots=True)
class QuestionTask:
    """Choose ``n_questions`` terms from ``candidates`` and phrase them.

    ``corrections`` lists guardrail violations of a previous attempt.
    """

    mode: Mode
    round: int
    n_questions: int
    candidates: tuple[CandidateTerm, ...]
    top_genes: tuple[GeneScore, ...]
    present: tuple[LabeledTerm, ...]
    absent: tuple[LabeledTerm, ...]
    unknown: tuple[LabeledTerm, ...]
    corrections: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NoteItem:
    hpo_id: str
    label: str
    answer: AnswerValue
    note: str


@dataclass(frozen=True, slots=True)
class NotesTask:
    mode: Mode
    items: tuple[NoteItem, ...]


@dataclass(frozen=True, slots=True)
class AnsweredTerm:
    hpo_id: str
    label: str
    answer: AnswerValue
    note_summary: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryTask:
    """Close the session. ``top_genes`` carry the posterior score and the model's rank."""

    mode: Mode
    finish_reason: str
    answers: tuple[AnsweredTerm, ...]
    top_genes: tuple[GeneScore, ...]
    specialties: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()


Task = QuestionTask | NotesTask | SummaryTask
