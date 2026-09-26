"""Question policies and the loop that plays one simulated session.

Every question policy runs through the real ``RoundService`` with the template
provider (no LLM): only the ``QuestionSelector`` changes, so re-ranking, stop
rules, the patient-mode filter and the evidence handling are identical.

* ``none``: no questions; the model's own ranking of the reported findings.
* ``agent``: the information-gain selector; the template provider asks the
  top of its diversified shortlist.
* ``random_askable``: random terms from the same candidate pool the selector
  scores (askable, near the candidates' annotations, not implied by known
  answers), never two related terms in a round.
* ``most_frequent_unreported``: what the results screen suggests today: the
  most frequent annotated findings of the current top gene, then the next
  gene, that are not reported yet.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from eval.answerer import AnswererConfig, SimulatedPatient
from eval.cases import Case
from eval.harness import TOP_K, Pipeline, settings_for, stable_seed

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import AnswerIn, RoundRequest, RoundResponse
from diagnosticar_preguntas.domain import (
    CandidateTerm,
    Evidence,
    Mode,
    ModelClient,
    ModelRanking,
    Posterior,
    QuestionSelector,
)
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.provider import LLMProvider
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from diagnosticar_preguntas.selection.selector import InformationGainSelector

POLICIES = ("none", "agent", "random_askable", "most_frequent_unreported")
QUESTION_POLICIES = POLICIES[1:]
MISSING_FREQUENCY_ORDER = 0.5
# Baselines carry no information gain; this keeps the "no useful questions" rule out of their way.
BASELINE_GAIN = 1.0


class RecordingSelector:
    """Passes shortlists through and records the best gain of each, for post-hoc stop rules."""

    def __init__(self, inner: QuestionSelector) -> None:
        self._inner = inner
        self.max_gains: list[float | None] = []
        self.shortlists: list[list[CandidateTerm]] = []

    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        result = self._inner.shortlist(posterior, evidence, k)
        self.shortlists.append(result)
        self.max_gains.append(max((c.information_gain for c in result), default=None))
        return result


class _BaselineSelector:
    def __init__(self, kb: KnowledgeBase, selector: InformationGainSelector) -> None:
        self._kb = kb
        self._selector = selector

    def _pool(self, posterior: Posterior, evidence: Evidence) -> list[str]:
        """The information-gain selector's own candidate pool, for a like-for-like comparison."""
        likelihood = self._selector._likelihood
        profiles = [likelihood.profile(h.disease_id) for h in posterior.hypotheses]
        return self._selector._candidates(profiles, evidence)

    def _related(self, first: str, second: str) -> bool:
        return first in self._kb.ancestors(second) or second in self._kb.ancestors(first)

    def _unrelated_prefix(self, ordered: Sequence[str], k: int) -> list[str]:
        picked: list[str] = []
        for term in ordered:
            if len(picked) == k:
                break
            if not any(self._related(term, p) for p in picked):
                picked.append(term)
        return picked

    def _candidate(self, hpo_id: str) -> CandidateTerm:
        term = self._kb.term(hpo_id)
        if term is None:
            raise KeyError(hpo_id)
        return CandidateTerm(
            hpo_id=hpo_id,
            label_es=term.label_es or term.label_en,
            label_en=term.label_en,
            information_gain=BASELINE_GAIN,
            p_yes=0.0,
            requires_testing=term.requires_testing,
        )


class RandomAskableSelector(_BaselineSelector):
    def __init__(self, kb: KnowledgeBase, selector: InformationGainSelector, seed: int) -> None:
        super().__init__(kb, selector)
        self._rng = random.Random(seed)

    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        pool = self._pool(posterior, evidence)
        self._rng.shuffle(pool)
        return [self._candidate(t) for t in self._unrelated_prefix(pool, k)]


class MostFrequentSelector(_BaselineSelector):
    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        allowed = set(self._pool(posterior, evidence))
        diseases_by_gene: dict[str, list[str]] = {}
        for hypothesis in posterior.hypotheses:
            diseases_by_gene.setdefault(hypothesis.gene_symbol, []).append(hypothesis.disease_id)
        ordered: list[str] = []
        seen: set[str] = set()
        for gene in posterior.genes:
            frequency: dict[str, float] = {}
            for disease in diseases_by_gene.get(gene.gene_symbol, ()):
                for a in self._kb.annotations(disease):
                    if a.negated or a.hpo_id not in allowed:
                        continue
                    value = MISSING_FREQUENCY_ORDER if a.frequency is None else a.frequency
                    frequency[a.hpo_id] = max(frequency.get(a.hpo_id, 0.0), value)
            for term in sorted(frequency, key=lambda t: (-frequency[t], t)):
                if term not in seen:
                    seen.add(term)
                    ordered.append(term)
            if len(ordered) >= 4 * k:
                break
        return [self._candidate(t) for t in self._unrelated_prefix(ordered, k)]


