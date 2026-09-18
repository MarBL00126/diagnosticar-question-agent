from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from genphenai_repreguntas.orphadata import normalize_orpha_code


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-záéíóúüñ0-9: ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def hpo_base(hpo: str) -> str:
    match = re.search(r"HP:\d+", hpo or "")
    return match.group(0) if match else ""


def hpo_codes(text: str) -> list[str]:
    return re.findall(r"HP:\d+", text or "")


def question_category(question: dict[str, Any]) -> str:
    text = normalize_text(
        " ".join(
            [
                str(question.get("pregunta", "")),
                str(question.get("hpo_asociado", "")),
                str(question.get("motivo", "")),
            ]
        )
    )
    rules = [
        ("hematuria", ["hematuria", "sangre en la orina", "hp:0000790"]),
        ("proteinuria", ["proteinuria", "proteina en orina", "proteínas en orina", "hp:0000093"]),
        ("diabetes_mellitus", ["diabetes mellitus", "glucemia", "hp:0000819"]),
        ("poliuria_polidipsia", ["sed", "orinar", "poliuria", "polidipsia", "hp:0000103", "hp:0001959"]),
        ("desarrollo", ["desarrollo", "hitos", "aprendizaje", "discapacidad intelectual", "hp:0001263", "hp:0001249"]),
        ("vision", ["vision", "visión", "visual", "ceguera", "electroretinogram", "hp:0000505"]),
        ("audicion", ["audicion", "audición", "hipoacusia", "hearing", "hp:0000365"]),
        ("memoria", ["memoria", "olvidos", "hp:0002354"]),
        ("movilidad", ["movilidad", "marcha", "caminar", "hp:0002540"]),
    ]
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    hpo = hpo_base(str(question.get("hpo_asociado", "")))
    return f"hpo:{hpo}" if hpo else "texto:" + " ".join(text.split()[:8])


def normalize_question(question: Any) -> dict[str, Any]:
    if isinstance(question, dict):
        normalized = {
            "pregunta": str(question.get("pregunta") or "Podrias ampliar los sintomas?"),
            "hpo_asociado": str(question.get("hpo_asociado") or "No especificado"),
            "distingue": question.get("distingue") if isinstance(question.get("distingue"), list) else [],
            "motivo": str(question.get("motivo") or ""),
        }
    else:
        normalized = {
            "pregunta": str(question),
            "hpo_asociado": "No especificado",
            "distingue": [],
            "motivo": "Pregunta general de ampliacion clinica.",
        }
    normalized = align_question_with_hpo(normalized)
    normalized["categoria_clinica"] = question_category(normalized)
    return normalized


def align_question_with_hpo(question: dict[str, Any]) -> dict[str, Any]:
    """Keep common neurologic questions aligned with their single HPO code."""

    codes = hpo_codes(str(question.get("hpo_asociado", "")))
    if len(codes) > 1:
        question["hpo_asociado"] = codes[0]

    hpo = hpo_base(str(question.get("hpo_asociado", "")))
    text = str(question.get("pregunta", ""))
    normalized = normalize_text(text)

    if hpo == "HP:0001250" and ("espastic" in normalized or "espasmo" in normalized):
        question["pregunta"] = "¿Ha tenido convulsiones?"
        question["motivo"] = (
            "Las convulsiones corresponden a HP:0001250 y ayudan a evaluar "
            "compromiso neurologico diferencial."
        )
    elif hpo == "HP:0001257" and (
        "convulsion" in normalized
        or "convulsiones" in normalized
        or "equilibrio" in normalized
        or "coordinacion" in normalized
        or "coordinar" in normalized
        or "espasmo" in normalized
    ):
        question["pregunta"] = "¿Ha notado espasticidad, rigidez muscular o aumento del tono muscular?"
        question["motivo"] = (
            "La espasticidad corresponde a HP:0001257 y debe preguntarse separada "
            "de convulsiones, equilibrio o coordinacion."
        )
    elif hpo == "HP:0001249" and ("memoria" in normalized or "recordar" in normalized):
        question["pregunta"] = "¿Ha tenido dificultades de aprendizaje o discapacidad intelectual?"
        question["motivo"] = (
            "La discapacidad intelectual corresponde a HP:0001249 y debe preguntarse "
            "separada de problemas de memoria."
        )
    elif hpo == "HP:0001744":
        question["pregunta"] = "¿Ha notado aumento en el tamaño del bazo o le mencionaron esplenomegalia?"
        question["motivo"] = (
            "La esplenomegalia corresponde a HP:0001744 y debe preguntarse separada "
            "de hepatomegalia."
        )
    elif hpo == "HP:0002240":
        question["pregunta"] = "¿Ha notado aumento en el tamaño del higado o le mencionaron hepatomegalia?"
        question["motivo"] = (
            "La hepatomegalia corresponde a HP:0002240 y debe preguntarse separada "
            "de esplenomegalia."
        )

    return question


STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "en", "o", "y", "a",
    "has", "ha", "han", "tenido", "tiene", "notado", "algun", "alguna",
    "pregunta", "sintoma", "sintomas", "ayuda", "diferenciar",
}


