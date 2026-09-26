from __future__ import annotations

import pytest

from diagnosticar_preguntas.agent.guardrails import (
    PatientText,
    PatientVocabulary,
    QuestionRules,
    RoundCheck,
    check_round,
    check_summary,
)
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.schemas import QuestionDraft, RoundDraft, SummaryDraft

SHORTLIST = frozenset({"HP:0001738", "HP:0001696", "HP:0002099", "HP:0000765", "HP:0000100"})
LABELS = {
    "HP:0001738": ("Insuficiencia pancreática exocrina", "Exocrine pancreatic insufficiency"),
    "HP:0000100": ("Síndrome nefrótico", "Nephrotic syndrome"),
}
# Every gene of the fake knowledge base, far beyond the ranked candidates.
ALL_GENES = ("CFTR", "DNAH5", "HLA-B", "SCN1A", "POR", "SON", "IQSEC2", "AB")
DISEASES = {
    "OMIM:219700": "Cystic fibrosis",
    "OMIM:607208": "Epileptic encephalopathy, early infantile, 6 (Dravet syndrome)",
    "OMIM:167800": "Pancreatitis, hereditary",
    "OMIM:256370": "Nephrotic syndrome, type 4",
}
VOCABULARY = PatientVocabulary(ALL_GENES, DISEASES.get)
TOP_DISEASES = VOCABULARY.for_diseases(DISEASES)


def rules(mode: Mode = Mode.CLINICIAN, expected: int = 1) -> QuestionRules:
    return QuestionRules(
        mode=mode,
        shortlist=SHORTLIST,
        already_known=frozenset({"HP:0002205", "HP:0000765"}),
        expected=expected,
        patient_text=TOP_DISEASES,
        labels=LABELS,
    )


def q(
    hpo_id: str = "HP:0001738",
    text: str = "¿Presenta esteatorrea?",
    help: str | None = None,
    rationale: str = "Separa CFTR (sí) de DNAH5 (no).",
) -> QuestionDraft:
    return QuestionDraft(hpo_id=hpo_id, text=text, help=help, rationale=rationale)


def check(
    *questions: QuestionDraft, mode: Mode = Mode.CLINICIAN, expected: int | None = None
) -> RoundCheck:
    return check_round(
        RoundDraft(questions=list(questions)),
        rules(mode, expected if expected is not None else len(questions)),
    )


def patient(text: str, hpo_id: str = "HP:0002099") -> list[str]:
    return list(check(q(hpo_id, text), mode=Mode.PATIENT).violations)


def patient_summary(text: str, exempt: tuple[str, ...] = ()) -> list[str]:
    return check_summary(SummaryDraft(summary=text), Mode.PATIENT, TOP_DISEASES, exempt)


def test_a_valid_clinician_round_passes() -> None:
    result = check(q(), q("HP:0001696", "¿Tiene situs inversus?"))

    assert result.violations == ()
    assert len(result.accepted) == 2


def test_an_id_outside_the_shortlist_is_rejected() -> None:
    result = check(q("HP:0009999"))

    assert result.accepted == ()
    assert "HP:0009999: no está entre los candidatos" in result.violations


def test_a_duplicated_symptom_keeps_only_the_first_question() -> None:
    result = check(q(), q(text="¿Tiene heces grasosas?"))

    assert [x.text for x in result.accepted] == ["¿Presenta esteatorrea?"]
    assert any("repite" in v for v in result.violations)


def test_a_symptom_already_asked_is_rejected() -> None:
    result = check(q("HP:0000765", "¿Tiene el tórax anormal?"))

    assert result.accepted == ()
    assert any("ya se preguntó" in v for v in result.violations)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Presenta esteatorrea",
        "¿Presenta esteatorrea? ¿Y diarrea?",
        "¿" + "a" * 200 + "?",
    ],
)
def test_text_must_be_one_short_question(text: str) -> None:
    assert check(q(text=text)).accepted == ()


