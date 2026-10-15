"""Checks every LLM draft must pass before it reaches a user.

Violations are phrased in Spanish because they are fed back to the model on
the retry. Patient-facing text must not name genes or diseases, show
percentages or frequencies, or use words that read as a verdict; the lists are
deliberately short and literal so a reviewer can audit them.

What patient text may not contain:

* any gene symbol of the knowledge base with at least three characters, in
  any case; ``-`` separates words, so "CFTR-related" names CFTR. Symbols that
  are also everyday words ("POR", "SON", "MAL") only count written in capitals;
* the name of a disease of the current top hypotheses, compared word by word
  after folding accents, case and the usual English/Spanish spelling
  differences ("Cystic fibrosis" matches "fibrosis quística");
* a percentage (after NFKC, so the full-width percent sign counts) or a
  frequency ("nueve de cada diez", "probable", "riesgo", "chance");
* verdict words: "síndrome", "gen", "variante", "mutación", "patogénica", and
  in questions "diagnóstico".

No summary, in either mode, may advise anything or name a study or test
("conviene", "solicitar", "descartar", "estudio", "exoma", "EEG", ...): it only
recaps what was asked, what was answered and how the list moved, because
nobody on the team can validate clinical advice.

The official label of the term a question asks about is exempt in that
question (an HPO label may well read "Síndrome nefrótico"), and in a summary
the labels of the answered terms are ("Anormalidad en el EEG").
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass

from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.schemas import (
    MAX_QUESTION_CHARS,
    QuestionDraft,
    RoundDraft,
    SummaryDraft,
)

MAX_HELP_CHARS = 200
MAX_RATIONALE_CHARS = 300
MAX_SUMMARY_CHARS = {Mode.PATIENT: 700, Mode.CLINICIAN: 1000}
MIN_SYMBOL_CHARS = 3

# Gene symbols that are also Spanish (or common English) words; lower-case uses are the word.
EVERYDAY_WORD_SYMBOLS = frozenset(
    {
        "ABO", "ACHE", "AIRE", "APP", "CAST", "CAT", "CHAT", "COPA", "DIABLO", "GAL", "ITCH",
        "KIT", "MAL", "MAX", "NODAL", "NONO", "POLE", "POR", "PURA", "RALA", "RARA", "REST",
        "SACS", "SET", "SKI", "SMS", "SON", "STAR", "WAS",
    }
)  # fmt: skip

_PERCENT = re.compile(r"\d\s*%|\bpor\s*ciento\b")
_NUMBER = r"(?:\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|veinte|cien|mil)"
# Word starts, matched against lower-cased text without accents. "gen" must be a whole
# word so that "genital" or "general" pass; "de cada" needs a number so "de cada lado" passes.
_FORBIDDEN_IN_SUMMARY = (
    "probabilidad",
    "probable",
    "porcentaje",
    "riesgo",
    "chance",
    rf"de cada {_NUMBER}\b",
    "mutacion",
    "variante",
    "patogenic",
    "sindrome",
    r"gen(?:es)?\b",
)
_FORBIDDEN_IN_QUESTIONS = (*_FORBIDDEN_IN_SUMMARY, "diagnostic")

# Word starts of advice and of studies and tests, forbidden in every summary. Verb forms are
# listed one by one so that "pediatra", "indica" or "secuencia" pass.
_ADVICE_IN_SUMMARY = (
    "solicit",
    r"ped(?:ir|i(?:le|les)?\b)",
    r"pid(?:a|as|an|ale|anle)\b",
    "indicar",
    "indiqu",
    r"indicad[oa]s?\b",
    "consider",
    r"conv(?:ien|endr|enien)",
    "suger",
    "sugier",
    "recomend",
    "confirm",
    "descart",
    r"deriv(?:ar|acion|en?\b)",
    "interconsult",
    r"(?:proximo|siguiente)s? pasos?\b",
    "estudi",
    r"analisis\b",
    r"pruebas?\b",
    r"tests?\b",
    "exom",
    "genom",
    r"secuenci(?:ar|acion|ad)",
    r"panel(?:es)?\b",
    "biops",
    "resonancia",
    r"rmn?\b",
    "tomogra",
    r"tac\b",
    r"tc\b",
    "electroencefalogra",
    r"eeg\b",
    "electrocardiogra",
    r"ecg\b",
    "ecocardiogra",
    "ecogra",
    "laboratori",
    "dosaj",
)
# A word start not preceded by "-" either, so the gene MT-TC does not read as "TC".
_ADVICE = re.compile(rf"(?<![\w-])(?:{'|'.join(_ADVICE_IN_SUMMARY)})\w*")
# A clinician rationale that says what a "no" would do: "(no)" after genes, "con un no",
# "si la respuesta es no", "un «no»". A "no" does not reach the ranking by default.
_NO_MOVES_GENES = re.compile(
    r"\(\s*no\s*\)"
    r"|\b(?:con|ante|si es|si la respuesta es)\s+(?:un\s+)?[«\"“']?no(?![\wáéíóúñ])"
    r"|\bun\s+[«\"“']no[»\"”']",
    re.IGNORECASE,
)

# Words of disease names that say nothing about which disease it is.
_GENERIC_NAME_WORDS = (
    "abnormality abnormalities acquired adult and anomalies associated atypical autosomal benign"
    " caused childhood chromosome classic classical combined complementation complex congenital"
    " deficiency defect deletion digenic disease disorder distal dominant due duplication early"
    " familial female form for gene group hereditary including infancy infantile inherited"
    " isolated juvenile late like linked male maternal microdeletion microduplication mild"
    " multiple mutation neonatal non onset other partial paternal plus primary progressive"
    " protein proximal recessive related region secondary severe single spectrum sporadic"
    " subtype susceptibility syndrome syndromic the to transient type variant with without"
    # Also everyday Spanish once accents are folded ("Mass syndrome" would match "más").
    " complete factor helix incomplete masa mass mitochondrial sensitive terminal viral"
)
_SPELLING: tuple[tuple[str, str], ...] = (
    ("qu", "c"),
    ("ph", "f"),
    ("th", "t"),
    ("ch", "c"),
    ("k", "c"),
    ("y", "i"),
    ("j", "g"),
    ("ae", "e"),
    ("oe", "e"),
)


def _fold(text: str) -> str:
    """NFKC, then accents removed; case is kept."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _stem(word: str) -> str:
    """A spelling-neutral stem, so English disease names match their Spanish renderings."""
    for english, spanish in _SPELLING:
        word = word.replace(english, spanish)
    word = re.sub(r"(.)\1+", r"\1", word)
    while len(word) > 5 and word[-1] in "aeiosu":
        word = word[:-1]
    return word


