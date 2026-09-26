"""One refinement round: interpret answers, re-rank, then ask or close.

The service is stateless; every request carries the whole session. The LLM
never blocks a round: any failure, and any call that would not fit in what is
left of the round's time budget, degrades to the template provider and adds a
warning. Only a model-service failure propagates, because without a ranking
there is nothing to refine.

Logs carry exception types and counts only, never prompts, notes or LLM
output. The API logs the one line per round.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.agent.evidence import build_evidence, interpret_answers, needs_reading
from diagnosticar_preguntas.agent.guardrails import (
    PatientText,
    PatientVocabulary,
    QuestionRules,
    RoundCheck,
    check_round,
    check_summary,
)
from diagnosticar_preguntas.api.schemas import (
    MAX_GENES_PER_SIDE,
    FinishReason,
    InterpretedAnswer,
    QuestionOut,
    RankedGene,
    RoundRequest,
    RoundResponse,
    Versions,
)
from diagnosticar_preguntas.config import Settings
from diagnosticar_preguntas.domain import (
    CandidateTerm,
    Evidence,
    Mode,
    ModelClient,
    ModelRanking,
    Posterior,
    QuestionSelector,
    Reranker,
    TermLabels,
)
from diagnosticar_preguntas.llm.prompting import notes_request, question_request, summary_request
from diagnosticar_preguntas.llm.provider import LLMError, LLMInvalidOutputError, LLMProvider
from diagnosticar_preguntas.llm.schemas import NoteReading, QuestionDraft
from diagnosticar_preguntas.llm.tasks import (
    AnsweredTerm,
    LabeledTerm,
    NoteItem,
    NotesTask,
    QuestionTask,
    SummaryTask,
)
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.strategy import model_query

logger = logging.getLogger(__name__)

RANKING_TOP_K = 50
PROMPT_TOP_GENES = 10
SUMMARY_TOP_GENES = 5
LLM_ATTEMPTS = 2
# No LLM call starts with less time than this left in the round's budget.
MIN_LLM_CALL_S = 10.0

WARNING_NOTES = "No pudimos interpretar las notas; se usaron las respuestas tal como se marcaron."
WARNING_QUESTIONS = "Algunas preguntas se generaron sin IA."
WARNING_SUMMARY = "El resumen se generó sin IA."
LLM_FALLBACK_WARNINGS = frozenset({WARNING_NOTES, WARNING_QUESTIONS, WARNING_SUMMARY})

_SCHEMA_VIOLATION = "la respuesta no respetó el esquema JSON"


@dataclass(frozen=True, slots=True)
class Deadline:
    """When the round's LLM calls must be over, on ``clock``'s time line."""

    expires_at: float
    clock: Callable[[], float]

    def remaining(self) -> float:
        return max(0.0, self.expires_at - self.clock())


@dataclass(slots=True)
class _Round:
    """Per-request bookkeeping: the deadline, the warnings, and whether any LLM output was used."""

    deadline: Deadline
    llm_enabled: bool
    warnings: list[str] = field(default_factory=list)
    used_llm: bool = False

    def fell_back(self, warning: str) -> None:
        # The template provider is not a fallback: running without an LLM warns nobody.
        if self.llm_enabled and warning not in self.warnings:
            self.warnings.append(warning)


class RoundService:
    """Runs rounds. ``kb_version`` is reported in ``versions.knowledge_base``.

    ``patient_vocabulary`` holds the genes and disease names patient text may
    not contain; ``clock`` exists for tests of the time budget.
    """

    def __init__(
        self,
        *,
        model: ModelClient,
        reranker: Reranker,
        selector: QuestionSelector,
        labels: TermLabels,
        llm: LLMProvider,
        settings: Settings,
        kb_version: str,
        patient_vocabulary: PatientVocabulary,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._reranker = reranker
        self._selector = selector
        self._labels = labels
        self._llm = llm
        self._settings = settings
        self._kb_version = kb_version
        self._vocabulary = patient_vocabulary
        self._clock = clock
        self._template = TemplateProvider()

    def run(self, request: RoundRequest) -> RoundResponse:
        state = _Round(
            deadline=Deadline(self._clock() + self._settings.round_budget_s, self._clock),
            llm_enabled=self._llm.model_name is not None,
        )
        interpreted = self._interpret(request, state)
        evidence = build_evidence(request, interpreted)
        query = model_query(
            self._settings.rerank_strategy,
            reported_present=request.present_hpo_ids,
            evidence=evidence,
        )
        ranking = self._model.rank(query, top_k=RANKING_TOP_K)
        posterior = self._reranker.rerank(ranking, evidence)
        patient_text = self._vocabulary.for_diseases(_top_diseases(posterior))

        finish_reason, candidates = self._next_step(request, posterior, evidence)
        questions: list[QuestionOut] = []
        summary = None
        if finish_reason is None:
            questions = self._questions(
                request, posterior, evidence, candidates, patient_text, state
            )
        else:
            summary = self._summary(
                request.mode, finish_reason, interpreted, posterior, ranking, patient_text, state
            )

        return RoundResponse(
            round=request.round,
            mode=request.mode,
            status="questions" if finish_reason is None else "finished",
            finish_reason=finish_reason,
            questions=questions,
            ranking=[
                RankedGene(
                    gene_id=g.gene_symbol,
                    gene_symbol=g.gene_symbol,
                    score=g.score,
                    model_score=g.model_score,
                    model_rank=g.model_rank,
                )
                for g in posterior.top(RANKING_TOP_K)
            ],
            # Straight from the model call, which (with the default strategy) only saw the
            # findings reported before any question: see RoundResponse.
            specialties=dict(ranking.specialties) if ranking.specialties is not None else None,
            confidence=ranking.confidence,
            interpreted_answers=interpreted,
            summary=summary,
            warnings=state.warnings,
            versions=Versions(
                agent=__version__,
                knowledge_base=self._kb_version,
                llm_model=self._llm.model_name if state.used_llm else None,
            ),
        )

    def _call_timeout(self, state: _Round, reserve: float = 0.0) -> float | None:
        """Timeout for the next LLM call, keeping ``reserve`` seconds for later calls.

        None when less than ``MIN_LLM_CALL_S`` would be left for it.
        """
        timeout = min(self._settings.llm_timeout_s, state.deadline.remaining() - reserve)
        return timeout if timeout >= MIN_LLM_CALL_S else None

    def _interpret(self, request: RoundRequest, state: _Round) -> list[InterpretedAnswer]:
        items = tuple(
            NoteItem(a.hpo_id, self._label(a.hpo_id), a.answer, a.note or "")
            for a in request.answers
            if needs_reading(a)
        )
        readings: dict[str, NoteReading] = {}
        if not items:
            return interpret_answers(request.answers, readings)
        # Leave room for the question or summary call that follows.
        timeout = self._call_timeout(state, reserve=MIN_LLM_CALL_S)
        if timeout is None:
            logger.warning("round budget too short to read the notes; keeping the buttons")
            state.fell_back(WARNING_NOTES)
            return interpret_answers(request.answers, readings)
        try:
            draft = self._llm.generate(
                notes_request(NotesTask(request.mode, items)), timeout_s=timeout
            )
        except LLMError as exc:
            logger.warning(
                "note interpretation failed (%s); keeping the buttons", type(exc).__name__
            )
            state.fell_back(WARNING_NOTES)
        else:
            noted = {i.hpo_id for i in items}
            readings = {r.hpo_id: r for r in draft.readings if r.hpo_id in noted}
            state.used_llm |= state.llm_enabled and bool(readings)
        return interpret_answers(request.answers, readings)

    def _next_step(
        self, request: RoundRequest, posterior: Posterior, evidence: Evidence
    ) -> tuple[FinishReason | None, list[CandidateTerm]]:
        settings = self._settings
        if request.finish:
            return "user_finished", []
        if request.round > settings.max_rounds:
            return "max_rounds", []
        if posterior.genes and posterior.genes[0].score >= settings.confident_threshold:
            return "confident", []
        shortlist = self._selector.shortlist(posterior, evidence, k=settings.shortlist_size)
        if not shortlist or max(c.information_gain for c in shortlist) < (
            settings.min_information_gain
        ):
            return "no_useful_questions", []
        return None, self._for_mode(shortlist, request.mode)

    def _for_mode(self, shortlist: list[CandidateTerm], mode: Mode) -> list[CandidateTerm]:
        """In patient mode, keep study-only findings just to reach a full round."""
        if mode is Mode.CLINICIAN:
            return shortlist
        answerable = [c for c in shortlist if not c.requires_testing]
        missing = self._settings.questions_per_round - len(answerable)
        if missing <= 0:
            return answerable
        return answerable + [c for c in shortlist if c.requires_testing][:missing]

    def _questions(
        self,
        request: RoundRequest,
        posterior: Posterior,
        evidence: Evidence,
        candidates: list[CandidateTerm],
        patient_text: PatientText,
        state: _Round,
    ) -> list[QuestionOut]:
        n = min(self._settings.questions_per_round, len(candidates))
        task = QuestionTask(
            mode=request.mode,
            round=request.round,
            n_questions=n,
            candidates=tuple(candidates),
            top_genes=posterior.top(PROMPT_TOP_GENES),
            present=self._labeled(evidence.present),
            absent=self._labeled(evidence.absent),
            unknown=self._labeled(evidence.unknown),
        )
        rules = QuestionRules(
            mode=request.mode,
            shortlist=frozenset(c.hpo_id for c in candidates),
            already_known=evidence.asked_or_known,
            expected=n,
            patient_text=patient_text,
            labels={c.hpo_id: (c.label_es, c.label_en) for c in candidates},
        )
        accepted = list(self._draft_questions(task, rules, state))
        state.used_llm |= state.llm_enabled and bool(accepted)
        if len(accepted) < n:
            chosen = {q.hpo_id for q in accepted}
            template = self._template.generate(
                question_request(
                    replace(
                        task,
                        candidates=tuple(c for c in candidates if c.hpo_id not in chosen),
                        n_questions=n - len(accepted),
                    )
                )
            )
            accepted.extend(template.questions)
            state.fell_back(WARNING_QUESTIONS)
        by_id = {c.hpo_id: c for c in candidates}
        return [self._question_out(q, by_id[q.hpo_id], request) for q in accepted]

    def _draft_questions(
        self, task: QuestionTask, rules: QuestionRules, state: _Round
    ) -> tuple[QuestionDraft, ...]:
        """Ask the LLM, retrying once with the violations; return the questions that passed."""
        check = RoundCheck(accepted=(), violations=())
        for _ in range(LLM_ATTEMPTS):
            timeout = self._call_timeout(state)
            if timeout is None:
                logger.warning("round budget too short for a question draft; using templates")
                break
            try:
                draft = self._llm.generate(question_request(task), timeout_s=timeout)
            except LLMInvalidOutputError:
                logger.warning("question draft did not match the schema")
                check = RoundCheck((), (_SCHEMA_VIOLATION,))
            except LLMError as exc:
                logger.warning("question drafting failed (%s); using templates", type(exc).__name__)
                return ()
            else:
                check = check_round(draft, rules)
                if not check.violations:
                    return check.accepted
            logger.info("question draft rejected: %d violations", len(check.violations))
            task = replace(task, corrections=check.violations)
        return check.accepted

    def _question_out(
        self, draft: QuestionDraft, candidate: CandidateTerm, request: RoundRequest
    ) -> QuestionOut:
        clinician = request.mode is Mode.CLINICIAN
        return QuestionOut(
            id=f"r{request.round}-{draft.hpo_id}",
            hpo_id=draft.hpo_id,
            label=candidate.label_es,
            text=draft.text.strip(),
            help=(draft.help or "").strip() or None,
            genes_if_yes=list(candidate.genes_yes[:MAX_GENES_PER_SIDE]) if clinician else None,
            genes_if_no=list(candidate.genes_no[:MAX_GENES_PER_SIDE]) if clinician else None,
            rationale=draft.rationale.strip() if clinician else None,
        )

    def _summary(
        self,
        mode: Mode,
        finish_reason: FinishReason,
        interpreted: Sequence[InterpretedAnswer],
        posterior: Posterior,
        ranking: ModelRanking,
        patient_text: PatientText,
        state: _Round,
    ) -> str:
        task = SummaryTask(
            mode=mode,
            finish_reason=finish_reason,
            answers=tuple(
                AnsweredTerm(a.hpo_id, self._label(a.hpo_id), a.effective_answer, a.note_summary)
                for a in interpreted
            ),
            top_genes=posterior.top(SUMMARY_TOP_GENES),
            specialties=_specialty_names(ranking),
        )
        answered_labels = [a.label for a in task.answers]
        for _ in range(LLM_ATTEMPTS):
            timeout = self._call_timeout(state)
            if timeout is None:
                logger.warning("round budget too short for the summary; using the template")
                break
            try:
                draft = self._llm.generate(summary_request(task), timeout_s=timeout)
            except LLMInvalidOutputError:
                logger.warning("summary draft did not match the schema")
                problems = [_SCHEMA_VIOLATION]
            except LLMError as exc:
                logger.warning("summary failed (%s); using the template", type(exc).__name__)
                break
            else:
                problems = check_summary(draft, mode, patient_text, exempt=answered_labels)
                if not problems:
                    state.used_llm |= state.llm_enabled
                    return draft.summary.strip()
            logger.info("summary draft rejected: %d violations", len(problems))
            task = replace(task, corrections=tuple(problems))
        state.fell_back(WARNING_SUMMARY)
        return self._template.generate(summary_request(task)).summary

    def _label(self, hpo_id: str) -> str:
        return self._labels.label_es(hpo_id) or self._labels.label_en(hpo_id) or hpo_id

    def _labeled(self, hpo_ids: Iterable[str]) -> tuple[LabeledTerm, ...]:
        return tuple(LabeledTerm(h, self._label(h)) for h in sorted(hpo_ids))


def _top_diseases(posterior: Posterior) -> list[str]:
    """Diseases behind the genes the LLM is shown: what it could name if it slipped."""
    top = {g.gene_symbol for g in posterior.top(PROMPT_TOP_GENES)}
    return [h.disease_id for h in posterior.hypotheses if h.gene_symbol in top]


def _specialty_names(ranking: ModelRanking) -> tuple[str, ...]:
    top = (ranking.specialties or {}).get("top_specialties")
    if not isinstance(top, list):
        return ()
    return tuple(s for s in top if isinstance(s, str))
