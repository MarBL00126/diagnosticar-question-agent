"""Whether the configured LLM provider has credentials to call Claude.

Only presence is checked, never validity: a real check would spend quota on
every health probe. Values are never read beyond "set or not", so nothing
here can leak a secret.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from diagnosticar_preguntas.config import LLMProviderName

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY_ENV = "ANTHROPIC_API_KEY"


def claude_credentials_file(env: Mapping[str, str]) -> Path:
    """Where ``claude`` stores the login of ``claude /login`` on Linux."""
    if config_dir := env.get("CLAUDE_CONFIG_DIR"):
        return Path(config_dir) / ".credentials.json"
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    return home / ".claude" / ".credentials.json"


def has_credentials(provider: LLMProviderName, env: Mapping[str, str] | None = None) -> bool:
    """True when ``provider`` can authenticate; the template provider needs nothing."""
    env = os.environ if env is None else env
    match provider:
        case LLMProviderName.TEMPLATE:
            return True
        case LLMProviderName.ANTHROPIC:
            return bool(env.get(API_KEY_ENV))
        case LLMProviderName.CLAUDE_CLI:
            return (
                bool(env.get(OAUTH_TOKEN_ENV))
                or bool(env.get(API_KEY_ENV))
                or claude_credentials_file(env).is_file()
            )
