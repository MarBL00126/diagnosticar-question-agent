from __future__ import annotations

import pytest

from diagnosticar_preguntas.config import LLMProviderName, Settings, get_settings
from diagnosticar_preguntas.ranking.likelihood import LikelihoodConfig
from diagnosticar_preguntas.ranking.reranker import RerankConfig
from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from diagnosticar_preguntas.selection.selector import SelectionConfig


def test_defaults_match_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PREGUNTAS_LLM_PROVIDER", "PREGUNTAS_LLM_MODEL", "PREGUNTAS_MAX_ROUNDS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider is LLMProviderName.CLAUDE_CLI
    assert settings.llm_model == "claude-opus-5"
    assert settings.llm_timeout_s == 60
    assert settings.llm_effort == "high"
    assert settings.llm_question_effort == "medium"
    assert settings.shortlist_size == 8
    assert settings.questions_per_round == 3
    assert settings.max_rounds == 3
    assert settings.service_token is None
    assert settings.allow_unauthenticated is False


def test_ranking_defaults_are_the_tuned_configuration() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    chosen = {
        "strategy": "fixed_prior",
        "temperature": 1.0,
        "background": 0.1,
        "missing_frequency": 0.3,
        "epsilon": 0.02,
        "p_unknown": 0.2,
        "confident_threshold": 0.9,
        "min_information_gain": 0.05,
    }

    assert {
        "strategy": settings.rerank_strategy.value,
        "temperature": RerankConfig().temperature,
        "background": LikelihoodConfig().background,
        "missing_frequency": LikelihoodConfig().missing_frequency,
        "epsilon": LikelihoodConfig().epsilon,
        "p_unknown": SelectionConfig().p_unknown,
        "confident_threshold": settings.confident_threshold,
        "min_information_gain": settings.min_information_gain,
    } == chosen


def test_the_strategy_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREGUNTAS_RERANK_STRATEGY", "model_sees_present")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.rerank_strategy is RerankStrategy.MODEL_SEES_PRESENT


def test_the_platform_timeout_covers_a_whole_round() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    platform_timeout_s = 100

    worst_case = (
        settings.queue_timeout_s + settings.round_budget_s + settings.model_service_timeout_s
    )

    assert (settings.round_budget_s, settings.queue_timeout_s) == (75, 10)
    assert worst_case < platform_timeout_s
    assert settings.llm_timeout_s < settings.round_budget_s


def test_http_defaults_serve_on_8010_with_a_small_bounded_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PREGUNTAS_HOST", "PREGUNTAS_PORT", "PREGUNTAS_MAX_CONCURRENT_ROUNDS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert (settings.host, settings.port) == ("0.0.0.0", 8010)
    assert settings.max_concurrent_rounds == 2
    assert settings.max_queued_rounds >= 0


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


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_service_token_means_no_token(blank: str) -> None:
    settings = Settings(_env_file=None, service_token=blank)  # type: ignore[call-arg]

    assert settings.service_token is None


def test_empty_environment_variables_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREGUNTAS_PORT", "")
    monkeypatch.setenv("PREGUNTAS_SERVICE_TOKEN", "")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.port == 8010
    assert settings.service_token is None


def test_more_questions_than_the_contract_allows_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, questions_per_round=4)  # type: ignore[call-arg]


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()
