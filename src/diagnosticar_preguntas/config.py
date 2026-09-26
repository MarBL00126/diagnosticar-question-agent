"""Runtime settings, read from ``PREGUNTAS_*`` environment variables (or ``.env``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from diagnosticar_preguntas.api.schemas import MAX_ROUNDS, QUESTIONS_PER_ROUND

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMProviderName(StrEnum):
    CLAUDE_CLI = "claude-cli"
    ANTHROPIC = "anthropic"
    TEMPLATE = "template"


class Settings(BaseSettings):
    """Every tunable of the service.

    ``confident_threshold`` is compared with the top gene's posterior score,
    which sums to 1 over the ranked genes. ``min_information_gain`` is in bits:
    when even the best candidate symptom would move the posterior less than
    that, asking more questions only costs the family time.

    Every round spends LLM quota and holds a worker thread for up to a few
    minutes, so at most ``max_concurrent_rounds`` run at once and at most
    ``max_queued_rounds`` more wait, each for up to ``queue_timeout_s``; beyond
    that the API answers 503 instead of piling up work the caller will have
    abandoned. Without ``service_token`` the API accepts unauthenticated calls,
    which is only meant for local development.
    """

    model_config = SettingsConfigDict(
        env_prefix="PREGUNTAS_",
        env_file=".env",
        # Compose passes unset variables as empty strings (``${X:-}``); treat them as unset.
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    model_service_url: str = "http://localhost:8000"
    model_service_timeout_s: float = Field(default=30.0, gt=0)
    kb_path: Path = Path("data/kb.sqlite")

    llm_provider: LLMProviderName = LLMProviderName.CLAUDE_CLI
    llm_model: str = "claude-opus-5"
    llm_timeout_s: float = Field(default=120.0, gt=0)
    llm_effort: Effort = "high"

    shortlist_size: int = Field(default=8, ge=1)
    questions_per_round: int = Field(default=QUESTIONS_PER_ROUND, ge=1, le=QUESTIONS_PER_ROUND)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=MAX_ROUNDS)
    confident_threshold: float = Field(default=0.8, gt=0, le=1)
    min_information_gain: float = Field(default=0.01, ge=0)

    host: str = "0.0.0.0"
    port: int = Field(default=8010, ge=1, le=65535)
    service_token: SecretStr | None = None
    max_concurrent_rounds: int = Field(default=2, ge=1)
    max_queued_rounds: int = Field(default=4, ge=0)
    queue_timeout_s: float = Field(default=30.0, gt=0)
    log_level: str = "INFO"

    @field_validator("service_token", mode="before")
    @classmethod
    def _blank_token_means_none(cls, value: object) -> object:
        # A blank token would otherwise be a valid one that any empty header matches.
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