def content_tokens(text: str) -> set[str]:
    return {
        token
        for token in normalize_text(text).split()
        if len(token) >= 3 and token not in STOPWORDS and not token.startswith("hp:")
    }


def answer_segments(answer: str) -> list[str]:
    parts = re.split(r"[.;\n]+", answer or "")
    return [part.strip() for part in parts if part.strip()]


def classify_answer_for_question(answer: str, question: dict[str, Any]) -> str:
    segments = answer_segments(answer)
    if not segments:
        return "desconocido"

    question_text = " ".join(
        [
            str(question.get("pregunta", "")),
            str(question.get("motivo", "")),
            str(question.get("hpo_asociado", "")),
        ]
    )
    q_tokens = content_tokens(question_text)
    scored_segments = [
        (len(content_tokens(segment) & q_tokens), segment)
        for segment in segments
    ]
    best_score, best_segment = max(scored_segments, default=(0, ""), key=lambda item: item[0])
    if best_score == 0:
        return "desconocido"
    text = normalize_text(best_segment)
    if re.search(r"\b(no|nunca|niega|sin|ausencia)\b", text):
        return "negado"
    if re.search(r"\b(si|presenta|tiene|tuvo|refiere|hay|con)\b", text):
        return "confirmado"
    return "desconocido"


@dataclass
class DiagnosticSession:
    case_text: str
    candidate_orpha_codes: list[str] = field(default_factory=list)
    candidate_names: list[str] = field(default_factory=list)
    history: list[str] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    asked_categories: set[str] = field(default_factory=set)
    asked_hpo: set[str] = field(default_factory=set)
    confirmed_findings: list[dict[str, str]] = field(default_factory=list)
    denied_findings: list[dict[str, str]] = field(default_factory=list)
    unknown_findings: list[dict[str, str]] = field(default_factory=list)
    iteration: int = 1

    def __post_init__(self) -> None:
        self.candidate_orpha_codes = [
            normalize_orpha_code(code)
            for code in self.candidate_orpha_codes
            if str(code).strip()
        ]
        self.candidate_names = [
            str(name).strip()
            for name in self.candidate_names
            if str(name).strip()
        ]
        if not self.history:
            self.history.append(f"CONSULTA INICIAL: {self.case_text.strip()}")

    def history_for_llm(self) -> str:
        lines = ["SINTOMAS Y RESPUESTAS:"]
        lines.extend(f"- {item}" for item in self.history)
        if self.questions:
            lines.append("\nPREGUNTAS YA REALIZADAS:")
            for question in self.questions:
                lines.append(
                    f"- [{question.get('categoria_clinica')}] {question.get('pregunta')} "
                    f"(HPO: {question.get('hpo_asociado')})"
                )
        findings = self.findings_for_llm()
        if findings:
            lines.append("\nHALLAZGOS ESTRUCTURADOS:")
            lines.append(findings)
        return "\n".join(lines)

    def candidates_for_llm(self) -> str:
        if not self.candidate_orpha_codes and not self.candidate_names:
            return "No se recibieron candidatas previas de Diagnostic.ar."
        lines = ["TOP CANDIDATAS DIAGNOSTIC.AR:"]
        lines.extend(f"- ORPHA:{code}" for code in self.candidate_orpha_codes)
        lines.extend(f"- {name}" for name in self.candidate_names)
        return "\n".join(lines)

    def asked_questions_for_llm(self) -> str:
        if not self.questions:
            return "Todavia no se realizaron repreguntas."
        return "\n".join(
            f"- [{question.get('categoria_clinica')}] {question.get('pregunta')} "
            f"(HPO: {question.get('hpo_asociado')})"
            for question in self.questions
        )

    def findings_for_llm(self) -> str:
        lines: list[str] = []
        for label, findings in [
            ("CONFIRMADOS", self.confirmed_findings),
            ("NEGADOS", self.denied_findings),
            ("NO CLASIFICADOS", self.unknown_findings),
        ]:
            if findings:
                lines.append(f"{label}:")
                lines.extend(
                    f"- {item['hpo']} | {item['pregunta']} | respuesta: {item['respuesta']}"
                    for item in findings
                )
        return "\n".join(lines)

    def register_answered_findings(self, answer: str) -> None:
        for question in self.pending_questions:
            hpo = hpo_base(question.get("hpo_asociado", ""))
            if not hpo:
                continue
            item = {
                "hpo": hpo,
                "pregunta": str(question.get("pregunta", "")),
                "respuesta": answer.strip(),
            }
            status = classify_answer_for_question(answer, question)
            if status == "confirmado":
                self.confirmed_findings.append(item)
            elif status == "negado":
                self.denied_findings.append(item)
            else:
                self.unknown_findings.append(item)

    def register_pending_as_asked(self) -> None:
        for question in self.pending_questions:
            self.questions.append(question)
            category = question.get("categoria_clinica")
            if category:
                self.asked_categories.add(category)
            hpo = hpo_base(question.get("hpo_asociado", ""))
            if hpo:
                self.asked_hpo.add(hpo)
        self.pending_questions = []

    def add_answer(self, answer: str) -> None:
        self.register_answered_findings(answer)
        self.register_pending_as_asked()
        self.history.append(f"RESPUESTA ITERACION {self.iteration}: {answer.strip()}")
        self.iteration += 1
