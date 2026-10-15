"""Runtime settings, read from ``PREGUNTAS_*`` environment variables (or ``.env``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from diagnosticar_preguntas.api.schemas import MAX_ROUNDS, QUESTIONS_PER_ROUND
from diagnosticar_preguntas.ranking.reranker import AnswerWeighting, RerankConfig
from diagnosticar_preguntas.ranking.strategy import RerankStrategy

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LLMProviderName(StrEnum):
    CLAUDE_CLI = "claude-cli"
    ANTHROPIC = "anthropic"
    TEMPLATE = "template"


class SelectorName(StrEnum):
    Q2B = "q2b"
    LEGACY = "legacy"


class Settings(BaseSettings):
    """Every tunable of the service.

    ``confident_threshold`` is compared with the top gene's posterior score,
    which sums to 1 over the ranked genes. ``min_information_gain`` is in bits:
    when even the best candidate symptom would move the posterior less than
    that, asking more questions only costs the family time. Both defaults come
    from the evaluation in ``docs/EVALUACION.md``. ``rerank_strategy`` and
    ``answer_weighting`` decide what the answers do: by default every "sí" is
    sent to the model and nothing else moves the ranking, which is the model's
    own; "no" answers and negated findings are only recorded
    (``docs/DECISIONES.md``, D-18). ``answer_weight`` only applies to the
    asymmetric weighting.

    With ``rerank_negations`` every round also sends the effective "no"
    answers to the model as ``excluded_hpo_ids``, so that a model service with
    the re-ranker lowers the genes that annotate them. The current service
    ignores the field (its request schema is a pydantic model with the default
    ``extra="ignore"``), so leaving it on changes nothing until the service is
    upgraded. It only applies with ``MODEL_SEES_PRESENT`` and
    ``AnswerWeighting.NONE``: with any other combination the answers already
    go through this agent's own re-ranker, and sending them as well would
    count them twice (``docs/NEGACIONES.md``, section 6).

    ``selector`` picks how the questions are chosen: ``q2b`` (default) asks
    ``questions_per_round`` terms by expected information gain over the top 10
    genes of the ranking with a diversity penalty, as validated in EXP-Q-001
    (``docs/DECISIONES.md``, D-19); ``legacy`` is the previous selector, kept
    for rollback.

    A round's LLM calls must end within ``round_budget_s`` of its start: each
    call gets at most ``llm_timeout_s`` and never more than what is left, and
    the round falls back to templates when too little is left for a call. The
    call to the model service is bounded separately by
    ``model_service_timeout_s``. A round can also wait ``queue_timeout_s`` for
    a slot first, so the caller's timeout must exceed
    ``queue_timeout_s + round_budget_s + model_service_timeout_s`` (95 s with
    the defaults; the platform waits 100 s).

    At most ``max_concurrent_rounds`` rounds run at once and at most
    ``max_queued_rounds`` more wait; beyond that the API answers 503 instead
    of piling up work the caller will have abandoned.

    Without ``service_token`` the API refuses every round, unless
    ``allow_unauthenticated`` is set, which is only meant for local
    development.
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
    model_service_timeout_s: float = Field(default=10.0, gt=0)
    kb_path: Path = Path("data/kb.sqlite")

    llm_provider: LLMProviderName = LLMProviderName.CLAUDE_CLI
    llm_model: str = "claude-opus-5"
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_effort: Effort = "high"
    # Measured in docs/EVALUACION_ESFUERZO.md: medium drafts questions as well and
    # 24% faster, but reads notes less cautiously and summarises worse.
    llm_question_effort: Effort = "medium"

    shortlist_size: int = Field(default=8, ge=1)
    questions_per_round: int = Field(default=QUESTIONS_PER_ROUND, ge=1, le=QUESTIONS_PER_ROUND)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=MAX_ROUNDS)
    confident_threshold: float = Field(default=0.9, gt=0, le=1)
    min_information_gain: float = Field(default=0.05, ge=0)
    rerank_strategy: RerankStrategy = RerankStrategy.MODEL_SEES_PRESENT
    answer_weighting: AnswerWeighting = RerankConfig().answer_weighting
    answer_weight: float = Field(default=RerankConfig().answer_weight, ge=0, lt=1)
    rerank_negations: bool = True
    selector: SelectorName = SelectorName.Q2B
    round_budget_s: float = Field(default=75.0, gt=0)

    host: str = "0.0.0.0"
    port: int = Field(default=8010, ge=1, le=65535)
    service_token: SecretStr | None = None
    allow_unauthenticated: bool = False
    max_concurrent_rounds: int = Field(default=2, ge=1)
    max_queued_rounds: int = Field(default=4, ge=0)
    queue_timeout_s: float = Field(default=10.0, gt=0)
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
