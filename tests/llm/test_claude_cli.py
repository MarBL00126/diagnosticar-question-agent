from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from llm_fakes import VALID_ROUND, round_request

from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider
from diagnosticar_preguntas.llm.provider import (
    LLMError,
    LLMInvalidOutputError,
    LLMQuotaError,
    LLMRefusalError,
    LLMTimeoutError,
)
from diagnosticar_preguntas.llm.schemas import RoundDraft


def envelope(**overrides: Any) -> str:
    """Shape of ``claude -p --output-format json`` as observed on Claude Code 2.1.270."""
    body: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "tool_use",
        "api_error_status": None,
        "result": json.dumps(VALID_ROUND),
        "structured_output": VALID_ROUND,
        **overrides,
    }
    return json.dumps(body)


class FakeRunner:
    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: BaseException | None = None,
    ) -> None:
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result


def provider(runner: FakeRunner, **overrides: Any) -> ClaudeCliProvider:
    values: dict[str, Any] = {
        "model": "claude-opus-5",
        "effort": "high",
        "timeout_s": 42,
        "binary": "/usr/bin/claude",
        "runner": runner,
        **overrides,
    }
    return ClaudeCliProvider(**values)


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


class TestInvocation:
    def test_builds_an_argument_list_with_model_rules_and_schema(self) -> None:
        runner = FakeRunner(stdout=envelope())
        request = round_request()

        provider(runner).generate(request)

        argv, _ = runner.calls[0]
        assert argv[:2] == ["/usr/bin/claude", "-p"]
        assert flag(argv, "--model") == "claude-opus-5"
        assert flag(argv, "--effort") == "high"
        assert flag(argv, "--append-system-prompt") == request.system
        assert flag(argv, "--output-format") == "json"
        assert flag(argv, "--tools") == ""
        schema = json.loads(flag(argv, "--json-schema"))
        assert schema["required"] == ["questions"]
        assert "--no-session-persistence" in argv

    def test_the_case_data_goes_on_stdin_never_in_the_arguments(self) -> None:
        runner = FakeRunner(stdout=envelope())
        request = round_request()

        provider(runner).generate(request)

        argv, kwargs = runner.calls[0]
        assert kwargs["input"] == request.user
        assert "stdin" not in kwargs
        arguments = "\n".join(argv)
        assert request.user not in arguments
        for case_data in ("Infecciones respiratorias recurrentes", "HP:0001738", "Situs inversus"):
            assert case_data in request.user
            assert case_data not in arguments

    def test_runs_without_shell_and_with_a_hard_timeout(self) -> None:
        runner = FakeRunner(stdout=envelope())

        provider(runner).generate(round_request())

        _, kwargs = runner.calls[0]
        assert kwargs["timeout"] == 42
        assert kwargs.get("shell", False) is False
        assert kwargs["capture_output"] is True

    def test_binary_comes_from_claude_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_BINARY", "/opt/claude/bin/claude")
        runner = FakeRunner(stdout=envelope())

        provider(runner, binary=None).generate(round_request())

        assert runner.calls[0][0][0] == "/opt/claude/bin/claude"

    def test_a_missing_binary_is_an_llm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_BINARY", raising=False)
        monkeypatch.setenv("PATH", "")

        with pytest.raises(LLMError, match="CLAUDE_BINARY"):
            provider(FakeRunner(), binary=None).generate(round_request())

    def test_a_binary_that_cannot_start_is_an_llm_error(self) -> None:
        runner = FakeRunner(raises=FileNotFoundError(2, "No such file or directory"))

        with pytest.raises(LLMError):
            provider(runner).generate(round_request())


class TestParsing:
    def test_returns_the_validated_structured_output(self) -> None:
        draft = provider(FakeRunner(stdout=envelope())).generate(round_request())

        assert isinstance(draft, RoundDraft)
        assert draft.questions[0].hpo_id == "HP:0001738"

    def test_falls_back_to_the_result_text_without_structured_output(self) -> None:
        runner = FakeRunner(stdout=envelope(structured_output=None))

        draft = provider(runner).generate(round_request())

        assert draft.questions[0].text == "¿Presenta esteatorrea?"

    def test_output_that_breaks_the_schema_is_invalid(self) -> None:
        runner = FakeRunner(stdout=envelope(structured_output={"questions": [{"hpo_id": 1}]}))

        with pytest.raises(LLMInvalidOutputError):
            provider(runner).generate(round_request())

    def test_non_json_stdout_is_invalid(self) -> None:
        with pytest.raises(LLMInvalidOutputError):
            provider(FakeRunner(stdout="Hola!")).generate(round_request())

    def test_a_non_success_subtype_is_invalid(self) -> None:
        runner = FakeRunner(stdout=envelope(subtype="error_max_structured_output_retries"))

        with pytest.raises(LLMInvalidOutputError):
            provider(runner).generate(round_request())

    def test_a_refusal_raises_refusal(self) -> None:
        runner = FakeRunner(stdout=envelope(stop_reason="refusal", structured_output=None))

        with pytest.raises(LLMRefusalError):
            provider(runner).generate(round_request())


class TestFailures:
    def test_a_timeout_raises_timeout(self) -> None:
        runner = FakeRunner(raises=subprocess.TimeoutExpired(["claude"], 42))

        with pytest.raises(LLMTimeoutError):
            provider(runner).generate(round_request())

    @pytest.mark.parametrize(
        "message",
        [
            "Claude AI usage limit reached|1790000000",
            "You've hit your limit · resets 3pm (America/Buenos_Aires)",
            "API Error: 429 rate_limit_error",
        ],
    )
    def test_a_spent_quota_raises_quota(self, message: str) -> None:
        runner = FakeRunner(
            stdout=envelope(is_error=True, result=message, structured_output=None), returncode=1
        )

        with pytest.raises(LLMQuotaError):
            provider(runner).generate(round_request())

    def test_a_429_status_raises_quota(self) -> None:
        runner = FakeRunner(
            stdout=envelope(is_error=True, api_error_status=429, result="", structured_output=None)
        )

        with pytest.raises(LLMQuotaError):
            provider(runner).generate(round_request())

    def test_a_quota_message_on_stderr_raises_quota(self) -> None:
        runner = FakeRunner(stderr="Error: usage limit reached", returncode=1)

        with pytest.raises(LLMQuotaError):
            provider(runner).generate(round_request())

    def test_a_nonzero_exit_is_a_plain_llm_error(self) -> None:
        runner = FakeRunner(
            stdout=envelope(is_error=True, subtype="error_during_execution", result="boom"),
            returncode=1,
        )

        with pytest.raises(LLMError) as caught:
            provider(runner).generate(round_request())

        assert type(caught.value) is LLMError

    def test_quota_words_in_a_successful_answer_are_not_a_quota_error(self) -> None:
        answer = {
            "questions": [
                {
                    "hpo_id": "HP:0001738",
                    "text": "¿Llegó al límite?",
                    "help": None,
                    "rationale": "rate limit",
                }
            ]
        }
        runner = FakeRunner(stdout=envelope(structured_output=answer))

        assert provider(runner).generate(round_request()).questions[0].rationale == "rate limit"


def test_logs_carry_no_prompt_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    request = round_request()

    provider(FakeRunner(stdout=envelope())).generate(request)

    assert "claude-opus-5" in caplog.text
    assert "Insuficiencia" not in caplog.text
    assert "HP:0001738" not in caplog.text
