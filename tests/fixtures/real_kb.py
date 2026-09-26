"""Where the checks that need the real knowledge base find it."""

from __future__ import annotations

import os
from pathlib import Path

REPO_KB = Path(__file__).resolve().parents[2] / "data" / "kb.sqlite"


def real_kb_path() -> Path:
    """``PREGUNTAS_KB_PATH`` when set, as in the image, else the repository's ``make kb`` output.

    The release job mounts only ``tests/`` into the image, so a path relative to
    the tests would miss the knowledge base the image actually ships.
    """
    configured = os.environ.get("PREGUNTAS_KB_PATH")
    return Path(configured) if configured else REPO_KB
