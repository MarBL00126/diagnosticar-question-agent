"""``GET /healthz``: what this instance can do right now.

The endpoint answers 200 whenever the process can serve HTTP, and reports
dependency problems in ``status`` instead of failing:

* LLM credentials missing: rounds still work, with template questions and
  summaries (``RoundService`` falls back on every LLM error), so the instance
  is ``degraded``, not down.
* Model service unreachable: rounds answer 503 until it comes back, but
  restarting this container would not help, so an orchestrator must not treat
  it as a failed liveness check either.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.llm.credentials import has_credentials
from diagnosticar_preguntas.wiring import Service

MODEL_HEALTH_TIMEOUT_S = 2.0


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMHealth(_Out):
    provider: LLMProviderName
    model: str | None
    credentials: Literal["ok", "missing"]


class Health(_Out):
    status: Literal["ok", "degraded"]
    agent_version: str
    knowledge_base: str
    llm: LLMHealth
    model_service: Literal["ok", "unreachable"]


def llm_health(settings: Settings) -> LLMHealth:
    template = settings.llm_provider is LLMProviderName.TEMPLATE
    return LLMHealth(
        provider=settings.llm_provider,
        model=None if template else settings.llm_model,
        credentials="ok" if has_credentials(settings.llm_provider) else "missing",
    )


def check_health(settings: Settings, service: Service) -> Health:
    llm = llm_health(settings)
    model_ok = service.model_service.is_healthy(timeout=MODEL_HEALTH_TIMEOUT_S)
    return Health(
        status="ok" if model_ok and llm.credentials == "ok" else "degraded",
        agent_version=__version__,
        knowledge_base=service.knowledge_base_version,
        llm=llm,
        model_service="ok" if model_ok else "unreachable",
    )
