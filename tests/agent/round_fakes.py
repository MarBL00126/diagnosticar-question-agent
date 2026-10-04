"""In-memory stand-ins for the collaborators of ``RoundService``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel

from diagnosticar_preguntas.agent.guardrails import PatientVocabulary
from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import RoundRequest
from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.domain import (
    CandidateTerm,
    Evidence,
    GeneScore,
    Hypothesis,
    Mode,
    ModelRanking,
    ModelUnavailableError,
    Posterior,
)
from diagnosticar_preguntas.llm.provider import LLMProvider, LLMRequest
from diagnosticar_preguntas.llm.schemas import (
    NoteReading,
    NotesDraft,
    QuestionDraft,
    RoundDraft,
    SummaryDraft,
)
from diagnosticar_preguntas.llm.tasks import NotesTask, QuestionTask, SummaryTask
from diagnosticar_preguntas.llm.template import TemplateProvider

PRESENT = ["HP:0002205", "HP:0004322"]
ABSENT = ["HP:0001250"]

LABELS = {
    "HP:0002205": "Infecciones respiratorias recurrentes",
    "HP:0004322": "Talla baja",
    "HP:0001250": "Convulsiones",
    "HP:0001738": "Insuficiencia pancreática exocrina",
    "HP:0001696": "Situs inversus",
    "HP:0012236": "Test del sudor alterado",
    "HP:0002099": "Asma",
    "HP:0000765": "Tórax anormal",
}

GENES = (
    GeneScore("CFTR", 0.5, 0.41, 1),
    GeneScore("DNAH5", 0.3, 0.20, 2),
    GeneScore("SCNN1A", 0.2, 0.09, 3),
)
HYPOTHESES = (
    Hypothesis("CFTR", "OMIM:219700", 0.5),
    Hypothesis("DNAH5", "OMIM:608644", 0.3),
    Hypothesis("SCNN1A", "OMIM:264350", 0.2),
)
DISEASE_NAMES = {
    "OMIM:219700": "Cystic fibrosis",
    "OMIM:608644": "Ciliary dyskinesia, primary, 3",
    "OMIM:264350": "Pseudohypoaldosteronism, type I, autosomal recessive",
}
KB_VERSION = "kb-test-1"
# Every gene the fake knowledge base knows, beyond the three candidates of the ranking.
ALL_GENES = ("CFTR", "DNAH5", "SCNN1A", "SCN1A", "POR", "HLA-B")


def candidate(
    hpo_id: str,
    information_gain: float = 0.3,
    *,
    requires_testing: bool = False,
    genes_yes: tuple[str, ...] = ("CFTR",),
    genes_no: tuple[str, ...] = ("DNAH5",),
) -> CandidateTerm:
    return CandidateTerm(
        hpo_id=hpo_id,
        label_es=LABELS.get(hpo_id, hpo_id),
        label_en=hpo_id,
        information_gain=information_gain,
        p_yes=0.4,
        genes_yes=genes_yes,
        genes_no=genes_no,
        requires_testing=requires_testing,
    )


SHORTLIST = [
    candidate("HP:0001738", 0.5),
    candidate("HP:0001696", 0.4, genes_yes=("DNAH5",), genes_no=("CFTR", "SCNN1A")),
    candidate("HP:0002099", 0.3),
    candidate("HP:0000765", 0.2),
]


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"llm_provider": LLMProviderName.TEMPLATE, **overrides}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def request(**overrides: Any) -> RoundRequest:
    values: dict[str, Any] = {
        "mode": Mode.CLINICIAN,
        "round": 1,
        "present_hpo_ids": PRESENT,
        "absent_hpo_ids": ABSENT,
        **overrides,
    }
    return RoundRequest.model_validate(values)


@dataclass
class FakeModel:
    ranking: ModelRanking = field(
        default_factory=lambda: ModelRanking(
            scores={g.gene_symbol: g.model_score for g in GENES},
            specialties={"top_specialties": ["Pulmonology", "Gastroenterology"]},
            confidence=0.41,
        )
    )
    fail: bool = False
    calls: list[tuple[list[str], int]] = field(default_factory=list)
    excluded: list[list[str]] = field(default_factory=list)

    def rank(
        self, hpo_ids: Sequence[str], top_k: int, excluded_hpo_ids: Sequence[str] = ()
    ) -> ModelRanking:
        self.calls.append((list(hpo_ids), top_k))
        self.excluded.append(list(excluded_hpo_ids))
        if self.fail:
            raise ModelUnavailableError("model service down")
        return replace(self.ranking, query_hpo_ids=tuple(hpo_ids))


@dataclass
class FakeReranker:
    genes: tuple[GeneScore, ...] = GENES
    hypotheses: tuple[Hypothesis, ...] = HYPOTHESES
    evidence: list[Evidence] = field(default_factory=list)
    rankings: list[ModelRanking] = field(default_factory=list)

    def rerank(self, ranking: ModelRanking, evidence: Evidence) -> Posterior:
        self.evidence.append(evidence)
        self.rankings.append(ranking)
        return Posterior(genes=self.genes, hypotheses=self.hypotheses)


@dataclass
class FakeSelector:
    candidates: list[CandidateTerm] = field(default_factory=lambda: list(SHORTLIST))
    calls: list[int] = field(default_factory=list)

    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        self.calls.append(k)
        return self.candidates[:k]


class FakeLabels:
    def label_es(self, hpo_id: str) -> str | None:
        return LABELS.get(hpo_id)

    def label_en(self, hpo_id: str) -> str | None:
        return None


class FakeClock:
    """A monotonic clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def vocabulary() -> PatientVocabulary:
    return PatientVocabulary(ALL_GENES, DISEASE_NAMES.get)


