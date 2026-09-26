"""Provider backed by the Claude API through the official ``anthropic`` SDK.

Structured output uses ``output_config.format`` with a JSON schema generated
from the pydantic model, on the beta endpoint so the request can opt into
server-side refusal fallbacks (``fallbacks="default"``). ``messages.parse()``
is not used: it validates the text inside the HTTP call, so a refused or
truncated answer would surface as a pydantic error before ``stop_reason``
could be inspected.
"""

from __future__ import annotations

import logging
import time

import anthropic
from anthropic import transform_schema
from pydantic import BaseModel, ValidationError

from diagnosticar_preguntas.config import Effort
from diagnosticar_preguntas.llm.provider import (
    LLMError,
    LLMInvalidOutputError,
    LLMQuotaError,
    LLMRefusalError,
    LLMRequest,
    LLMTimeoutError,
    log_call,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
STRUCTURED_OUTPUTS_BETA = "structured-outputs-2025-12-15"
# Adaptive thinking shares this cap with the answer, so it leaves ample room.
DEFAULT_MAX_TOKENS = 16_000


class AnthropicProvider:
    def __init__(
        self,
        *,
        model: str,
        effort: Effort,
        timeout_s: float,
        client: anthropic.Anthropic | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._timeout_s = timeout_s
        # The round service falls back to templates on its own; SDK retries would only
        # spend the round's time budget.
        self._client = client or anthropic.Anthropic(timeout=timeout_s, max_retries=0)

    @property
    def model_name(self) -> str | None:
        return self._model

    def generate[T: BaseModel](
        self, request: LLMRequest[T], *, timeout_s: float | None = None
    ) -> T:
        started = time.monotonic()
        outcome = "error"
        timeout = self._timeout_s if timeout_s is None else min(self._timeout_s, timeout_s)
        try:
            result = self._generate(request, timeout)
            outcome = "ok"
            return result
        except LLMError as exc:
            outcome = type(exc).__name__
            raise
        finally:
            log_call(
                provider="anthropic",
                model=self._model,
                task=request.task,
                elapsed_ms=(time.monotonic() - started) * 1000,
                outcome=outcome,
            )

    def _generate[T: BaseModel](self, request: LLMRequest[T], timeout: float) -> T:
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": request.user}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": transform_schema(request.output)},
                },
                fallbacks="default",
                betas=[STRUCTURED_OUTPUTS_BETA, FALLBACK_BETA],
                timeout=timeout,
            )
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Claude API request timed out") from exc
        except anthropic.RateLimitError as exc:
            raise LLMQuotaError("Claude API rate limit reached") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API returned {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the Claude API") from exc

        stop_reason = response.stop_reason
        logger.debug("claude api request_id=%s stop_reason=%s", response._request_id, stop_reason)
        if stop_reason == "refusal":
            raise LLMRefusalError("the model and its fallback declined the request")
        if stop_reason in ("max_tokens", "model_context_window_exceeded"):
            raise LLMInvalidOutputError(f"answer truncated ({stop_reason})")
        text = "".join(block.text for block in response.content if block.type == "text")
        return _validate(text, request.output)


def _validate[T: BaseModel](text: str, output: type[T]) -> T:
    try:
        return output.model_validate_json(text)
    except ValidationError as exc:
        # ``from None``: the validation error quotes the answer, which may echo a note.
        raise LLMInvalidOutputError(
            f"answer does not match {output.__name__}: {exc.error_count()} errors"
        ) from None
