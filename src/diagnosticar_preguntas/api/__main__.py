"""Serve the API: ``python -m diagnosticar_preguntas.api``.

One process: the knowledge base lives in memory and concurrency is bounded
by ``PREGUNTAS_MAX_CONCURRENT_ROUNDS``, not by the number of workers.
"""

from __future__ import annotations

import logging

import uvicorn

from diagnosticar_preguntas.api.app import create_app
from diagnosticar_preguntas.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
