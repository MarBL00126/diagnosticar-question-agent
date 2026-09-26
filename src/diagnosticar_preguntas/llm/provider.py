"""The provider seam: one structured-output call, typed errors, content-free logging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from diagnosticar_preguntas.llm.tasks import Task

logger = logging.getLogger("diagnosticar_preguntas.llm")


@dataclass(frozen=True, slots=True)
class LLMRequest[T: BaseModel]:
    """A single structured-output call.

    ``system`` and ``user`` are the rendered prompts; ``output`` is the model
    the answer must validate against. ``task`` keeps the structured inputs the
    prompts were rendered from, so a provider that does not read prompts (the
    template provider) can build the same answer deterministically.
    """

    system: str
    user: str
    output: type[T]
    task: Task


class LLMProvider(Protocol):
    @property
    def model_name(self) -> str | None:
        """Model identifier reported in ``Versions.llm_model``; None when no LLM is used."""
        ...

    def generate[T: BaseModel](self, request: LLMRequest[T]) -> T:
        """Return a validated ``request.output`` instance or raise an ``LLMError``."""
        ...


class LLMError(Exception):
    """The provider could not produce a valid answer."""


class LLMTimeoutError(LLMError):
    pass


class LLMRefusalError(LLMError):
    pass


class LLMInvalidOutputError(LLMError):
    """The answer was truncated, not JSON, or failed schema validation."""


class LLMQuotaError(LLMError):
    """Usage quota or rate limit exhausted. Retrying immediately will not help."""


def log_call(*, provider: str, model: str, task: Task, elapsed_ms: float, outcome: str) -> None:
    # Prompts and answers carry clinical data, so only metadata is logged.
    logger.info(
        "llm call provider=%s model=%s task=%s elapsed_ms=%.0f outcome=%s",
        provider,
        model,
        type(task).__name__,
        elapsed_ms,
        outcome,
    )
