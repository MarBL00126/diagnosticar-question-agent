"""Composition root: the only place that knows which implementation backs each seam.

``build_service`` is called once per process, at startup. Loading the
knowledge base takes about a second and every later lookup is in memory, so
nothing here runs per request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.config import Settings
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.factory import build_provider
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.model_client import HttpModelClient
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
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


def build_service(settings: Settings) -> Service:
    """Wire the real components. Raises ``KnowledgeBaseError`` if the KB is missing or stale."""
    kb = KnowledgeBase.open(settings.kb_path)
    likelihood = LikelihoodModel(kb)
    model = HttpModelClient(settings.model_service_url, timeout=settings.model_service_timeout_s)
    rounds = RoundService(
        model=model,
        reranker=BayesianReranker(kb, likelihood),
        selector=InformationGainSelector(kb, likelihood),
        labels=kb,
        llm=build_provider(settings),
        settings=settings,
    )
    return Service(rounds=rounds, knowledge_base_version=kb.version, model_service=model)
