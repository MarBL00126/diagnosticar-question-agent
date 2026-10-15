from __future__ import annotations

from pathlib import Path

import pytest

from diagnosticar_preguntas.config import LLMProviderName
from diagnosticar_preguntas.llm.credentials import claude_credentials_file, has_credentials


def logged_in_home(tmp_path: Path) -> Path:
    credentials = tmp_path / ".claude" / ".credentials.json"
    credentials.parent.mkdir()
    credentials.write_text("{}", encoding="utf-8")
    return tmp_path


def test_the_template_provider_needs_nothing() -> None:
    assert has_credentials(LLMProviderName.TEMPLATE, env={}) is True


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"ANTHROPIC_API_KEY": "sk-ant-x"}, True),
        ({"ANTHROPIC_API_KEY": ""}, False),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-x"}, False),
        ({}, False),
    ],
)
def test_the_api_provider_needs_an_api_key(env: dict[str, str], expected: bool) -> None:
    assert has_credentials(LLMProviderName.ANTHROPIC, env=env) is expected


@pytest.mark.parametrize("variable", ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_the_cli_accepts_a_token_in_the_environment(variable: str, tmp_path: Path) -> None:
    assert has_credentials(LLMProviderName.CLAUDE_CLI, env={variable: "x", "HOME": str(tmp_path)})


def test_the_cli_accepts_a_stored_login(tmp_path: Path) -> None:
    env = {"HOME": str(logged_in_home(tmp_path))}

    assert has_credentials(LLMProviderName.CLAUDE_CLI, env=env) is True


def test_the_cli_without_token_or_login_has_no_credentials(tmp_path: Path) -> None:
    assert has_credentials(LLMProviderName.CLAUDE_CLI, env={"HOME": str(tmp_path)}) is False


def test_claude_config_dir_moves_the_stored_login(tmp_path: Path) -> None:
    env = {"HOME": "/nonexistent", "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")}

    assert claude_credentials_file(env) == tmp_path / "cfg" / ".credentials.json"
