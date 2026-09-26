"""Stand-ins for the HTTP tests: stub rounds and probes, and the real pipeline on the tiny KB."""

from __future__ import annotations

import threading
from typing import Any

from fastapi.testclient import TestClient

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api.app import create_app
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse, Versions
from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.domain import ModelClient
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.template import TemplateProvider
from diagnosticar_preguntas.ranking.likelihood import LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
from diagnosticar_preguntas.selection.selector import InformationGainSelector
from diagnosticar_preguntas.wiring import Service, patient_vocabulary
from fixtures.fake_model import FakeModelClient
from fixtures.tiny_kb import GENE_A, GENE_B, GENE_C

TOKEN = "s3cret-token-for-tests-0123456789"
KB_VERSION = "kb-test+hpo.2026-02-16"

# Present findings and a note that must never reach a log line or an error body.
PRESENT = ["HP:0002205", "HP:0004322"]
ABSENT = ["HP:0001250"]
NOTE = "desde bebé, dice la abuela"


def make_settings(**overrides: Any) -> Settings:
    """Template wording and, unless a test says otherwise, no token required."""
    values: dict[str, Any] = {
        "llm_provider": LLMProviderName.TEMPLATE,
        "allow_unauthenticated": True,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def round_body(**overrides: Any) -> dict[str, Any]:
    return {
        "mode": "paciente",
        "round": 1,
        "present_hpo_ids": PRESENT,
        "absent_hpo_ids": ABSENT,
        "answers": [],
        "finish": False,
        **overrides,
    }


def finished(request: RoundRequest, warnings: tuple[str, ...] = ()) -> RoundResponse:
    return RoundResponse(
        round=request.round,
        mode=request.mode,
        status="finished",
        finish_reason="user_finished",
        ranking=[],
        warnings=list(warnings),
        versions=Versions(agent="test", knowledge_base=KB_VERSION, llm_model=None),
    )


class StubRounds:
    """Answers a finished round, raises ``error``, or holds each call until ``release``."""

    def __init__(
        self,
        *,
        error: BaseException | None = None,
        hold: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.error = error
        self.warnings = warnings
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.entered = threading.Semaphore(0)
        self.requests: list[RoundRequest] = []

    def run(self, request: RoundRequest) -> RoundResponse:
        self.requests.append(request)
        self.entered.release()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test never released the round")
        if self.error is not None:
            raise self.error
        return finished(request, self.warnings)


class StubProbe:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.timeouts: list[float] = []

    def is_healthy(self, timeout: float) -> bool:
        self.timeouts.append(timeout)
        return self.healthy


def stub_service(rounds: Any = None, probe: StubProbe | None = None) -> Service:
    return Service(
        rounds=rounds or StubRounds(),
        knowledge_base_version=KB_VERSION,
        model_service=probe or StubProbe(),
    )


def client(settings: Settings | None = None, service: Service | None = None) -> TestClient:
    return TestClient(create_app(settings or make_settings(), service or stub_service()))


def real_rounds(kb: KnowledgeBase, model: ModelClient | None = None) -> RoundService:
    """The real pipeline on the tiny knowledge base, with a fake model and template wording."""
    likelihood = LikelihoodModel(kb)
    return RoundService(
        model=model or FakeModelClient({GENE_A: 0.4, GENE_B: 0.35, GENE_C: 0.25}),
        reranker=BayesianReranker(kb, likelihood),
        selector=InformationGainSelector(kb, likelihood),
        labels=kb,
        llm=TemplateProvider(),
        settings=make_settings(),
        kb_version=kb.version,
        patient_vocabulary=patient_vocabulary(kb),
    )
