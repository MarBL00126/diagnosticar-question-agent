"""HTTP contract of ``POST /v1/rounds`` (version 2).

This is the only interface Express depends on. The platform keeps a JSON copy
of a request and a response in ``frontend/mocks/fixtures.json`` (key
``questions``); change both together. The documented example lives in
``docs/contrato/rondas-ejemplo.json``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from diagnosticar_preguntas.domain import AnswerValue, Mode

HpoId = Annotated[str, StringConstraints(pattern=r"^HP:\d{7}$")]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]

MAX_ROUNDS = 3
QUESTIONS_PER_ROUND = 3
MAX_NOTE_SUMMARY_CHARS = 200
MAX_GENES_PER_SIDE = 5

NoteSummary = Annotated[str, StringConstraints(max_length=MAX_NOTE_SUMMARY_CHARS)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnswerReading(_Strict):
    """How the agent read an answer's note in an earlier response, echoed back by the caller.

    Copy it from that response's ``interpreted_answers``. The agent reads each
    note once: an answer that carries a ``reading`` is used as that reading,
    so the effective answer stays the same across rounds and modes.
    """

    effective_answer: AnswerValue
    note_summary: NoteSummary | None = None


class AnswerIn(_Strict):
    """One answered question: the button pressed, an optional note and its earlier reading."""

    hpo_id: HpoId
    answer: AnswerValue
    note: Note | None = None
    reading: AnswerReading | None = None

    @model_validator(mode="after")
    def _reading_only_downgrades(self) -> AnswerIn:
        # The same rule the agent applies to notes: a reading may turn an answer into
        # "no sé", never a "sí" into a "no" or back.
        effective = None if self.reading is None else self.reading.effective_answer
        if effective not in (None, self.answer, AnswerValue.UNKNOWN):
            raise ValueError("reading.effective_answer must equal answer or be no_se")
        return self


class RoundRequest(_Strict):
    """State of the refinement session, sent in full on every round.

    ``round`` is the 1-based number of the round being requested. Round 1
    carries no answers. A request with ``round > MAX_ROUNDS`` or
    ``finish=True`` returns the final ranking and summary without questions.

    ``present_hpo_ids`` and ``absent_hpo_ids`` are the findings reported
    before any question (the extraction step) and must not overlap.
    ``answers`` are only for questions the agent asked, so none of them may
    repeat a reported finding.
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
    def _findings_and_answers_are_consistent(self) -> RoundRequest:
        # Messages never name the offending ids: validation errors reach logs and bodies.
        ids = [a.hpo_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("each hpo_id may be answered at most once")
        present, absent = set(self.present_hpo_ids), set(self.absent_hpo_ids)
        if not present.isdisjoint(absent):
            raise ValueError("a finding cannot be both present and absent")
        if not (present | absent).isdisjoint(ids):
            raise ValueError("answers may only be about asked questions, not reported findings")
        return self


class QuestionOut(_Strict):
    """One question.

    ``label`` is the official Spanish HPO label of ``hpo_id`` (the English one
    when the term has no translation), short enough for a chip such as
    "No presenta: Convulsiones" once the question is answered.

    ``genes_if_yes``, ``genes_if_no`` and ``rationale`` are only filled in
    clinician mode (``null`` for patients). ``genes_if_yes`` are the genes
    associated with the symptom in the knowledge base (at most five, most
    associated first): "genes asociados al síntoma; el modelo puede subirlos
    si la respuesta es sí". ``genes_if_no`` is always an empty list with the
    default answer weighting, where a "no" is recorded and moves nothing; with
    another weighting it holds the genes that rise if the answer is "no".
    ``rationale`` says which candidates the symptom is typical of, never what a
    "no" would do.
    """

    id: str
    hpo_id: HpoId
    label: str
    text: str
    help: str | None = None
    genes_if_yes: list[str] | None = Field(default=None, max_length=MAX_GENES_PER_SIDE)
    genes_if_no: list[str] | None = Field(default=None, max_length=MAX_GENES_PER_SIDE)
    rationale: str | None = None


class RankedGene(_Strict):
    """Same leading fields as a ``/rank-genes`` result, so the UI can reuse its gene cards."""

    gene_id: str
    gene_symbol: str
    score: float
    model_score: float
    model_rank: int


class InterpretedAnswer(_Strict):
    """How an answer was used, for every answer of the request.

    ``effective_answer`` differs from ``answer`` only when the note
    contradicted the button or expressed doubt, in which case it is
    downgraded to ``no_se``. Answers that arrived with a ``reading`` echo it
    unchanged. Send ``effective_answer`` and ``note_summary`` back as the
    answer's ``reading`` in later requests.
    """

    hpo_id: HpoId
    answer: AnswerValue
    effective_answer: AnswerValue
    note_summary: NoteSummary | None = None


class Versions(_Strict):
    """``llm_model`` is ``null`` when every text of the response came from templates."""

    agent: str
    knowledge_base: str
    llm_model: str | None


FinishReason = Literal["max_rounds", "user_finished", "confident", "no_useful_questions"]


class RoundResponse(_Strict):
    """Result of one round.

    ``mode`` is the mode the texts of this response (questions, help,
    summary, note summaries) were written for; it always equals the
    request's ``mode``.

    ``specialties`` and ``confidence`` come unchanged from this round's call
    to the diagnostic.ar model. With the default strategy
    (``model_sees_present``) that call carries the findings reported before
    any question plus every effective "sí", so both change from round to round
    as "sí" answers arrive. With the default answer weighting (``none``)
    ``ranking`` is the model's own order for that call: ``model_rank`` equals
    the position and ``score`` is ``model_score`` renormalised over the 50
    genes. "No" answers and ``absent_hpo_ids`` are recorded
    (``interpreted_answers``) but do not move it. With ``fixed_prior`` the
    model only ever sees ``present_hpo_ids`` and the answers act through the
    re-ranker instead.
    """

    round: int
    mode: Mode
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
