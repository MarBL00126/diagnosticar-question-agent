from pathlib import Path

from genphenai_repreguntas.config import load_settings


def test_load_settings_reads_env_file(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=test-key",
                "REPREGUNTAS_MAX_DISEASES=all",
                "REPREGUNTAS_MIN_HPO_TERMS=5",
                "REPREGUNTAS_API_PORT=8999",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("REPREGUNTAS_MAX_DISEASES", raising=False)
    monkeypatch.delenv("REPREGUNTAS_MIN_HPO_TERMS", raising=False)
    monkeypatch.delenv("REPREGUNTAS_API_PORT", raising=False)

    settings = load_settings(env_file)

    assert settings.openai_api_key == "test-key"
    assert settings.max_diseases is None
    assert settings.min_hpo_terms == 5
    assert settings.api_port == 8999