def test_the_wrong_number_of_questions_is_a_violation() -> None:
    result = check(q(), expected=3)

    assert len(result.accepted) == 1
    assert "se pidieron 3 preguntas y llegaron 1" in result.violations


def test_extra_valid_questions_beyond_the_expected_count_are_dropped() -> None:
    result = check(q(), q("HP:0001696", "¿Tiene situs inversus?"), expected=1)

    assert len(result.accepted) == 1


def test_clinician_questions_need_a_rationale() -> None:
    result = check(q(rationale="  "))

    assert any("fundamento" in v for v in result.violations)


def test_clinician_text_may_name_genes_and_diseases() -> None:
    assert check(q(text="¿Tiene un estudio previo de CFTR por fibrosis quística?")).violations == ()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("¿Notaste algo que apunte a CFTR?", "nombra genes"),
        ("¿Te hablaron del HLA-B?", "nombra genes"),
        ("¿Hubo una alteración en cftr?", "nombra genes"),
        ("¿Hubo cambios en IqSec2?", "nombra genes"),
        ("¿Se notó algo en el scn1a-positivo?", "nombra genes"),
        ("¿Notaste esto en el 50% de las comidas?", "porcentajes"),
        ("¿Pasa en más del 50 por ciento de los días?", "porcentajes"),
        ("¿Hay un 80\uff05 de chance de que pase?", "porcentajes"),
        ("¿Te dieron un diagnóstico de fibrosis?", "palabras no permitidas"),
        ("¿Te hablaron de un gen alterado?", "palabras no permitidas"),
        ("¿Conocés la probabilidad de tenerlo?", "palabras no permitidas"),
        ("¿Te dijeron que es un sindrome?", "palabras no permitidas"),
        ("¿Es probable que haya pasado antes?", "palabras no permitidas"),
        ("¿Te hablaron de algún riesgo?", "palabras no permitidas"),
        ("¿Pasa en uno de cada 4 días?", "palabras no permitidas"),
        ("¿Te dijeron que tiene fibrosis quística?", "nombra enfermedades (Cystic fibrosis)"),
        ("¿Algún médico habló de Dravet?", "nombra enfermedades"),
        ("¿Hubo una pancreatitis hereditaria?", "nombra enfermedades"),
    ],
)
def test_patient_text_hides_genes_diseases_percentages_and_verdict_words(
    text: str, reason: str
) -> None:
    result = check(q("HP:0002099", text), mode=Mode.PATIENT)

    assert result.accepted == ()
    assert any(reason in v for v in result.violations)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Es muy probable que se trate de fibrosis quística.", "Cystic fibrosis"),
        ("Es muy probable que se trate de fibrosis quística.", "(probable)"),
        ("Los síntomas son compatibles con la enfermedad de Dravet.", "Dravet"),
        ("Todo indica una alteración en cftr.", "nombra genes (CFTR)"),
        ("El gen CFTR-related quedó primero.", "nombra genes (CFTR)"),
        ("El gen CFTR-related quedó primero.", "(gen)"),
        ("Hay un 80\uff05 de chance de que sea eso.", "porcentajes"),
        ("Hay un 80\uff05 de chance de que sea eso.", "(chance)"),
        ("Nueve de cada diez familias tienen esto.", "(de cada diez)"),
        ("Podría haber una variante patogénica en el gen.", "variante"),
        ("Podría haber una variante patogénica en el gen.", "patogenica"),
        ("Podría haber una mutación.", "mutacion"),
    ],
)
def test_patient_summaries_catch_the_reviewers_examples(text: str, reason: str) -> None:
    assert any(reason in p for p in patient_summary(text))


def test_a_disease_is_only_named_when_all_its_words_appear() -> None:
    assert patient("¿Algún médico habló de fibrosis en los pulmones?") == []
    assert patient("¿Hubo quistes en los riñones?") == []


