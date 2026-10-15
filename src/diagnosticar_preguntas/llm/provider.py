"""The provider seam: one structured-output call, typed errors, content-free logging."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from diagnosticar_preguntas.config import Effort
from diagnosticar_preguntas.llm.tasks import QuestionTask, Task

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

    def generate[T: BaseModel](
        self, request: LLMRequest[T], *, timeout_s: float | None = None
    ) -> T:
        """Return a validated ``request.output`` instance or raise an ``LLMError``.

        ``timeout_s`` caps this call below the provider's own timeout; the
        round service passes what is left of the round's budget.
        """
        ...


class LLMError(Exception):
    """The provider could not produce a valid answer.

    Messages are generic and never quote the prompt or the answer, which carry
    clinical data; callers log the exception type only.
    """


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


def effort_for(task: Task, default: Effort, question_effort: Effort | None) -> Effort:
    """Effort for one call: question drafting may run lighter than notes and summaries."""
    if question_effort is not None and isinstance(task, QuestionTask):
        return question_effort
    return default
