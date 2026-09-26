"""Checks every LLM draft must pass before it reaches a user.

Violations are phrased in Spanish because they are fed back to the model on
the retry. Patient-facing text must not name genes, show percentages or use
words that read as a verdict; the list is deliberately short and literal so a
reviewer can audit it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.schemas import QuestionDraft, RoundDraft, SummaryDraft

MAX_QUESTION_CHARS = 200
MAX_HELP_CHARS = 200
MAX_RATIONALE_CHARS = 300
MAX_SUMMARY_CHARS = {Mode.PATIENT: 700, Mode.CLINICIAN: 1000}

_PERCENT = re.compile(r"\d\s*%|por ciento")
# Word starts, matched against lower-cased text without accents. "gen" must be a whole
# word so that "genital" or "general" pass.
_FORBIDDEN_IN_SUMMARY = ("probabilidad", "porcentaje", "mutacion", "sindrome")
_FORBIDDEN_IN_QUESTIONS = (*_FORBIDDEN_IN_SUMMARY, "diagnostic", r"gen(?:es)?\b")


@dataclass(frozen=True, slots=True)
class QuestionRules:
    mode: Mode
    shortlist: frozenset[str]
    already_known: frozenset[str]
    gene_symbols: frozenset[str]
    expected: int


@dataclass(frozen=True, slots=True)
class RoundCheck:
    accepted: tuple[QuestionDraft, ...]
    violations: tuple[str, ...]


def check_round(draft: RoundDraft, rules: QuestionRules) -> RoundCheck:
    """Split a draft into questions that pass every rule and the reasons the rest failed."""
    accepted: list[QuestionDraft] = []
    violations: list[str] = []
    seen: set[str] = set()
    for question in draft.questions:
        problems = _question_problems(question, rules)
        if question.hpo_id in seen:
            problems.append("repite un síntoma de otra pregunta de esta ronda")
        seen.add(question.hpo_id)
        if problems:
            violations.extend(f"{question.hpo_id}: {p}" for p in problems)
        elif len(accepted) < rules.expected:
            accepted.append(question)
    if len(draft.questions) != rules.expected:
        violations.append(
            f"se pidieron {rules.expected} preguntas y llegaron {len(draft.questions)}"
        )
    return RoundCheck(tuple(accepted), tuple(violations))


def check_summary(draft: SummaryDraft, mode: Mode, gene_symbols: Collection[str]) -> list[str]:
    text = draft.summary.strip()
    if not text:
        return ["el resumen está vacío"]
    problems = []
    if len(text) > MAX_SUMMARY_CHARS[mode]:
        problems.append(f"el resumen supera {MAX_SUMMARY_CHARS[mode]} caracteres")
    if mode is Mode.PATIENT:
        problems.extend(_patient_problems(text, gene_symbols, _FORBIDDEN_IN_SUMMARY))
    return problems


def _question_problems(question: QuestionDraft, rules: QuestionRules) -> list[str]:
    problems: list[str] = []
    if question.hpo_id not in rules.shortlist:
        problems.append("no está entre los candidatos")
    if question.hpo_id in rules.already_known:
        problems.append("ese síntoma ya se preguntó o ya se conoce")
    text = question.text.strip()
    if not text:
        return [*problems, "el texto está vacío"]
    if len(text) > MAX_QUESTION_CHARS:
        problems.append(f"el texto supera {MAX_QUESTION_CHARS} caracteres")
    if not (text.startswith("¿") and text.endswith("?")) or text.count("?") != 1:
        problems.append("tiene que ser una sola pregunta, entre ¿ y ?")
    help_text = (question.help or "").strip()
    if len(help_text) > MAX_HELP_CHARS:
        problems.append(f"la ayuda supera {MAX_HELP_CHARS} caracteres")
    if rules.mode is Mode.PATIENT:
        for part in (text, help_text):
            problems.extend(_patient_problems(part, rules.gene_symbols, _FORBIDDEN_IN_QUESTIONS))
    else:
        rationale = question.rationale.strip()
        if not rationale:
            problems.append("falta el fundamento para el médico")
        elif len(rationale) > MAX_RATIONALE_CHARS:
            problems.append(f"el fundamento supera {MAX_RATIONALE_CHARS} caracteres")
    return problems


def _patient_problems(
    text: str, gene_symbols: Collection[str], forbidden: Iterable[str]
) -> list[str]:
    if not text:
        return []
    problems = []
    named = sorted(s for s in gene_symbols if _mentions_symbol(text, s))
    if named:
        problems.append(f"nombra genes ({', '.join(named)}) en texto para pacientes")
    if _PERCENT.search(text):
        problems.append("muestra porcentajes en texto para pacientes")
    plain = _plain(text)
    words = sorted({m.group(0) for p in forbidden if (m := re.search(rf"\b{p}\w*", plain))})
    if words:
        problems.append(f"usa palabras no permitidas para pacientes ({', '.join(words)})")
    return problems


def _mentions_symbol(text: str, symbol: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(symbol)}(?![\w-])", text) is not None


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))