def _stems(folded_lower: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", folded_lower)}


_NAMES_A_DISEASE = re.compile(r"\b(?:syndrome|disease)\b")
_GENERIC_STEMS = frozenset(_stem(w) for w in _GENERIC_NAME_WORDS.split())


def _signatures(name: str) -> tuple[frozenset[str], ...]:
    """Word stems that identify ``name``.

    A parenthesis naming a syndrome or disease is an alternative name:
    "Epileptic encephalopathy, early infantile, 6 (Dravet syndrome)" gives
    {epileptic, encefalopat} and {dravet}. Any other parenthesis qualifies the
    name ("(hepatic type)") and stays part of it. A name made only of generic
    words gives nothing and is never matched.
    """
    folded = _fold(name).lower()
    alternatives = [a for a in re.findall(r"\(([^()]*)\)", folded) if _NAMES_A_DISEASE.search(a)]
    main = re.sub(
        r"\(([^()]*)\)", lambda m: " " if m.group(1) in alternatives else m.group(1), folded
    )
    variants = [main, *alternatives]
    found = []
    for variant in variants:
        stems = frozenset(s for s in _stems(variant) if len(s) >= 3) - _GENERIC_STEMS
        if stems:
            found.append(stems)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class DiseaseName:
    name: str
    signatures: tuple[frozenset[str], ...]


class PatientVocabulary:
    """What patient text must never name: every gene of the knowledge base, and disease names.

    Build it once per process; ``for_diseases`` then gives the checker of one
    round, with the names of that round's top hypotheses.
    """

    def __init__(
        self, gene_symbols: Iterable[str], disease_name: Callable[[str], str | None]
    ) -> None:
        self._symbols = {s.lower(): s for s in gene_symbols if len(s) >= MIN_SYMBOL_CHARS}
        self._disease_name = disease_name
        self._names: dict[str, DiseaseName | None] = {}

    def for_diseases(self, disease_ids: Iterable[str]) -> PatientText:
        names = []
        for disease_id in dict.fromkeys(disease_ids):
            if disease_id not in self._names:
                name = self._disease_name(disease_id)
                signatures = _signatures(name) if name else ()
                self._names[disease_id] = DiseaseName(name, signatures) if name else None
            if (found := self._names[disease_id]) is not None and found.signatures:
                names.append(found)
        return PatientText(self, tuple(names))

    def genes_in(self, folded: str) -> list[str]:
        """Gene symbols in ``folded`` (see ``_fold``), in order of appearance."""
        named: dict[str, None] = {}
        for token in re.findall(r"[0-9A-Za-z]+(?:-[0-9A-Za-z]+)*", folded):
            parts = token.split("-")
            for start in range(len(parts)):
                for end in range(start + 1, len(parts) + 1):
                    written = "-".join(parts[start:end])
                    symbol = self._symbols.get(written.lower())
                    if symbol is None:
                        continue
                    if symbol in EVERYDAY_WORD_SYMBOLS and written != symbol:
                        continue
                    named[symbol] = None
        return list(named)


class PatientText:
    """Checks patient-facing text against the vocabulary and one round's disease names."""

    def __init__(self, vocabulary: PatientVocabulary, diseases: Iterable[DiseaseName] = ()) -> None:
        self.vocabulary = vocabulary
        # Each signature is filed under one of its stems, so a check only looks at the
        # few names that share a word with the text.
        self._by_stem: dict[str, list[tuple[str, frozenset[str]]]] = {}
        for disease in diseases:
            for signature in disease.signatures:
                self._by_stem.setdefault(min(signature), []).append((disease.name, signature))

    def diseases_in(self, stems: set[str]) -> list[str]:
        named = {
            name
            for stem in stems
            for name, signature in self._by_stem.get(stem, ())
            if signature <= stems
        }
        return sorted(named)

    def problems(
        self, text: str, *, exempt: Iterable[str] = (), forbidden: Iterable[str]
    ) -> list[str]:
        if not text:
            return []
        folded = _without_labels(_fold(text), exempt)
        lower = folded.lower()
        problems = []
        if genes := self.vocabulary.genes_in(folded):
            problems.append(f"nombra genes ({', '.join(genes)}) en texto para pacientes")
        if diseases := self.diseases_in(_stems(lower)):
            problems.append(f"nombra enfermedades ({'; '.join(diseases)}) en texto para pacientes")
        if _PERCENT.search(lower):
            problems.append("muestra porcentajes en texto para pacientes")
        words = sorted({m.group(0) for p in forbidden if (m := re.search(rf"\b{p}\w*", lower))})
        if words:
            problems.append(f"usa palabras no permitidas para pacientes ({', '.join(words)})")
        return problems


def _without_labels(folded: str, labels: Iterable[str]) -> str:
    """``folded`` with the exempt labels blanked out, also when quoted and cut short with "…"."""
    exempt = [f for label in labels if (f := _fold(label).strip())]
    if not exempt:
        return folded
    lowered = [label.lower() for label in exempt]

    def quoted(match: re.Match[str]) -> str:
        inner = match.group(1).strip().lower()
        # NFKC has already turned "…" into "...".
        cut = inner.removesuffix("...").rstrip()
        whole = inner in lowered
        shortened = bool(cut) and cut != inner and any(lab.startswith(cut) for lab in lowered)
        return " " if whole or shortened else match.group(0)

    folded = re.sub(r"«([^«»]*)»", quoted, folded)
    for label in sorted(exempt, key=len, reverse=True):
        folded = re.sub(rf"(?<!\w){re.escape(label)}(?!\w)", " ", folded, flags=re.IGNORECASE)
    return folded


@dataclass(frozen=True, slots=True)
class QuestionRules:
    """``labels`` maps each shortlisted term to its official labels, exempt in its own question."""

    mode: Mode
    shortlist: frozenset[str]
    already_known: frozenset[str]
    expected: int
    patient_text: PatientText
    labels: Mapping[str, tuple[str, ...]]


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
        problems = question_problems(question, rules)
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


def check_summary(
    draft: SummaryDraft, mode: Mode, patient_text: PatientText, exempt: Collection[str] = ()
) -> list[str]:
    """``exempt``: labels of the answered terms, which a summary may repeat."""
    text = draft.summary.strip()
    if not text:
        return ["el resumen está vacío"]
    problems = []
    if len(text) > MAX_SUMMARY_CHARS[mode]:
        problems.append(f"el resumen supera {MAX_SUMMARY_CHARS[mode]} caracteres")
    advice = _without_labels(_fold(text), exempt).lower()
    if words := sorted({m.group(0) for m in _ADVICE.finditer(advice)}):
        problems.append(
            f"menciona estudios o pasos a seguir ({', '.join(words)}): el resumen solo repasa"
            " qué se preguntó, qué se respondió y cómo cambió la lista; la etiqueta de un"
            " síntoma respondido se puede nombrar tal cual"
        )
    if mode is Mode.PATIENT:
        problems.extend(patient_text.problems(text, exempt=exempt, forbidden=_FORBIDDEN_IN_SUMMARY))
    return problems


def question_problems(question: QuestionDraft, rules: QuestionRules) -> list[str]:
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
        exempt = rules.labels.get(question.hpo_id, ())
        for part in (text, help_text):
            problems.extend(
                rules.patient_text.problems(part, exempt=exempt, forbidden=_FORBIDDEN_IN_QUESTIONS)
            )
    else:
        rationale = question.rationale.strip()
        if not rationale:
            problems.append("falta el fundamento para el médico")
        elif len(rationale) > MAX_RATIONALE_CHARS:
            problems.append(f"el fundamento supera {MAX_RATIONALE_CHARS} caracteres")
        if _NO_MOVES_GENES.search(rationale):
            problems.append(
                "el fundamento dice qué pasaría con un «no»: tiene que decir de qué genes"
                " es típico el síntoma, porque un «no» no cambia la lista"
            )
    return problems
