from __future__ import annotations

import pytest

from diagnosticar_preguntas.agent.guardrails import QuestionRules, check_round, check_summary
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.schemas import QuestionDraft, RoundDraft, SummaryDraft

SHORTLIST = frozenset({"HP:0001738", "HP:0001696", "HP:0002099", "HP:0000765"})
GENES = frozenset({"CFTR", "DNAH5", "HLA-B"})


def rules(mode: Mode = Mode.CLINICIAN, expected: int = 1) -> QuestionRules:
    return QuestionRules(
        mode=mode,
        shortlist=SHORTLIST,
        already_known=frozenset({"HP:0002205", "HP:0000765"}),
        gene_symbols=GENES,
        expected=expected,
    )


def q(
    hpo_id: str = "HP:0001738",
    text: str = "¿Presenta esteatorrea?",
    help: str | None = None,
    rationale: str = "Separa CFTR (sí) de DNAH5 (no).",
) -> QuestionDraft:
    return QuestionDraft(hpo_id=hpo_id, text=text, help=help, rationale=rationale)


def check(*questions: QuestionDraft, mode: Mode = Mode.CLINICIAN, expected: int | None = None):
    return check_round(
        RoundDraft(questions=list(questions)),
        rules(mode, expected if expected is not None else len(questions)),
    )


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


def test_clinician_text_may_name_genes() -> None:
    assert check(q(text="¿Tiene un estudio previo de CFTR?")).violations == ()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("¿Notaste algo que apunte a CFTR?", "nombra genes"),
        ("¿Te hablaron del HLA-B?", "nombra genes"),
        ("¿Notaste esto en el 50% de las comidas?", "porcentajes"),
        ("¿Te dieron un diagnóstico de fibrosis?", "palabras no permitidas"),
        ("¿Te hablaron de un gen alterado?", "palabras no permitidas"),
        ("¿Conocés la probabilidad de tenerlo?", "palabras no permitidas"),
        ("¿Te dijeron que es un sindrome?", "palabras no permitidas"),
    ],
)
def test_patient_text_hides_genes_percentages_and_verdict_words(text: str, reason: str) -> None:
    result = check(q(text=text), mode=Mode.PATIENT)

    assert result.accepted == ()
    assert any(reason in v for v in result.violations)


def test_patient_help_is_checked_like_the_question() -> None:
    result = check(q(text="¿Notaste heces grasosas?", help="Típico de CFTR."), mode=Mode.PATIENT)

    assert any("nombra genes" in v for v in result.violations)


@pytest.mark.parametrize(
    "text", ["¿Notaste alguna anomalía genital?", "¿Tuvo un malestar general?", "¿Notaste CFTRX?"]
)
def test_patient_checks_match_whole_words_only(text: str) -> None:
    assert check(q(text=text), mode=Mode.PATIENT).violations == ()


def test_patient_questions_do_not_need_a_rationale() -> None:
    assert (
        check(q(text="¿Notaste heces grasosas?", rationale=""), mode=Mode.PATIENT).violations == ()
    )


def test_a_patient_summary_may_not_name_genes_or_percentages() -> None:
    problems = check_summary(SummaryDraft(summary="CFTR subió al 80%."), Mode.PATIENT, GENES)

    assert len(problems) == 2


def test_a_patient_summary_may_say_it_is_not_a_diagnosis() -> None:
    text = "Esto no es un diagnóstico: llevá la lista a la consulta."

    assert check_summary(SummaryDraft(summary=text), Mode.PATIENT, GENES) == []


def test_a_clinician_summary_may_name_genes() -> None:
    assert (
        check_summary(SummaryDraft(summary="CFTR subió al puesto 1."), Mode.CLINICIAN, GENES) == []
    )


@pytest.mark.parametrize("mode", list(Mode))
def test_an_empty_or_huge_summary_is_rejected(mode: Mode) -> None:
    assert check_summary(SummaryDraft(summary=" "), mode, GENES)
    assert check_summary(SummaryDraft(summary="a" * 1001), mode, GENES)
