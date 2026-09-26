"""Fixtures for the HTTP tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.tiny_kb import tiny_kb as tiny_kb


@pytest.fixture
def no_llm_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
