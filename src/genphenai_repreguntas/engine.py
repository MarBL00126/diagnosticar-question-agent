from __future__ import annotations

import os
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from genphenai_repreguntas.certainty import normalize_certainty, parse_json_object
from genphenai_repreguntas.config import Settings, load_settings
from genphenai_repreguntas.indexing import build_or_load_vector_store
from genphenai_repreguntas.orphadata import DiseaseRecord, load_orphadata_records
from genphenai_repreguntas.prompts import CONFIDENCE_TEMPLATE, FINAL_TEMPLATE
from genphenai_repreguntas.session import DiagnosticSession, hpo_base, normalize_question, normalize_text


class RepreguntasEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        if self.settings.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", self.settings.openai_api_key)
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required. Copy .env.example to .env and set it.")

        self.vector_store = build_or_load_vector_store(self.settings)
        self.retriever = self.vector_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": self.settings.retriever_k},
        )
        self.llm = ChatOpenAI(model=self.settings.openai_model, temperature=0.2)
        self.confidence_llm = ChatOpenAI(model=self.settings.openai_model, temperature=0.0)
        self.confidence_prompt = ChatPromptTemplate.from_template(CONFIDENCE_TEMPLATE)
        self.final_prompt = ChatPromptTemplate.from_template(FINAL_TEMPLATE)
        self.records_by_name, self.records_by_code = self.load_record_indexes()

    def load_record_indexes(self) -> tuple[dict[str, DiseaseRecord], dict[str, DiseaseRecord]]:
        records = load_orphadata_records(
            self.settings.orphadata_xml,
            min_hpo_terms=self.settings.min_hpo_terms,
            min_diag_criteria=self.settings.min_diag_criteria,
            max_diseases=self.settings.max_diseases,
            max_hpo_per_disease=self.settings.max_hpo_per_disease,
        )
        by_name = {normalize_text(record.disease): record for record in records}
        by_code = {record.orpha_code: record for record in records}
        return by_name, by_code

    def retrieved_context(self, session: DiagnosticSession) -> list[Any]:
        query = session.history_for_llm()
        candidate_codes = [f"ORPHA:{code}" for code in session.candidate_orpha_codes]
        if candidate_codes:
            try:
                docs = self.vector_store.similarity_search(
                    query,
                    k=max(self.settings.retriever_k, len(candidate_codes)),
                    filter={"orpha_code": {"$in": candidate_codes}},
                )
                if docs:
                    return docs
            except Exception:
                pass
        if session.candidate_names:
            try:
                docs = self.vector_store.similarity_search(
                    query,
                    k=max(self.settings.retriever_k, len(session.candidate_names)),
                    filter={"disease": {"$in": session.candidate_names}},
                )
                if docs:
                    return docs
            except Exception:
                pass
        return self.vector_store.similarity_search(query, k=self.settings.retriever_k)

    def evaluate(self, session: DiagnosticSession) -> dict[str, Any]:
        messages = self.confidence_prompt.format_messages(
            context=self.retrieved_context(session),
            symptoms_history=session.history_for_llm(),
            asked_questions=session.asked_questions_for_llm(),
            top_candidates=session.candidates_for_llm(),
            clinical_findings=session.findings_for_llm() or "Todavia no hay HPO confirmados o negados.",
        )
        raw = self.confidence_llm.invoke(messages).content
        try:
            evaluation = parse_json_object(raw)
        except Exception as exc:
            evaluation = {
                "certeza": 30,
                "justificacion_certeza": f"No se pudo parsear respuesta del modelo: {exc}",
                "enfermedades_candidatas": [],
                "preguntas_seguimiento": [],
                "necesita_mas_informacion": True,
            }
        evaluation = normalize_certainty(evaluation)
        evaluation = self.validate_candidate_evidence(session, evaluation)
        evaluation["preguntas_seguimiento"] = self.filter_questions(
            session, evaluation.get("preguntas_seguimiento", [])
        )
        return evaluation

    def find_record_for_candidate(self, candidate: dict[str, Any]) -> DiseaseRecord | None:
        name = normalize_text(str(candidate.get("nombre", "")))
        if name in self.records_by_name:
            return self.records_by_name[name]
        code = str(candidate.get("orpha_code", "")).replace("ORPHA:", "").strip()
        if code and code in self.records_by_code:
            return self.records_by_code[code]
        return None

    def validate_candidate_evidence(
        self,
        session: DiagnosticSession,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        confirmed_hpo = {item["hpo"] for item in session.confirmed_findings}
        denied_hpo = {item["hpo"] for item in session.denied_findings}

        for candidate in evaluation.get("enfermedades_candidatas", []):
            if not isinstance(candidate, dict):
                continue
            if not confirmed_hpo:
                candidate["sintomas_coincidentes"] = []
                if str(candidate.get("probabilidad", "")).lower() == "alta":
                    candidate["probabilidad"] = "media"
                continue
            record = self.find_record_for_candidate(candidate)
            if record is None:
                candidate["sintomas_coincidentes"] = []
                continue
            hpo_to_term = {term.hpo_id: term.term for term in record.hpo_terms}
            valid_confirmed = sorted(confirmed_hpo & set(hpo_to_term))
            candidate["sintomas_coincidentes"] = [
                f"{hpo_to_term[hpo]} ({hpo})" for hpo in valid_confirmed if hpo not in denied_hpo
            ]
            resolved_terms = {
                hpo_to_term[hpo]
                for hpo in confirmed_hpo | denied_hpo
                if hpo in hpo_to_term
            }
            candidate["sintomas_faltantes_criticos"] = self.filter_missing_criteria(
                candidate.get("sintomas_faltantes_criticos", []),
                confirmed_hpo | denied_hpo,
                resolved_terms,
            )
            candidate["sintomas_faltantes"] = self.filter_missing_criteria(
                candidate.get("sintomas_faltantes", []),
                confirmed_hpo | denied_hpo,
                resolved_terms,
            )

            probability = str(candidate.get("probabilidad", "")).lower()
            if probability == "alta" and not candidate["sintomas_coincidentes"]:
                candidate["probabilidad"] = "media"
                if isinstance(candidate.get("certeza_estimada"), (int, float)):
                    candidate["certeza_estimada"] = min(candidate["certeza_estimada"], 60)

        self.normalize_candidate_probabilities(evaluation)

        if not any(
            isinstance(candidate, dict) and candidate.get("sintomas_coincidentes")
            for candidate in evaluation.get("enfermedades_candidatas", [])
        ):
            for key in ("certeza", "certeza_final"):
                if isinstance(evaluation.get(key), (int, float)):
                    evaluation[key] = min(evaluation[key], 50)

        self.rewrite_validated_justification(evaluation)
        return normalize_certainty(evaluation)

    def normalize_candidate_probabilities(self, evaluation: dict[str, Any]) -> None:
        candidates = [
            candidate for candidate in evaluation.get("enfermedades_candidatas", [])
            if isinstance(candidate, dict)
        ]
        if not candidates:
            return

        evidence_counts = [
            len(candidate.get("sintomas_coincidentes") or [])
            for candidate in candidates
        ]
        if max(evidence_counts, default=0) == 0:
            return

        for candidate, count in zip(candidates, evidence_counts):
            probability = str(candidate.get("probabilidad", "")).lower()
            if count == 0:
                candidate["probabilidad"] = "baja"
            elif probability == "baja":
                candidate["probabilidad"] = "media"

    def filter_missing_criteria(
        self,
        missing: Any,
        resolved_hpo: set[str],
        resolved_terms: set[str],
    ) -> list[Any]:
        if not isinstance(missing, list) or (not resolved_hpo and not resolved_terms):
            return missing if isinstance(missing, list) else []
        filtered = []
        normalized_terms = {normalize_text(term) for term in resolved_terms}
        for item in missing:
            text = str(item)
            hpo = hpo_base(text)
            if hpo and hpo in resolved_hpo:
                continue
            normalized_text = normalize_text(text)
            if any(term and term in normalized_text for term in normalized_terms):
                continue
            filtered.append(item)
        return filtered

    def rewrite_validated_justification(self, evaluation: dict[str, Any]) -> None:
        candidates = [
            candidate for candidate in evaluation.get("enfermedades_candidatas", [])
            if isinstance(candidate, dict)
        ]
        with_evidence = [
            candidate for candidate in candidates
            if candidate.get("sintomas_coincidentes")
        ]
        without_evidence = [
            str(candidate.get("nombre", "candidata sin nombre"))
            for candidate in candidates
            if not candidate.get("sintomas_coincidentes")
        ]

        if not candidates:
            return
        if not with_evidence:
            evaluation["justificacion_certeza"] = (
                "No hay HPO confirmados y validados contra Orphadata para las candidatas actuales; "
                "se requieren repreguntas diferenciales antes de aumentar la certeza."
            )
            evaluation["justificacion_global"] = evaluation["justificacion_certeza"]
            return

        evidence_parts = []
        for candidate in with_evidence:
            evidence = ", ".join(str(item) for item in candidate.get("sintomas_coincidentes", []))
            evidence_parts.append(f"{candidate.get('nombre', 'candidata')}: {evidence}")
        text = "Evidencia HPO validada contra Orphadata: " + "; ".join(evidence_parts) + "."
        if without_evidence:
            text += " Sin HPO confirmados validados para: " + ", ".join(without_evidence) + "."
        text += " La certeza se mantiene moderada si faltan fenotipos criticos o persiste ambiguedad."
        evaluation["justificacion_certeza"] = text
        evaluation["justificacion_global"] = text

    def filter_questions(self, session: DiagnosticSession, questions: list[Any]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        seen_categories: set[str] = set()
        for raw_question in questions:
            question = normalize_question(raw_question)
            category = question["categoria_clinica"]
            hpo = hpo_base(question.get("hpo_asociado", ""))
            if category in session.asked_categories or category in seen_categories:
                continue
            if hpo and hpo in session.asked_hpo:
                continue
            filtered.append(question)
            seen_categories.add(category)
        return filtered

    def final_diagnosis(self, session: DiagnosticSession) -> dict[str, Any]:
        messages = self.final_prompt.format_messages(
            context=self.retrieved_context(session),
            all_symptoms="\n".join(session.history),
            top_candidates=session.candidates_for_llm(),
            clinical_findings=session.findings_for_llm() or "Todavia no hay HPO confirmados o negados.",
        )
        raw = self.llm.invoke(messages).content
        try:
            diagnosis = normalize_certainty(parse_json_object(raw))
            return self.validate_candidate_evidence(session, diagnosis)
        except Exception:
            return {
                "diagnostico_principal": "Diagnostico diferencial no concluyente",
                "certeza_final": 40,
                "justificacion_global": raw,
                "enfermedades_candidatas": [],
                "fenotipos_no_explicados": [],
                "evidencia_mixta": True,
                "recomendaciones": [],
            }

    def response_from(self, session_id: str, session: DiagnosticSession, evaluation: dict[str, Any]) -> dict[str, Any]:
        certainty = int(evaluation.get("certeza") or 0)
        questions = evaluation.get("preguntas_seguimiento", [])
        final = (
            certainty >= self.settings.certainty_threshold
            or session.iteration >= self.settings.max_iterations
            or not questions
        )
        payload = {
            "session_id": session_id,
            "status": "final" if final else "asking",
            "certainty": certainty,
            "justification": evaluation.get("justificacion_certeza", ""),
            "candidates": evaluation.get("enfermedades_candidatas", []),
            "phenotypes_not_explained": evaluation.get("fenotipos_no_explicados", []),
            "mixed_evidence": evaluation.get("evidencia_mixta", False),
            "questions": [] if final else questions,
            "iteration": session.iteration,
        }
        if final:
            final_diagnosis = self.final_diagnosis(session)
            final_diagnosis["fenotipos_no_explicados"] = evaluation.get("fenotipos_no_explicados", [])
            final_diagnosis["evidencia_mixta"] = evaluation.get("evidencia_mixta", False)
            payload["final_diagnosis"] = final_diagnosis
        else:
            session.pending_questions = questions
        return payload
