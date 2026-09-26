from __future__ import annotations

from typing import Any

import pytest

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.api.health import MODEL_HEALTH_TIMEOUT_S
from diagnosticar_preguntas.config import LLMProviderName
from fixtures.http_stubs import KB_VERSION, StubProbe, client, make_settings, stub_service

pytestmark = pytest.mark.usefixtures("no_llm_credentials")


def healthz(probe: StubProbe | None = None, **settings: Any) -> dict[str, Any]:
    response = client(make_settings(**settings), stub_service(probe=probe)).get("/healthz")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_the_template_provider_is_healthy_without_credentials() -> None:
    assert healthz() == {
        "status": "ok",
        "agent_version": __version__,
        "knowledge_base": KB_VERSION,
        "llm": {"provider": "template", "model": None, "credentials": "ok"},
        "model_service": "ok",
    }


def test_missing_claude_credentials_degrade_but_do_not_fail() -> None:
    body = healthz(llm_provider=LLMProviderName.CLAUDE_CLI)

    assert body["status"] == "degraded"
    assert body["llm"] == {
        "provider": "claude-cli",
        "model": "claude-opus-5",
        "credentials": "missing",
    }


def test_an_oauth_token_counts_as_claude_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-ant-oat01-not-a-real-token"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", secret)
    api = client(make_settings(llm_provider=LLMProviderName.CLAUDE_CLI))

    response = api.get("/healthz")

    assert response.json()["status"] == "ok"
    assert response.json()["llm"]["credentials"] == "ok"
    assert secret not in response.text


@pytest.mark.parametrize(("key", "expected"), [("sk-ant-api-x", "ok"), (None, "missing")])
def test_the_api_provider_needs_an_api_key(
    key: str | None, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if key is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)

    body = healthz(llm_provider=LLMProviderName.ANTHROPIC)

    assert body["llm"]["credentials"] == expected


def test_an_unreachable_model_service_is_reported_not_raised() -> None:
    body = healthz(StubProbe(healthy=False))

    assert body["status"] == "degraded"
    assert body["model_service"] == "unreachable"


def test_the_model_check_uses_a_short_timeout() -> None:
    probe = StubProbe()

    healthz(probe)

    assert probe.timeouts == [MODEL_HEALTH_TIMEOUT_S]
    assert MODEL_HEALTH_TIMEOUT_S <= 5


def test_warns_at_startup_when_llm_credentials_are_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client(make_settings(llm_provider=LLMProviderName.CLAUDE_CLI))

    assert "no credentials for LLM provider claude-cli" in caplog.text
