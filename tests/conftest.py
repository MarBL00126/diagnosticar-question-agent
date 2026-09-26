"""Configuration shared by the whole test suite.

The platform's release job runs this suite inside the image, with only
``tests/`` mounted. The evaluation harness (``eval/`` next to ``tests/``) is
not part of the installed package, so its tests run from a checkout only.
"""

from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "eval"

collect_ignore = [] if HARNESS.is_dir() else ["eval"]


def pytest_configure(config: pytest.Config) -> None:
    # Repeated from pyproject.toml, which the in-image run does not read.
    config.addinivalue_line(
        "markers", "live: calls a real LLM or model service; excluded by default (run with -m live)"
    )
