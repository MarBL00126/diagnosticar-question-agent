"""One refinement round: interpret answers, re-rank, then ask or close.

The service is stateless; every request carries the whole session. The LLM
never blocks a round: any failure degrades to the template provider and adds a
warning. Only a model-service failure (``ModelUnavailableError``) propagates,
because without a ranking there is nothing to refine.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import replace

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.agent.evidence import build_evidence, interpret_answers
from diagnosticar_preguntas.agent.guardrails import (
    QuestionRules,
    RoundCheck,
    check_round,
    check_summary,
)
from diagnosticar_preguntas.api.schemas import (
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

logger = logging.getLogger(__name__)

RANKING_TOP_K = 50
MAX_SEPARATES = 5
PROMPT_TOP_GENES = 10
SUMMARY_TOP_GENES = 5
LLM_ATTEMPTS = 2

WARNING_NOTES = "No pudimos interpretar las notas; se usaron las respuestas tal como se marcaron."
WARNING_QUESTIONS = "Algunas preguntas se generaron sin IA."
WARNING_SUMMARY = "El resumen se generó sin IA."


class RoundService:
    def __init__(
        self,
        *,
        model: ModelClient,
        reranker: Reranker,
        selector: QuestionSelector,
        labels: TermLabels,
        llm: LLMProvider,
        settings: Settings,
    ) -> None:
        self._model = model
        self._reranker = reranker
        self._selector = selector
        self._labels = labels
        self._llm = llm
        self._settings = settings
        self._template = TemplateProvider()

    def run(self, request: RoundRequest) -> RoundResponse:
        warnings: list[str] = []
        interpreted = self._interpret(request, warnings)
        evidence = build_evidence(request, interpreted)
        # The model only sees present findings; absences and answers are applied by the reranker.
        ranking = self._model.rank(sorted(evidence.present), top_k=RANKING_TOP_K)
        posterior = self._reranker.rerank(ranking, evidence)
        gene_symbols = frozenset(g.gene_symbol for g in posterior.top(RANKING_TOP_K))

        finish_reason, candidates = self._next_step(request, posterior, evidence)
        questions: list[QuestionOut] = []
        summary = None
        if finish_reason is None:
            questions = self._questions(
                request, posterior, evidence, candidates, gene_symbols, warnings
            )
        else:
            summary = self._summary(
                request.mode, finish_reason, interpreted, posterior, ranking, gene_symbols, warnings
            )

        logger.info(
            "round=%d mode=%s finish_reason=%s questions=%d warnings=%d",
            request.round,
            request.mode.value,
            finish_reason,
            len(questions),
            len(warnings),
        )
        return RoundResponse(
            round=request.round,
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
            # The model ranks from present findings only, so specialties and its
            # confidence reflect the "sí" answers, never the "no" ones.
            specialties=dict(ranking.specialties) if ranking.specialties is not None else None,
            confidence=ranking.confidence,
            interpreted_answers=interpreted,
            summary=summary,
            warnings=warnings,
            versions=Versions(
                agent=__version__,
                knowledge_base=_kb_version(self._labels),
                llm_model=self._llm.model_name,
            ),
        )

    def _interpret(self, request: RoundRequest, warnings: list[str]) -> list[InterpretedAnswer]:
        items = tuple(
            NoteItem(a.hpo_id, self._label(a.hpo_id), a.answer, a.note)
            for a in request.answers
            if a.note
        )
        readings: dict[str, NoteReading] = {}
        if items:
            try:
                draft = self._llm.generate(notes_request(NotesTask(request.mode, items)))
                noted = {i.hpo_id for i in items}
                readings = {r.hpo_id: r for r in draft.readings if r.hpo_id in noted}
            except LLMError:
                logger.warning("note interpretation failed; keeping the buttons", exc_info=True)
                warnings.append(WARNING_NOTES)
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
        shortlist = [
            c
            for c in self._selector.shortlist(posterior, evidence, k=settings.shortlist_size)
            if c.hpo_id not in evidence.asked_or_known
        ]
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
        gene_symbols: frozenset[str],
        warnings: list[str],
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
            gene_symbols=gene_symbols,
            expected=n,
        )
        accepted = list(self._draft_questions(task, rules))
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
            if self._llm.model_name is not None:
                warnings.append(WARNING_QUESTIONS)
        by_id = {c.hpo_id: c for c in candidates}
        return [self._question_out(q, by_id[q.hpo_id], request) for q in accepted]

    def _draft_questions(
        self, task: QuestionTask, rules: QuestionRules
    ) -> tuple[QuestionDraft, ...]:
        """Ask the LLM, retrying once with the violations; return the questions that passed."""
        check = RoundCheck(accepted=(), violations=())
        for _ in range(LLM_ATTEMPTS):
            try:
                draft = self._llm.generate(question_request(task))
            except LLMInvalidOutputError:
                logger.warning("question draft was not valid JSON for the schema")
                check = RoundCheck((), ("la respuesta no respetó el esquema JSON",))
            except LLMError:
                logger.warning("question drafting failed; using templates", exc_info=True)
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
        separates = list(dict.fromkeys((*candidate.genes_yes, *candidate.genes_no)))
        return QuestionOut(
            id=f"r{request.round}-{draft.hpo_id}",
            hpo_id=draft.hpo_id,
            text=draft.text.strip(),
            help=(draft.help or "").strip() or None,
            separates=separates[:MAX_SEPARATES] if clinician else None,
            rationale=draft.rationale.strip() if clinician else None,
        )

    def _summary(
        self,
        mode: Mode,
        finish_reason: FinishReason,
        interpreted: Sequence[InterpretedAnswer],
        posterior: Posterior,
        ranking: ModelRanking,
        gene_symbols: Collection[str],
        warnings: list[str],
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
        for _ in range(LLM_ATTEMPTS):
            try:
                draft = self._llm.generate(summary_request(task))
            except LLMInvalidOutputError:
                problems = ["la respuesta no respetó el esquema JSON"]
            except LLMError:
                logger.warning("summary failed; using the template", exc_info=True)
                break
            else:
                problems = check_summary(draft, mode, gene_symbols)
                if not problems:
                    return draft.summary.strip()
            logger.info("summary draft rejected: %d violations", len(problems))
            task = replace(task, corrections=tuple(problems))
        if self._llm.model_name is not None:
            warnings.append(WARNING_SUMMARY)
        return self._template.generate(summary_request(task)).summary

    def _label(self, hpo_id: str) -> str:
        return self._labels.label_es(hpo_id) or self._labels.label_en(hpo_id) or hpo_id

    def _labeled(self, hpo_ids: Iterable[str]) -> tuple[LabeledTerm, ...]:
        return tuple(LabeledTerm(h, self._label(h)) for h in sorted(hpo_ids))


def _specialty_names(ranking: ModelRanking) -> tuple[str, ...]:
    top = (ranking.specialties or {}).get("top_specialties")
    if not isinstance(top, list):
        return ()
    return tuple(s for s in top if isinstance(s, str))


def _kb_version(labels: TermLabels) -> str:
    version = getattr(labels, "version", None)
    return version if isinstance(version, str) and version else "unknown"
