"""Build the configured LLM provider."""

from __future__ import annotations

from diagnosticar_preguntas.config import LLMProviderName, Settings
from diagnosticar_preguntas.llm.anthropic_api import AnthropicProvider
from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider
from diagnosticar_preguntas.llm.provider import LLMProvider
from diagnosticar_preguntas.llm.template import TemplateProvider


def build_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case LLMProviderName.CLAUDE_CLI:
            return ClaudeCliProvider(
                model=settings.llm_model,
                effort=settings.llm_effort,
                timeout_s=settings.llm_timeout_s,
            )
        case LLMProviderName.ANTHROPIC:
            return AnthropicProvider(
                model=settings.llm_model,
                effort=settings.llm_effort,
                timeout_s=settings.llm_timeout_s,
            )
        case LLMProviderName.TEMPLATE:
            return TemplateProvider()
