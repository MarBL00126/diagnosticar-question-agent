"""Provider backed by the Claude Code CLI (``claude -p``).

Runs on a Claude subscription: locally with the user's login, in a container
with ``CLAUDE_CODE_OAUTH_TOKEN``.

The user prompt, which carries the case data, goes to the CLI on stdin: any
account on the host can read another process's arguments from
``/proc/<pid>/cmdline``. Only the system prompt (static rules) and the output
schema (derived from the pydantic models) are passed as arguments.

Structured output comes from ``--json-schema`` with ``--output-format json``,
which prints one envelope::

    {"type": "result", "subtype": "success", "is_error": false,
     "result": "<the JSON as text>", "structured_output": {...}, ...}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from typing import Any

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

Runner = Callable[..., subprocess.CompletedProcess[str]]

_QUOTA_PATTERN = re.compile(
    r"usage limit|limit reached|hit your limit|rate[ _-]?limit|quota|out of (extra )?usage"
    r"|credit balance is too low",
    re.IGNORECASE,
)


class ClaudeCliProvider:
    def __init__(
        self,
        *,
        model: str,
        effort: Effort,
        timeout_s: float,
        binary: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self._model = model
        self._effort = effort
        self._timeout_s = timeout_s
        self._binary = binary or os.environ.get("CLAUDE_BINARY") or shutil.which("claude")
        self._runner = runner

    @property
    def model_name(self) -> str | None:
        return self._model

    def build_argv(self, request: LLMRequest[Any]) -> list[str]:
        if self._binary is None:
            raise LLMError("claude CLI not found; install Claude Code or set CLAUDE_BINARY")
        schema = json.dumps(transform_schema(request.output), ensure_ascii=False)
        return [
            self._binary,
            "-p",
            "--model",
            self._model,
            "--effort",
            self._effort,
            "--append-system-prompt",
            request.system,
            "--output-format",
            "json",
            "--json-schema",
            schema,
            # The answer only needs the prompt; no tool may touch the host.
            "--tools",
            "",
            "--no-session-persistence",
        ]

    def generate[T: BaseModel](
        self, request: LLMRequest[T], *, timeout_s: float | None = None
    ) -> T:
        argv = self.build_argv(request)
        timeout = self._timeout_s if timeout_s is None else min(self._timeout_s, timeout_s)
        started = time.monotonic()
        outcome = "error"
        try:
            completed = self._runner(
                argv,
                # ``input`` writes the prompt and closes stdin, so the CLI never waits on it.
                input=request.user,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                # Keeps any project CLAUDE.md in the service's directory out of the context.
                cwd=tempfile.gettempdir(),
            )
            result = _parse(completed, request.output)
            outcome = "ok"
            return result
        except subprocess.TimeoutExpired:
            outcome = "timeout"
            # ``from None``: the expired process carries whatever the CLI printed so far.
            raise LLMTimeoutError(f"claude CLI exceeded {timeout:.0f}s") from None
        except OSError as exc:
            raise LLMError(f"could not start the claude CLI: {exc.strerror}") from exc
        except LLMError as exc:
            outcome = type(exc).__name__
            raise
        finally:
            log_call(
                provider="claude-cli",
                model=self._model,
                task=request.task,
                elapsed_ms=(time.monotonic() - started) * 1000,
                outcome=outcome,
            )


def _parse[T: BaseModel](completed: subprocess.CompletedProcess[str], output: type[T]) -> T:
    envelope = _envelope(completed.stdout)
    if completed.returncode != 0 or envelope is None or envelope.get("is_error") is True:
        detail = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        if (envelope or {}).get("api_error_status") == 429 or _QUOTA_PATTERN.search(detail):
            raise LLMQuotaError("Claude subscription quota or rate limit reached")
        if envelope is None:
            raise LLMInvalidOutputError(
                f"claude CLI exited {completed.returncode} without a JSON result"
            )
        raise LLMError(
            f"claude CLI failed (exit {completed.returncode}, subtype {envelope.get('subtype')})"
        )
    if envelope.get("stop_reason") == "refusal":
        raise LLMRefusalError("the model declined the request")
    if envelope.get("subtype") != "success":
        raise LLMInvalidOutputError(f"claude CLI result subtype {envelope.get('subtype')}")
    payload = envelope.get("structured_output")
    try:
        if payload is None:
            return output.model_validate_json(str(envelope.get("result", "")))
        return output.model_validate(payload)
    except ValidationError as exc:
        # ``from None``: the validation error quotes the answer, which may echo a note.
        raise LLMInvalidOutputError(
            f"answer does not match {output.__name__}: {exc.error_count()} errors"
        ) from None


def _envelope(stdout: str | None) -> dict[str, Any] | None:
    if not stdout or not stdout.strip():
        return None
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and value.get("type") == "result":
        return value
    return None
