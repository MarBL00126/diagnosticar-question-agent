from __future__ import annotations

import pytest

from diagnosticar_preguntas.config import LLMProviderName, Settings, get_settings


def test_defaults_match_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PREGUNTAS_LLM_PROVIDER", "PREGUNTAS_LLM_MODEL", "PREGUNTAS_MAX_ROUNDS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider is LLMProviderName.CLAUDE_CLI
    assert settings.llm_model == "claude-opus-5"
    assert settings.llm_timeout_s == 120
    assert settings.llm_effort == "high"
    assert settings.shortlist_size == 8
    assert settings.questions_per_round == 3
    assert settings.max_rounds == 3
    assert settings.service_token is None


def test_environment_variables_use_the_preguntas_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREGUNTAS_LLM_PROVIDER", "template")
    monkeypatch.setenv("PREGUNTAS_SERVICE_TOKEN", "s3cret")
    monkeypatch.setenv("PREGUNTAS_CONFIDENT_THRESHOLD", "0.9")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider is LLMProviderName.TEMPLATE
    assert settings.service_token is not None
    assert settings.service_token.get_secret_value() == "s3cret"
    assert settings.confident_threshold == 0.9
    assert "s3cret" not in repr(settings)


def test_more_questions_than_the_contract_allows_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, questions_per_round=4)  # type: ignore[call-arg]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
