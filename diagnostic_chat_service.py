"""Compatibility entrypoint for the FastAPI service.

The real implementation lives in ``src/genphenai_repreguntas``. Keeping this
file lets the project still run with ``python diagnostic_chat_service.py``.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from genphenai_repreguntas.api import app, main  # noqa: E402


if __name__ == "__main__":
    main()
