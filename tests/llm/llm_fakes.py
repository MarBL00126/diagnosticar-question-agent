"""Shared helpers for the provider tests."""

from __future__ import annotations

from diagnosticar_preguntas.domain import CandidateTerm, GeneScore, Mode
from diagnosticar_preguntas.llm.prompting import question_request
from diagnosticar_preguntas.llm.provider import LLMRequest
from diagnosticar_preguntas.llm.schemas import RoundDraft
from diagnosticar_preguntas.llm.tasks import LabeledTerm, QuestionTask

CANDIDATES = (
    CandidateTerm(
        hpo_id="HP:0001738",
        label_es="Insuficiencia pancreática exocrina",
        label_en="Exocrine pancreatic insufficiency",
        information_gain=0.5,
        p_yes=0.4,
        genes_yes=("CFTR",),
        genes_no=("DNAH5",),
    ),
    CandidateTerm(
        hpo_id="HP:0001696",
        label_es="Situs inversus total",
        label_en="Situs inversus totalis",
        information_gain=0.4,
        p_yes=0.3,
        genes_yes=("DNAH5",),
        genes_no=("CFTR",),
    ),
    CandidateTerm(
        hpo_id="HP:0012236",
        label_es="Test del sudor alterado",
        label_en="Elevated sweat chloride",
        information_gain=0.3,
        p_yes=0.3,
        genes_yes=("CFTR", "SCNN1A"),
        requires_testing=True,
    ),
)


def question_task(mode: Mode = Mode.CLINICIAN, n_questions: int = 2) -> QuestionTask:
    return QuestionTask(
        mode=mode,
        round=1,
        n_questions=n_questions,
        candidates=CANDIDATES,
        top_genes=(GeneScore("CFTR", 0.6, 0.4, 1), GeneScore("DNAH5", 0.4, 0.3, 2)),
        present=(LabeledTerm("HP:0002205", "Infecciones respiratorias recurrentes"),),
        absent=(),
        unknown=(),
    )


def round_request(mode: Mode = Mode.CLINICIAN) -> LLMRequest[RoundDraft]:
    return question_request(question_task(mode))


VALID_ROUND = {
    "questions": [
        {
            "hpo_id": "HP:0001738",
            "text": "¿Presenta esteatorrea?",
            "help": None,
            "rationale": "Típico de CFTR.",
        }
    ]
}
