"""HTTP API of the question agent: ``POST /v1/rounds`` and ``GET /healthz``.

Requests carry clinical data (findings and free-text notes), so no error body
and no log line ever repeats any part of them. Errors answer with a short
code instead:

======  ====================  ==================================================
Status  ``error``             When
======  ====================  ==================================================
401     ``unauthorized``      ``X-Internal-Token`` missing or wrong
422     ``invalid-request``   the body breaks the contract (``detail`` lists
                              where, without the offending values)
422     ``no-known-hpo-ids``  the model knows none of the present findings
503     ``busy``              every round slot is taken and the queue is full
503     ``model-unavailable`` the diagnostic.ar model service failed
500     ``internal-error``    anything else
======  ====================  ==================================================

503 answers carry ``Retry-After``. LLM failures are not errors: the round
falls back to template wording and says so in ``warnings``.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from diagnosticar_preguntas import __version__
from diagnosticar_preguntas.agent.round import LLM_FALLBACK_WARNINGS
from diagnosticar_preguntas.api.auth import TOKEN_HEADER, token_matches
from diagnosticar_preguntas.api.health import Health, check_health, llm_health
from diagnosticar_preguntas.api.limiter import RoundLimiter, SaturatedError
from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.config import Settings, get_settings
from diagnosticar_preguntas.domain import ModelUnavailableError
from diagnosticar_preguntas.ranking.model_client import NO_KNOWN_HPO_IDS, NoKnownHpoIdsError
from diagnosticar_preguntas.wiring import Service, build_service

logger = logging.getLogger(__name__)

RETRY_AFTER_S = 10


class ErrorOut(BaseModel):
    error: str
    detail: list[dict[str, Any]] | None = None


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, *, retry_after_s: int | None = None) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.retry_after_s = retry_after_s


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorOut, "description": description}
    for status, description in {
        401: "Missing or wrong X-Internal-Token.",
        422: "Invalid request, or no finding known to the model.",
        500: "Unexpected failure.",
        503: "Busy, or the model service is unavailable. See Retry-After.",
    }.items()
}


def create_app(settings: Settings | None = None, service: Service | None = None) -> FastAPI:
    """Build the app. Without ``service`` the real components are wired from ``settings``."""
    settings = settings or get_settings()
    service = service or build_service(settings)
    limiter = RoundLimiter(
        concurrent=settings.max_concurrent_rounds,
        queued=settings.max_queued_rounds,
        wait_s=settings.queue_timeout_s,
    )
    _warn_about_setup(settings)

    app = FastAPI(
        title="diagnostic.ar question agent",
        version=__version__,
        summary="Follow-up symptom questions that refine a gene ranking. It never diagnoses.",
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, error: ApiError) -> JSONResponse:
        headers = {} if error.retry_after_s is None else {"Retry-After": str(error.retry_after_s)}
        return JSONResponse({"error": error.code}, status_code=error.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes each offending value ("input"), which may be a note.
        detail = [
            {"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")}
            for e in error.errors()
        ]
        logger.info("rejected an invalid request: %d errors", len(detail))
        return JSONResponse({"error": "invalid-request", "detail": detail}, status_code=422)

    def require_token(
        token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None,
    ) -> None:
        if not token_matches(settings.service_token, token):
            raise ApiError(401, "unauthorized")

    @app.post(
        "/v1/rounds",
        response_model=RoundResponse,
        dependencies=[Depends(require_token)],
        responses=_ERROR_RESPONSES,
    )
    def create_round(body: RoundRequest) -> RoundResponse:
        """Run one refinement round. Sync on purpose: FastAPI runs it in a worker thread."""
        return _run_round(service, limiter, body)

    @app.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return check_health(settings, service)

    return app


def _run_round(service: Service, limiter: RoundLimiter, request: RoundRequest) -> RoundResponse:
    started = time.monotonic()
    error: ApiError
    try:
        with limiter.slot():
            response = service.rounds.run(request)
    except SaturatedError:
        error = ApiError(503, "busy", retry_after_s=RETRY_AFTER_S)
    except NoKnownHpoIdsError:
        error = ApiError(422, NO_KNOWN_HPO_IDS)
    except ModelUnavailableError:
        error = ApiError(503, "model-unavailable", retry_after_s=RETRY_AFTER_S)
    except Exception as exc:
        _log_unexpected(exc)
        error = ApiError(500, "internal-error")
    else:
        logger.info(
            "round=%d mode=%s http=200 status=%s finish_reason=%s questions=%d"
            " llm_fallback=%s elapsed_ms=%.0f",
            request.round,
            request.mode.value,
            response.status,
            response.finish_reason,
            len(response.questions),
            not LLM_FALLBACK_WARNINGS.isdisjoint(response.warnings),
            _elapsed_ms(started),
        )
        return response
    logger.warning(
        "round=%d mode=%s http=%d error=%s elapsed_ms=%.0f",
        request.round,
        request.mode.value,
        error.status_code,
        error.code,
        _elapsed_ms(started),
    )
    raise error


def _log_unexpected(exc: BaseException) -> None:
    # The traceback without the exception message: messages may quote request data.
    frames = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("round failed with %s\n%s", type(exc).__name__, frames)


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _warn_about_setup(settings: Settings) -> None:
    if settings.service_token is None:
        logger.warning(
            "PREGUNTAS_SERVICE_TOKEN is not set: /v1/rounds accepts unauthenticated calls."
            " Fine for local development, never for a shared host."
        )
    if llm_health(settings).credentials == "missing":
        logger.warning(
            "no credentials for LLM provider %s: questions and summaries will use templates",
            settings.llm_provider.value,
        )
