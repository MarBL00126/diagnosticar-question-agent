"""Composition root: the only place that knows which implementation backs each seam.

``build_service`` is called once per process, at startup. Loading the
knowledge base takes about a second and every later lookup is in memory, so
nothing here runs per request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from diagnosticar_preguntas.agent.guardrails import PatientVocabulary
from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.config import SelectorName, Settings
from diagnosticar_preguntas.domain import QuestionSelector
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.factory import build_provider
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.model_client import HttpModelClient
from diagnosticar_preguntas.ranking.reranker import BayesianReranker, RerankConfig
from diagnosticar_preguntas.selection.q2b import Q2bConfig, Q2bSelector
from diagnosticar_preguntas.selection.selector import InformationGainSelector


class RoundRunner(Protocol):
    def run(self, request: RoundRequest) -> RoundResponse: ...


class HealthProbe(Protocol):
    def is_healthy(self, timeout: float) -> bool: ...


@dataclass(frozen=True, slots=True)
class Service:
    """What the HTTP layer needs: something that runs rounds and what ``/healthz`` reports."""

    rounds: RoundRunner
    knowledge_base_version: str
    model_service: HealthProbe


def patient_vocabulary(kb: KnowledgeBase) -> PatientVocabulary:
    return PatientVocabulary(kb.genes(), kb.disease_name)


def rerank_config(settings: Settings) -> RerankConfig:
    return RerankConfig(
        answer_weighting=settings.answer_weighting, answer_weight=settings.answer_weight
    )


def build_selector(
    settings: Settings, kb: KnowledgeBase, likelihood: LikelihoodModel
) -> QuestionSelector:
    if settings.selector is SelectorName.LEGACY:
        return InformationGainSelector(kb, likelihood)
    return Q2bSelector(kb, likelihood, Q2bConfig(n_questions=settings.questions_per_round))


def build_service(settings: Settings) -> Service:
    """Wire the real components. Raises ``KnowledgeBaseError`` if the KB is missing or stale."""
    kb = KnowledgeBase.open(settings.kb_path)
    likelihood = LikelihoodModel(kb)
    model = HttpModelClient(settings.model_service_url, timeout=settings.model_service_timeout_s)
    rounds = RoundService(
        model=model,
        reranker=BayesianReranker(kb, likelihood, rerank_config(settings)),
        selector=build_selector(settings, kb, likelihood),
        labels=kb,
        llm=build_provider(settings),
        settings=settings,
        kb_version=kb.version,
        patient_vocabulary=patient_vocabulary(kb),
    )
    return Service(rounds=rounds, knowledge_base_version=kb.version, model_service=model)
