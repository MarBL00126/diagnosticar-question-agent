from genphenai_repreguntas.session import (
    DiagnosticSession,
    classify_answer_for_question,
    normalize_question,
    question_category,
)


def test_session_formats_candidates_and_history() -> None:
    session = DiagnosticSession(
        case_text="Paciente con movilidad reducida",
        candidate_orpha_codes=["ORPHA:58", "61"],
        candidate_names=["Alexander disease"],
    )

    assert "CONSULTA INICIAL" in session.history_for_llm()
    assert "ORPHA:58" in session.candidates_for_llm()
    assert "ORPHA:61" in session.candidates_for_llm()
    assert "Alexander disease" in session.candidates_for_llm()


def test_register_pending_questions_prevents_repeated_categories() -> None:
    session = DiagnosticSession(case_text="Paciente con problemas de memoria")
    session.pending_questions = [
        normalize_question(
            {
                "pregunta": "¿Tiene olvidos frecuentes?",
                "hpo_asociado": "HP:0002354",
                "motivo": "Explora memoria",
            }
        )
    ]

    session.add_answer("Si, tiene olvidos.")

    assert "memoria" in session.asked_categories
    assert "HP:0002354" in session.asked_hpo
    assert session.iteration == 2


def test_question_category_uses_hpo_fallback() -> None:
    question = {"pregunta": "¿Presenta X?", "hpo_asociado": "HP:1234567", "motivo": ""}

    assert question_category(question) == "hpo:HP:1234567"


def test_answered_findings_track_confirmed_and_denied_hpo() -> None:
    session = DiagnosticSession(case_text="Paciente con retraso")
    session.pending_questions = [
        normalize_question(
            {
                "pregunta": "¿Has notado algun aumento en el tamano de la lengua?",
                "hpo_asociado": "HP:0000158",
                "motivo": "Macroglossia",
            }
        ),
        normalize_question(
            {
                "pregunta": "¿Has tenido retraso en el desarrollo?",
                "hpo_asociado": "HP:0001263",
                "motivo": "Global developmental delay",
            }
        ),
    ]

    session.add_answer(
        "No tiene aumento del tamano de la lengua. "
        "Si tuvo retraso en el desarrollo y problemas de aprendizaje."
    )

    assert session.denied_findings[0]["hpo"] == "HP:0000158"
    assert session.confirmed_findings[0]["hpo"] == "HP:0001263"


def test_classify_answer_for_question_handles_negative_segment() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has notado algun aumento en el tamano de la cabeza?",
            "hpo_asociado": "HP:0000256",
        }
    )

    assert classify_answer_for_question("No tiene aumento del tamano de la cabeza.", question) == "negado"


def test_classify_answer_does_not_deny_unmentioned_question() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has tenido problemas de vision, como cataratas?",
            "hpo_asociado": "HP:0000518",
        }
    )

    answer = "No tiene aumento del tamano de la lengua. Si tuvo retraso en el desarrollo global."

    assert classify_answer_for_question(answer, question) == "desconocido"


def test_normalize_question_keeps_seizure_question_aligned_with_hpo() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has experimentado convulsiones o episodios de espasticidad?",
            "hpo_asociado": "HP:0001250",
        }
    )

    assert question["pregunta"] == "¿Ha tenido convulsiones?"
    assert "espasticidad" not in question["pregunta"].lower()


def test_normalize_question_keeps_seizure_question_separate_from_spasms() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has tenido convulsiones o episodios de espasmos?",
            "hpo_asociado": "HP:0001250",
        }
    )

    assert question["pregunta"] == "¿Ha tenido convulsiones?"
    assert "espasmos" not in question["pregunta"].lower()


def test_development_hpo_is_not_categorized_as_mobility() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has tenido problemas de desarrollo o retraso en alcanzar hitos como caminar o hablar?",
            "hpo_asociado": "HP:0001263",
        }
    )

    assert question["categoria_clinica"] == "desarrollo"


def test_spasticity_hpo_is_not_asked_as_balance_or_coordination() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has notado problemas de equilibrio o coordinación?",
            "hpo_asociado": "HP:0001257",
        }
    )

    assert question["pregunta"] == "¿Ha notado espasticidad, rigidez muscular o aumento del tono muscular?"
    assert "equilibrio" not in question["pregunta"].lower()
    assert "coordinación" not in question["pregunta"].lower()


def test_spasticity_hpo_is_not_asked_as_coordination_or_spasms() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has notado dificultad para coordinar tus movimientos o espasmos musculares?",
            "hpo_asociado": "HP:0001257",
        }
    )

    assert question["pregunta"] == "¿Ha notado espasticidad, rigidez muscular o aumento del tono muscular?"
    assert "coordinar" not in question["pregunta"].lower()
    assert "espasmos" not in question["pregunta"].lower()


def test_intellectual_disability_hpo_is_not_asked_as_memory() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has notado dificultad para recordar cosas o aprender nuevas habilidades?",
            "hpo_asociado": "HP:0001249",
        }
    )

    assert question["pregunta"] == "¿Ha tenido dificultades de aprendizaje o discapacidad intelectual?"
    assert "recordar" not in question["pregunta"].lower()


def test_multiple_hpo_question_is_reduced_to_one_hpo() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Ha notado algun aumento en el tamano del bazo o del higado?",
            "hpo_asociado": "HP:0001744, HP:0002240",
        }
    )

    assert question["hpo_asociado"] == "HP:0001744"
    assert "higado" not in question["pregunta"].lower()


def test_splenomegaly_hpo_asks_about_spleen() -> None:
    question = normalize_question(
        {
            "pregunta": "¿Has notado aumento del abdomen o problemas para comer?",
            "hpo_asociado": "HP:0001744",
        }
    )

    assert "bazo" in question["pregunta"].lower()