def test_the_asked_terms_label_is_exempt_in_its_own_question() -> None:
    assert patient("¿Algún médico mencionó «Síndrome nefrótico»?", "HP:0000100") == []
    assert patient("¿Algún médico mencionó síndrome nefrótico?", "HP:0000100") == []


def test_the_exemption_is_only_for_that_label() -> None:
    violations = patient("¿Algún médico mencionó «Síndrome nefrótico»?", "HP:0002099")

    assert any("sindrome" in v for v in violations)


def test_a_label_cut_short_by_the_template_is_still_exempt() -> None:
    assert patient("¿Algún médico mencionó «Síndrome nef…» o se notó algo así?", "HP:0000100") == []


def test_the_exempt_label_does_not_hide_the_rest_of_the_question() -> None:
    violations = patient(
        "¿Algún médico mencionó «Síndrome nefrótico» por el gen CFTR?", "HP:0000100"
    )

    assert any("CFTR" in v for v in violations)


def test_the_label_exemption_covers_the_help_too() -> None:
    draft = q(
        "HP:0001738", "¿Hay materia fecal grasosa?", help="Se llama insuficiencia pancreática."
    )

    assert check(draft, mode=Mode.PATIENT).violations == ()


def test_patient_help_is_checked_like_the_question() -> None:
    result = check(q(text="¿Notaste heces grasosas?", help="Típico de CFTR."), mode=Mode.PATIENT)

    assert any("nombra genes" in v for v in result.violations)


@pytest.mark.parametrize(
    "text",
    [
        "¿Notaste alguna anomalía genital?",
        "¿Tuvo un malestar general?",
        "¿Notaste CFTRX?",
        "¿Pasa por la noche?",
        "¿Son frecuentes las infecciones?",
        "¿Pasa de cada lado del cuerpo?",
        "¿Se notó algo en el ab?",
    ],
)
def test_patient_checks_match_whole_words_and_everyday_words_pass(text: str) -> None:
    assert patient(text) == []


def test_everyday_word_symbols_still_count_in_capitals() -> None:
    assert any("POR" in v for v in patient("¿Te hablaron de POR?"))


def test_patient_questions_do_not_need_a_rationale() -> None:
    assert (
        check(q(text="¿Notaste heces grasosas?", rationale=""), mode=Mode.PATIENT).violations == ()
    )


def test_a_patient_summary_may_not_name_genes_or_percentages() -> None:
    assert len(patient_summary("CFTR subió al 80%.")) == 2


def test_a_patient_summary_may_say_it_is_not_a_diagnosis() -> None:
    assert patient_summary("Esto no es un diagnóstico: llevá la lista a la consulta.") == []


def test_a_patient_summary_may_repeat_an_answered_label() -> None:
    text = "Se informó síndrome nefrótico. Llevá la lista a la consulta."

    assert patient_summary(text) != []
    assert patient_summary(text, exempt=LABELS["HP:0000100"]) == []


def test_a_clinician_summary_may_name_genes() -> None:
    draft = SummaryDraft(summary="CFTR subió al puesto 1.")

    assert check_summary(draft, Mode.CLINICIAN, TOP_DISEASES) == []


@pytest.mark.parametrize("mode", list(Mode))
def test_an_empty_or_huge_summary_is_rejected(mode: Mode) -> None:
    assert check_summary(SummaryDraft(summary=" "), mode, TOP_DISEASES)
    assert check_summary(SummaryDraft(summary="a" * 1001), mode, TOP_DISEASES)


def test_diseases_outside_the_round_are_not_checked() -> None:
    text = PatientText(VOCABULARY, ())

    assert text.problems("¿Hubo fibrosis quística?", forbidden=()) == []


def test_a_name_made_only_of_generic_words_is_never_matched() -> None:
    vocabulary = PatientVocabulary((), {"X:1": "Distal 16p11.2 microdeletion syndrome"}.get)

    assert vocabulary.for_diseases(["X:1"]).problems("un síndrome distal", forbidden=()) == []
