from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from llm_fakes import VALID_ROUND, round_request

from diagnosticar_preguntas.llm.anthropic_api import FALLBACK_BETA, AnthropicProvider
from diagnosticar_preguntas.llm.provider import (
    LLMError,
    LLMInvalidOutputError,
    LLMQuotaError,
    LLMRefusalError,
    LLMTimeoutError,
)

API_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def response(stop_reason: str = "end_turn", text: str | None = None) -> SimpleNamespace:
    blocks = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        blocks.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=blocks, _request_id="req_test")


class FakeClient:
    def __init__(self, result: SimpleNamespace | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def provider(client: FakeClient) -> AnthropicProvider:
    return AnthropicProvider(model="claude-opus-5", effort="medium", timeout_s=30, client=client)  # type: ignore[arg-type]


def ok_client() -> FakeClient:
    return FakeClient(response(text=json.dumps(VALID_ROUND)))


class TestRequest:
    def test_sends_model_adaptive_thinking_effort_and_schema(self) -> None:
        client = ok_client()

        provider(client).generate(round_request())

        call = client.calls[0]
        assert call["model"] == "claude-opus-5"
        assert call["thinking"] == {"type": "adaptive"}
        assert call["output_config"]["effort"] == "medium"
        output_format = call["output_config"]["format"]
        assert output_format["type"] == "json_schema"
        assert output_format["schema"]["required"] == ["questions"]

    def test_caches_the_system_prompt_and_sends_case_data_as_the_user_turn(self) -> None:
        client = ok_client()
        request = round_request()

        provider(client).generate(request)

        call = client.calls[0]
        assert call["system"] == [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ]
        assert call["messages"] == [{"role": "user", "content": request.user}]

    def test_opts_into_default_server_side_fallbacks(self) -> None:
        client = ok_client()

        provider(client).generate(round_request())

        assert client.calls[0]["fallbacks"] == "default"
        assert FALLBACK_BETA in client.calls[0]["betas"]


class TestResponse:
    def test_returns_the_validated_answer_from_the_text_block(self) -> None:
        draft = provider(ok_client()).generate(round_request())

        assert draft.questions[0].hpo_id == "HP:0001738"

    def test_a_refusal_raises_refusal_before_reading_content(self) -> None:
        with pytest.raises(LLMRefusalError):
            provider(FakeClient(response("refusal"))).generate(round_request())

    @pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
    def test_a_truncated_answer_is_invalid(self, stop_reason: str) -> None:
        client = FakeClient(response(stop_reason, text='{"questions": ['))

        with pytest.raises(LLMInvalidOutputError):
            provider(client).generate(round_request())

    def test_text_that_breaks_the_schema_is_invalid(self) -> None:
        client = FakeClient(response(text='{"preguntas": []}'))

        with pytest.raises(LLMInvalidOutputError):
            provider(client).generate(round_request())


class TestErrors:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (anthropic.APITimeoutError(request=API_REQUEST), LLMTimeoutError),
            (
                anthropic.RateLimitError(
                    "rate limited", response=httpx2.Response(429, request=API_REQUEST), body=None
                ),
                LLMQuotaError,
            ),
            (
                anthropic.InternalServerError(
                    "overloaded", response=httpx2.Response(529, request=API_REQUEST), body=None
                ),
                LLMError,
            ),
            (anthropic.APIConnectionError(request=API_REQUEST), LLMError),
        ],
    )
    def test_sdk_errors_map_to_typed_llm_errors(
        self, error: Exception, expected: type[LLMError]
    ) -> None:
        with pytest.raises(expected) as caught:
            provider(FakeClient(error)).generate(round_request())

        assert caught.value.__cause__ is error
