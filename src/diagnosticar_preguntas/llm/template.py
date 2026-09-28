"""Deterministic provider that needs no LLM.

Used for evaluation, for local runs without credentials, and as the fallback
whenever an LLM answer fails validation twice. It follows the selector's order,
phrases questions from the Spanish HPO labels and never interprets free text.

Patient questions quote the official label as something a doctor may have
said, in the same impersonal form the patient prompt asks of Claude: the
reader may be the patient or a relative. The label is the one text the
patient guardrails exempt in its own question, so these questions pass them
whatever the label says (a label may well contain "síndrome").
"""

from __future__ import annotations

from pydantic import BaseModel

from diagnosticar_preguntas.domain import AnswerValue, CandidateTerm, Mode
from diagnosticar_preguntas.llm.provider import LLMError, LLMRequest
from diagnosticar_preguntas.llm.schemas import (
    MAX_QUESTION_CHARS,
    NoteReading,
    NotesDraft,
    QuestionDraft,
    RoundDraft,
    SummaryDraft,
)
from diagnosticar_preguntas.llm.tasks import NotesTask, QuestionTask, SummaryTask

_MAX_GENES_IN_RATIONALE = 3


class TemplateProvider:
    @property
    def model_name(self) -> str | None:
        return None

    def generate[T: BaseModel](
        self, request: LLMRequest[T], *, timeout_s: float | None = None
    ) -> T:
        task = request.task
        draft: BaseModel
        match task:
            case QuestionTask():
                draft = RoundDraft(
                    questions=[
                        phrase_question(c, task.mode) for c in task.candidates[: task.n_questions]
                    ]
                )
            case NotesTask():
                draft = NotesDraft(
                    readings=[
                        NoteReading(hpo_id=i.hpo_id, consistent=True, summary=None)
                        for i in task.items
                    ]
                )
            case SummaryTask():
                draft = SummaryDraft(summary=summarize(task))
        if not isinstance(draft, request.output):
            raise LLMError(f"{type(task).__name__} cannot produce {request.output.__name__}")
        return draft


def phrase_question(candidate: CandidateTerm, mode: Mode) -> QuestionDraft:
    label = candidate.label_es or candidate.label_en or candidate.hpo_id
    if mode is Mode.PATIENT:
        if candidate.requires_testing:
            text = "¿Algún médico o estudio mencionó «{}»?"
            help_text = "Si no se hizo ese estudio o no se sabe, respondé «No sé»."
        else:
            text = "¿Algún médico mencionó «{}» o se notó algo así?"
            help_text = "Si no hay seguridad, respondé «No sé»."
        rationale = "Pregunta generada sin IA."
    else:
        label = _as_phrase(label)
        text = "¿Algún estudio mostró {}?" if candidate.requires_testing else "¿Presenta {}?"
        help_text = None
        rationale = _rationale(candidate)
    room = MAX_QUESTION_CHARS - len(text.format(""))
    if len(label) > room:
        label = label[: room - 1].rstrip() + "…"
    return QuestionDraft(
        hpo_id=candidate.hpo_id, text=text.format(label), help=help_text, rationale=rationale
    )


def summarize(task: SummaryTask) -> str:
    if task.mode is Mode.PATIENT:
        opening = (
            "Con las respuestas se reordenó la lista de posibles causas genéticas."
            if task.answers
            else "No se sumaron respuestas, así que la lista quedó como la ordenó el modelo."
        )
        return (
            f"{opening} Llevá esta información a la consulta para conversarla con el médico, "
            "que va a decidir qué estudios tienen sentido."
        )
    parts = []
    for value, name in (
        (AnswerValue.YES, "sí"),
        (AnswerValue.NO, "no"),
        (AnswerValue.UNKNOWN, "no sabe"),
    ):
        labels = [a.label for a in task.answers if a.answer is value]
        if labels:
            parts.append(f"{name}: {', '.join(labels)}")
    answers = f"Respuestas — {'; '.join(parts)}." if parts else "Sin respuestas nuevas."
    risers = [
        f"{g.gene_symbol} (#{g.model_rank}→#{position})"
        for position, g in enumerate(task.top_genes, start=1)
        if position < g.model_rank
    ]
    moves = (
        f" Subieron respecto del modelo: {', '.join(risers)}."
        if risers
        else " El orden de los primeros genes no cambió respecto del modelo."
    )
    return f"{answers}{moves} Orientación para estudios dirigidos, no un diagnóstico."


def _as_phrase(label: str) -> str:
    label = label.strip()
    if len(label) > 1 and label[1].isupper():
        return label
    return label[:1].lower() + label[1:]


def _rationale(candidate: CandidateTerm) -> str:
    yes = ", ".join(candidate.genes_yes[:_MAX_GENES_IN_RATIONALE])
    no = ", ".join(candidate.genes_no[:_MAX_GENES_IN_RATIONALE])
    if yes and no:
        return f"Separa {yes} (sí) de {no} (no)."
    if yes or no:
        return f"Favorece a {yes or no} con un {'sí' if yes else 'no'}."
    return "Síntoma con más información disponible."
