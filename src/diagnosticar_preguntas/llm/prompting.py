"""Render the three tasks into prompts.

The system prompt depends only on the mode and the task kind, so it is stable
across requests and cacheable. Every piece of case data goes in the user
message, as JSON inside a ``<datos>`` block the rules tell the model to treat
as data.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.provider import LLMRequest
from diagnosticar_preguntas.llm.schemas import NotesDraft, RoundDraft, SummaryDraft
from diagnosticar_preguntas.llm.tasks import (
    LabeledTerm,
    NotesTask,
    QuestionTask,
    SummaryTask,
)

# Clinical rules carried over from Mariano Brizuela's original prototype of the
# question agent, keyed by where each one lives in the prompts.
RULES_FROM_PROTOTYPE: dict[str, str] = {
    "comun.md#1": "No evidence outside the given data.",
    "comun.md#2": "A denied or negated finding never supports a gene.",
    "comun.md#3": "A finding shared by several candidates motivates a discriminating question.",
    "comun.md#4": "Certainty may fall between rounds.",
    "comun.md#5": "One symptom per question, never 'X o Y'.",
    "comun.md#6": "Never repeat or paraphrase an asked question.",
    "comun.md#7": "Never output a diagnosis; everything is to discuss with a doctor.",
    "paciente.md": "Plain language for patients.",
}

_TASK_PROMPT = {
    QuestionTask: "tarea_preguntas",
    NotesTask: "tarea_notas",
    SummaryTask: "tarea_resumen",
}


@cache
def _load(name: str) -> str:
    return (
        resources.files("diagnosticar_preguntas.llm")
        .joinpath(f"prompts/{name}.md")
        .read_text("utf-8")
        .strip()
    )


def system_prompt(mode: Mode, task: type[QuestionTask | NotesTask | SummaryTask]) -> str:
    return "\n\n".join((_load("comun"), _load(mode.value), _load(_TASK_PROMPT[task])))


def _data_block(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    # Angle brackets only occur inside JSON strings, so escaping them keeps the
    # JSON valid while making it impossible for a note to close the block.
    text = text.replace("<", "\\u003c").replace(">", "\\u003e")
    return f"<datos>\n{text}\n</datos>"


def _labels(terms: tuple[LabeledTerm, ...]) -> list[str]:
    return [t.label for t in terms]


def question_request(task: QuestionTask) -> LLMRequest[RoundDraft]:
    payload = {
        "modo": task.mode.value,
        "ronda": task.round,
        "cantidad_de_preguntas": task.n_questions,
        "genes_candidatos": [
            {"gen": g.gene_symbol, "puntaje": round(g.score, 3)} for g in task.top_genes
        ],
        "sintomas_presentes": _labels(task.present),
        "sintomas_ausentes": _labels(task.absent),
        "sintomas_sin_respuesta": _labels(task.unknown),
        "candidatos": [
            {
                "hpo_id": c.hpo_id,
                "etiqueta": c.label_es,
                "etiqueta_en": c.label_en,
                "informacion": round(c.information_gain, 3),
                "p_si": round(c.p_yes, 2),
                "suben_con_si": list(c.genes_yes),
                "suben_con_no": list(c.genes_no),
                "requiere_estudio": c.requires_testing,
            }
            for c in task.candidates
        ],
        "correcciones": list(task.corrections),
    }
    return LLMRequest(
        system=system_prompt(task.mode, QuestionTask),
        user=_data_block(payload),
        output=RoundDraft,
        task=task,
    )


def notes_request(task: NotesTask) -> LLMRequest[NotesDraft]:
    payload = {
        "respuestas": [
            {"hpo_id": i.hpo_id, "sintoma": i.label, "boton": i.answer.value, "nota": i.note}
            for i in task.items
        ]
    }
    return LLMRequest(
        system=system_prompt(task.mode, NotesTask),
        user=_data_block(payload),
        output=NotesDraft,
        task=task,
    )


def summary_request(task: SummaryTask) -> LLMRequest[SummaryDraft]:
    payload = {
        "modo": task.mode.value,
        "motivo_de_cierre": task.finish_reason,
        "respuestas": [
            {"sintoma": a.label, "respuesta": a.answer.value, "nota": a.note_summary}
            for a in task.answers
        ],
        "genes": [
            {
                "gen": g.gene_symbol,
                "puesto_actual": position,
                "puesto_del_modelo": g.model_rank,
                "puntaje": round(g.score, 3),
            }
            for position, g in enumerate(task.top_genes, start=1)
        ],
        "correcciones": list(task.corrections),
    }
    return LLMRequest(
        system=system_prompt(task.mode, SummaryTask),
        user=_data_block(payload),
        output=SummaryDraft,
        task=task,
    )
