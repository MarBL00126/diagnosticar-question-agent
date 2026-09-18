from genphenai_repreguntas.certainty import normalize_certainty, parse_json_object
from genphenai_repreguntas.engine import RepreguntasEngine
from genphenai_repreguntas.session import DiagnosticSession


def test_parse_json_object_extracts_json_from_text() -> None:
    parsed = parse_json_object('respuesta:\n{"certeza": 55, "evidencia_mixta": false}')

    assert parsed["certeza"] == 55


def test_normalize_certainty_caps_mixed_evidence() -> None:
    payload = {
        "certeza": 92,
        "evidencia_mixta": True,
        "enfermedades_candidatas": [{"nombre": "A", "probabilidad": "alta"}],
    }

    normalized = normalize_certainty(payload)

    assert normalized["certeza"] == 70
    assert normalized["diagnostico_principal"] == "Diagnostico diferencial no concluyente"


def test_normalize_certainty_caps_unexplained_phenotypes() -> None:
    payload = {
        "certeza_final": 88,
        "fenotipos_no_explicados": ["problemas de memoria"],
        "enfermedades_candidatas": [{"nombre": "A", "probabilidad": "media"}],
    }

    assert normalize_certainty(payload)["certeza_final"] == 70


def test_validate_candidate_evidence_caps_certainty_without_confirmed_hpo() -> None:
    engine = object.__new__(RepreguntasEngine)
    engine.records_by_name = {}
    engine.records_by_code = {}
    session = DiagnosticSession(case_text="Paciente con sintomas inespecificos")
    evaluation = {
        "certeza": 80,
        "enfermedades_candidatas": [
            {
                "nombre": "Alexander disease",
                "probabilidad": "alta",
                "sintomas_coincidentes": ["Discapacidad intelectual"],
            }
        ],
    }

    validated = engine.validate_candidate_evidence(session, evaluation)

    assert validated["certeza"] == 50
    assert validated["enfermedades_candidatas"][0]["probabilidad"] == "media"
    assert validated["enfermedades_candidatas"][0]["sintomas_coincidentes"] == []


def test_rewrite_validated_justification_uses_only_validated_evidence() -> None:
    engine = object.__new__(RepreguntasEngine)
    evaluation = {
        "enfermedades_candidatas": [
            {
                "nombre": "Alpha-mannosidosis",
                "sintomas_coincidentes": ["Hearing impairment (HP:0000365)"],
            },
            {
                "nombre": "Alexander disease",
                "sintomas_coincidentes": [],
            },
        ]
    }

    engine.rewrite_validated_justification(evaluation)

    assert "Alpha-mannosidosis: Hearing impairment" in evaluation["justificacion_certeza"]
    assert "Sin HPO confirmados validados para: Alexander disease" in evaluation["justificacion_certeza"]


def test_filter_missing_criteria_removes_resolved_terms_without_hpo() -> None:
    engine = object.__new__(RepreguntasEngine)

    filtered = engine.filter_missing_criteria(
        ["Macroglossia", "Global developmental delay", "Cataract (HP:0000518)"],
        {"HP:0001263", "HP:0000518"},
        {"Global developmental delay", "Cataract"},
    )

    assert filtered == ["Macroglossia"]


def test_validate_candidate_evidence_filters_final_missing_symptoms() -> None:
    from genphenai_repreguntas.orphadata import DiseaseRecord, HpoTerm

    engine = object.__new__(RepreguntasEngine)
    record = DiseaseRecord(
        disease="Alpha-mannosidosis",
        orpha_code="61",
        hpo_terms=(
            HpoTerm(hpo_id="HP:0000365", term="Hearing impairment", frequency="Frequent"),
            HpoTerm(hpo_id="HP:0001249", term="Intellectual disability", frequency="Frequent"),
        ),
        total_hpo_terms=2,
    )
    engine.records_by_name = {"alpha mannosidosis": record}
    engine.records_by_code = {"61": record}
    session = DiagnosticSession(case_text="Paciente")
    session.confirmed_findings = [
        {"hpo": "HP:0000365", "pregunta": "audicion", "respuesta": "si"},
        {"hpo": "HP:0001249", "pregunta": "aprendizaje", "respuesta": "si"},
    ]
    evaluation = {
        "enfermedades_candidatas": [
            {
                "nombre": "Alpha-mannosidosis",
                "sintomas_coincidentes": [],
                "sintomas_faltantes": ["Intellectual disability", "Macroglossia"],
            }
        ]
    }

    validated = engine.validate_candidate_evidence(session, evaluation)

    assert validated["enfermedades_candidatas"][0]["sintomas_faltantes"] == ["Macroglossia"]


def test_normalize_candidate_probabilities_prefers_validated_evidence() -> None:
    engine = object.__new__(RepreguntasEngine)
    evaluation = {
        "enfermedades_candidatas": [
            {
                "nombre": "Alpha-mannosidosis",
                "probabilidad": "baja",
                "sintomas_coincidentes": ["Hearing impairment (HP:0000365)"],
            },
            {
                "nombre": "Alexander disease",
                "probabilidad": "media",
                "sintomas_coincidentes": [],
            },
        ]
    }

    engine.normalize_candidate_probabilities(evaluation)

    assert evaluation["enfermedades_candidatas"][0]["probabilidad"] == "media"
    assert evaluation["enfermedades_candidatas"][1]["probabilidad"] == "baja"