def service(
    llm: LLMProvider | None = None,
    *,
    model: FakeModel | None = None,
    reranker: FakeReranker | None = None,
    selector: FakeSelector | None = None,
    clock: FakeClock | None = None,
    **config: Any,
) -> RoundService:
    return RoundService(
        model=model or FakeModel(),
        reranker=reranker or FakeReranker(),
        selector=selector or FakeSelector(),
        labels=FakeLabels(),
        llm=llm or TemplateProvider(),
        settings=settings(**config),
        kb_version=KB_VERSION,
        patient_vocabulary=vocabulary(),
        clock=clock or getattr(llm, "clock", None) or FakeClock(),
    )


class ScriptedLLM:
    """Replays scripted answers per task kind; an ``Exception`` entry is raised instead."""

    model_name = "claude-opus-5"

    def __init__(
        self,
        *,
        questions: Sequence[BaseModel | Exception] = (),
        notes: Sequence[BaseModel | Exception] = (),
        summaries: Sequence[BaseModel | Exception] = (),
        clock: FakeClock | None = None,
        seconds_per_call: float = 0.0,
    ) -> None:
        self._script: dict[type, list[BaseModel | Exception]] = {
            QuestionTask: list(questions),
            NotesTask: list(notes),
            SummaryTask: list(summaries),
        }
        self.requests: list[LLMRequest[Any]] = []
        self.timeouts: list[float | None] = []
        self.clock = clock or FakeClock()
        self.seconds_per_call = seconds_per_call

    def generate(self, request: LLMRequest[Any], *, timeout_s: float | None = None) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout_s)
        self.clock.advance(self.seconds_per_call)
        queue = self._script[type(request.task)]
        assert queue, f"unexpected {type(request.task).__name__} call"
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, request.output)
        return item

    def tasks(self, kind: type) -> list[Any]:
        return [r.task for r in self.requests if isinstance(r.task, kind)]


def question(
    hpo_id: str, text: str | None = None, rationale: str = "Separa CFTR de DNAH5."
) -> QuestionDraft:
    return QuestionDraft(
        hpo_id=hpo_id,
        text=text or f"¿Presenta {LABELS.get(hpo_id, hpo_id).lower()}?",
        help=None,
        rationale=rationale,
    )


def round_draft(*hpo_ids: str) -> RoundDraft:
    return RoundDraft(questions=[question(h) for h in hpo_ids])


def notes(*readings: tuple[str, bool, str | None]) -> NotesDraft:
    return NotesDraft(
        readings=[NoteReading(hpo_id=h, consistent=c, summary=s) for h, c, s in readings]
    )


def summary(
    text: str = "Con tus respuestas se ordenó la lista. Conversalo con el médico.",
) -> SummaryDraft:
    return SummaryDraft(summary=text)