class FixedPriorModel:
    """Sends only the findings reported before any question, whatever the round."""

    def __init__(self, inner: ModelClient, reported_present: Sequence[str]) -> None:
        self._inner = inner
        self._query = tuple(sorted(set(reported_present)))

    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        return self._inner.rank(self._query, top_k)


@dataclass(slots=True)
class RoundTrace:
    """One request to the service. ``round`` is the request's 1-based round number."""

    round: int
    true_rank: int | None
    top_score: float
    max_gain: float | None
    finish_reason: str | None
    asked: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass(slots=True)
class CaseTrace:
    case_id: str
    policy: str
    rounds: list[RoundTrace]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaseTrace:
        return cls(
            case_id=data["case_id"],
            policy=data["policy"],
            rounds=[RoundTrace(**r) for r in data["rounds"]],
            warnings=list(data.get("warnings", [])),
        )


def true_rank(genes: Sequence[str], gene: str) -> int | None:
    try:
        return genes.index(gene) + 1
    except ValueError:
        return None


def model_only(case: Case, model: ModelClient) -> CaseTrace:
    started = time.perf_counter()
    ranking = model.rank(sorted(case.present), TOP_K)
    ordered = sorted(ranking.scores, key=lambda g: -ranking.scores[g])
    top = ranking.scores[ordered[0]] if ordered else 0.0
    trace = RoundTrace(
        round=1,
        true_rank=true_rank(ordered, case.gene),
        top_score=top,
        max_gain=None,
        finish_reason="no_questions",
        elapsed_s=time.perf_counter() - started,
    )
    return CaseTrace(case.case_id, "none", [trace])


def selector_for(policy: str, pipeline: Pipeline, case: Case) -> QuestionSelector:
    match policy:
        case "agent":
            return pipeline.selector
        case "random_askable":
            return RandomAskableSelector(
                pipeline.kb, pipeline.selector, stable_seed("random", case.case_id)
            )
        case "most_frequent_unreported":
            return MostFrequentSelector(pipeline.kb, pipeline.selector)
    raise ValueError(f"unknown policy {policy!r}")


def run_case(
    policy: str,
    case: Case,
    pipeline: Pipeline,
    model: ModelClient,
    answerer: AnswererConfig,
    *,
    mode: Mode = Mode.PATIENT,
    early_stop: bool = False,
    llm: LLMProvider | None = None,
    max_request_rounds: int = 4,
) -> CaseTrace:
    if policy == "none":
        return model_only(case, model)
    if pipeline.config.strategy is RerankStrategy.FIXED_PRIOR:
        model = FixedPriorModel(model, case.present)
    selector = RecordingSelector(selector_for(policy, pipeline, case))
    service = RoundService(
        model=model,
        reranker=pipeline.reranker,
        selector=selector,
        labels=pipeline.kb,
        llm=llm or TemplateProvider(),
        settings=settings_for(pipeline.config, early_stop=early_stop),
    )
    patient = SimulatedPatient(pipeline.kb, case, answerer, mode)
    answers: list[AnswerIn] = []
    trace = CaseTrace(case.case_id, policy, [])
    for round_number in range(1, max_request_rounds + 1):
        request = RoundRequest(
            mode=mode,
            round=round_number,
            present_hpo_ids=list(case.present),
            absent_hpo_ids=list(case.absent),
            answers=answers,
        )
        shortlists_before = len(selector.max_gains)
        started = time.perf_counter()
        response = service.run(request)
        elapsed = time.perf_counter() - started
        record = _record(response, case, selector, shortlists_before, elapsed)
        trace.rounds.append(record)
        trace.warnings.extend(response.warnings)
        if response.status == "finished":
            break
        new = [
            AnswerIn(hpo_id=q.hpo_id, answer=patient.answer(q.hpo_id)) for q in response.questions
        ]
        record.asked = [a.hpo_id for a in new]
        record.answers = [a.answer.value for a in new]
        answers = [*answers, *new]
    return trace


def _record(
    response: RoundResponse,
    case: Case,
    selector: RecordingSelector,
    shortlists_before: int,
    elapsed: float,
) -> RoundTrace:
    genes = [g.gene_symbol for g in response.ranking]
    new_gain = len(selector.max_gains) > shortlists_before
    return RoundTrace(
        round=response.round,
        true_rank=true_rank(genes, case.gene),
        top_score=response.ranking[0].score if response.ranking else 0.0,
        max_gain=selector.max_gains[-1] if new_gain else None,
        finish_reason=response.finish_reason,
        elapsed_s=elapsed,
    )
