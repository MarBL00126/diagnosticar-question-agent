"""Structured outputs the LLM must return. Providers validate against these models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MAX_QUESTION_CHARS = 200


class QuestionDraft(_Output):
    hpo_id: str
    text: str
    help: str | None
    rationale: str


class RoundDraft(_Output):
    questions: list[QuestionDraft]


class NoteReading(_Output):
    """``consistent`` is false when the note contradicts the button or expresses doubt."""

    hpo_id: str
    consistent: bool
    summary: str | None


class NotesDraft(_Output):
    readings: list[NoteReading]


class SummaryDraft(_Output):
    summary: str
