from __future__ import annotations

import pytest

from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.llm.anthropic_api import AnthropicProvider
from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider
from diagnosticar_preguntas.llm.factory import build_provider
from diagnosticar_preguntas.llm.template import TemplateProvider


def settings(provider: LLMProviderName) -> Settings:
    return Settings(_env_file=None, llm_provider=provider, llm_model="claude-opus-5")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (LLMProviderName.CLAUDE_CLI, ClaudeCliProvider),
        (LLMProviderName.ANTHROPIC, AnthropicProvider),
        (LLMProviderName.TEMPLATE, TemplateProvider),
    ],
)
def test_builds_the_configured_provider(
    name: LLMProviderName, expected: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")

    provider = build_provider(settings(name))

    assert isinstance(provider, expected)


def test_llm_providers_report_the_model_and_the_template_none() -> None:
    assert build_provider(settings(LLMProviderName.CLAUDE_CLI)).model_name == "claude-opus-5"
    assert build_provider(settings(LLMProviderName.TEMPLATE)).model_name is None
