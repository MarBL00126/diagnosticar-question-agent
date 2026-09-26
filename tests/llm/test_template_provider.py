from __future__ import annotations

from dataclasses import replace

import pytest
from llm_fakes import CANDIDATES, question_task

from diagnosticar_preguntas.agent.guardrails import (
    PatientVocabulary,
    QuestionRules,
    check_round,
    check_summary,
)
from diagnosticar_preguntas.domain import AnswerValue, GeneScore, Mode
from diagnosticar_preguntas.llm.prompting import notes_request, question_request, summary_request
from diagnosticar_preguntas.llm.provider import LLMError, LLMRequest
from diagnosticar_preguntas.llm.schemas import MAX_QUESTION_CHARS, SummaryDraft
from diagnosticar_preguntas.llm.tasks import AnsweredTerm, NoteItem, NotesTask, SummaryTask
from diagnosticar_preguntas.llm.template import TemplateProvider, phrase_question

PATIENT_TEXT = PatientVocabulary(("CFTR", "DNAH5", "SCNN1A"), dict[str, str]().get).for_diseases(())


def rules(mode: Mode, expected: int) -> QuestionRules:
    return QuestionRules(
        mode=mode,
        shortlist=frozenset(c.hpo_id for c in CANDIDATES),
        already_known=frozenset(),
        expected=expected,
        patient_text=PATIENT_TEXT,
        labels={c.hpo_id: (c.label_es, c.label_en) for c in CANDIDATES},
    )


@pytest.mark.parametrize("mode", list(Mode))
def test_template_questions_pass_the_guardrails(mode: Mode) -> None:
    draft = TemplateProvider().generate(question_request(question_task(mode, n_questions=3)))

    assert check_round(draft, rules(mode, 3)).violations == ()


def test_questions_follow_the_selector_order() -> None:
    draft = TemplateProvider().generate(question_request(question_task(n_questions=2)))

    assert [q.hpo_id for q in draft.questions] == ["HP:0001738", "HP:0001696"]


def test_patient_questions_quote_the_spanish_label_impersonally() -> None:
    draft = phrase_question(CANDIDATES[0], Mode.PATIENT)

    assert draft.text == (
        "¿Algún médico mencionó «Insuficiencia pancreática exocrina» o se notó algo así?"
    )
    assert draft.help == "Si no hay seguridad, respondé «No sé»."


def test_patient_questions_about_studies_offer_no_se() -> None:
    draft = phrase_question(CANDIDATES[2], Mode.PATIENT)

    assert draft.text == "¿Algún médico o estudio mencionó «Test del sudor alterado»?"
    assert draft.help is not None and "No sé" in draft.help


def test_clinician_rationale_names_the_genes_it_separates() -> None:
    draft = phrase_question(CANDIDATES[0], Mode.CLINICIAN)

    assert draft.text == "¿Presenta insuficiencia pancreática exocrina?"
    assert draft.rationale == "Separa CFTR (sí) de DNAH5 (no)."


def test_acronym_labels_keep_their_case() -> None:
    candidate = CANDIDATES[0].__class__(
        hpo_id="HP:0000001", label_es="EEG anormal", label_en="", information_gain=1, p_yes=0.5
    )

    assert phrase_question(candidate, Mode.CLINICIAN).text == "¿Presenta EEG anormal?"


def test_long_labels_are_cut_to_fit_a_question() -> None:
    candidate = CANDIDATES[0].__class__(
        hpo_id="HP:0000001", label_es="a" * 400, label_en="", information_gain=1, p_yes=0.5
    )

    text = phrase_question(candidate, Mode.PATIENT).text

    assert len(text) <= MAX_QUESTION_CHARS
    assert "…»" in text


def test_notes_are_never_interpreted() -> None:
    task = NotesTask(Mode.PATIENT, (NoteItem("HP:0001738", "x", AnswerValue.YES, "creo que no"),))

    draft = TemplateProvider().generate(notes_request(task))

    assert [(r.consistent, r.summary) for r in draft.readings] == [(True, None)]


def summary_task(mode: Mode) -> SummaryTask:
    return SummaryTask(
        mode=mode,
        finish_reason="user_finished",
        answers=(AnsweredTerm("HP:0001738", "Esteatorrea", AnswerValue.YES),),
        top_genes=(GeneScore("CFTR", 0.7, 0.3, 2), GeneScore("DNAH5", 0.3, 0.4, 1)),
    )


def test_patient_summary_passes_the_guardrails() -> None:
    draft = TemplateProvider().generate(summary_request(summary_task(Mode.PATIENT)))

    assert check_summary(draft, Mode.PATIENT, PATIENT_TEXT) == []
    assert "médico" in draft.summary


def test_clinician_summary_reports_answers_and_genes_that_rose() -> None:
    draft = TemplateProvider().generate(summary_request(summary_task(Mode.CLINICIAN)))

    assert "sí: Esteatorrea" in draft.summary
    assert "CFTR (#2→#1)" in draft.summary


def test_a_request_for_the_wrong_output_is_an_error() -> None:
    mismatched = LLMRequest(system="", user="", output=SummaryDraft, task=question_task())

    with pytest.raises(LLMError):
        TemplateProvider().generate(mismatched)


def test_a_long_label_cut_by_the_template_still_passes_the_patient_guardrails() -> None:
    long = CANDIDATES[0].__class__(
        hpo_id="HP:0001738",
        label_es="Síndrome " + "muy largo " * 30,
        label_en="",
        information_gain=1,
        p_yes=0.5,
    )
    draft = TemplateProvider().generate(
        question_request(replace(question_task(Mode.PATIENT, 1), candidates=(long,)))
    )
    long_rules = replace(rules(Mode.PATIENT, 1), labels={long.hpo_id: (long.label_es, "")})

    assert check_round(draft, long_rules).violations == ()


def test_patient_questions_never_address_the_reader_as_the_patient() -> None:
    for candidate in CANDIDATES:
        text = phrase_question(candidate, Mode.PATIENT).text

        assert not any(word in text for word in ("te ", "tuviste", "notaste", "tenés"))
