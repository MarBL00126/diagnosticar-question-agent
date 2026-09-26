"""HTTP contract of ``POST /v1/rounds``.

This is the only interface Express depends on. The platform keeps a JSON copy
of a request and a response in ``frontend/mocks/fixtures.json`` (key
``questions``); change both together.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from diagnosticar_preguntas.domain import AnswerValue, Mode

HpoId = Annotated[str, StringConstraints(pattern=r"^HP:\d{7}$")]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]

MAX_ROUNDS = 3
QUESTIONS_PER_ROUND = 3


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerIn(_Strict):
    hpo_id: HpoId
    answer: AnswerValue
    note: Note | None = None


class RoundRequest(_Strict):
    """State of the refinement session, sent in full on every round.

    ``round`` is the 1-based number of the round being requested. Round 1
    carries no answers. A request with ``round > MAX_ROUNDS`` or
    ``finish=True`` returns the final ranking and summary without questions.
    """

    mode: Mode
    round: int = Field(ge=1, le=MAX_ROUNDS + 1)
    present_hpo_ids: list[HpoId] = Field(min_length=1, max_length=100)
    absent_hpo_ids: list[HpoId] = Field(default_factory=list, max_length=100)
    answers: list[AnswerIn] = Field(
        default_factory=list, max_length=MAX_ROUNDS * QUESTIONS_PER_ROUND
    )
    finish: bool = False

    @model_validator(mode="after")
    def _answers_are_unique(self) -> RoundRequest:
        ids = [a.hpo_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("each hpo_id may be answered at most once")
        return self


class QuestionOut(_Strict):
    """One question. ``separates`` and ``rationale`` are only filled in clinician mode.

    ``label`` is the official Spanish HPO label of ``hpo_id`` (the English one
    when the term has no translation), short enough for a chip such as
    "No presenta: Convulsiones" once the question is answered.
    """

    id: str
    hpo_id: HpoId
    label: str
    text: str
    help: str | None = None
    separates: list[str] | None = None
    rationale: str | None = None


class RankedGene(_Strict):
    """Same leading fields as a ``/rank-genes`` result, so the UI can reuse its gene cards."""

    gene_id: str
    gene_symbol: str
    score: float
    model_score: float
    model_rank: int


class InterpretedAnswer(_Strict):
    """How an answer was used. ``effective_answer`` differs from ``answer`` only when
    the note contradicted the button, in which case it is downgraded to ``no_se``."""

    hpo_id: HpoId
    answer: AnswerValue
    effective_answer: AnswerValue
    note_summary: str | None = None


class Versions(_Strict):
    agent: str
    knowledge_base: str
    llm_model: str | None


FinishReason = Literal["max_rounds", "user_finished", "confident", "no_useful_questions"]


class RoundResponse(_Strict):
    round: int
    status: Literal["questions", "finished"]
    finish_reason: FinishReason | None = None
    questions: list[QuestionOut] = Field(default_factory=list)
    ranking: list[RankedGene]
    specialties: dict[str, object] | None = None
    confidence: float | None = None
    interpreted_answers: list[InterpretedAnswer] = Field(default_factory=list)
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    versions: Versions
