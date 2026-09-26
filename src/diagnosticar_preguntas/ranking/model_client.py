"""HTTP client for the diagnostic.ar model (``POST /rank-genes``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

import httpx

from diagnosticar_preguntas.domain import ModelRanking, ModelUnavailableError

NO_KNOWN_HPO_IDS = "no-known-hpo-ids"


class NoKnownHpoIdsError(ValueError):
    """The model knows none of the terms sent, so it cannot rank anything."""

    def __init__(self, hpo_ids: Sequence[str]) -> None:
        super().__init__(f"the model knows none of {list(hpo_ids)}")
        self.hpo_ids = tuple(hpo_ids)


class HttpModelClient:
    """Implements ``domain.ModelClient`` over HTTP.

    Connection errors, timeouts, 5xx and unusable responses raise
    ``ModelUnavailableError``; a 422 ``no-known-hpo-ids`` raises
    ``NoKnownHpoIdsError`` because retrying the same terms cannot help.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        *,
        transport: httpx.BaseTransport | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport, headers=headers
        )

    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        query = tuple(hpo_ids)
        try:
            response = self._client.post(
                "/rank-genes", json={"hpo_ids": list(query), "top_k": top_k}
            )
        except httpx.TimeoutException as error:
            raise ModelUnavailableError(f"model timed out: {error}") from error
        except httpx.HTTPError as error:
            raise ModelUnavailableError(f"model unreachable: {error}") from error

        if response.status_code == 422 and _error_code(response) == NO_KNOWN_HPO_IDS:
            raise NoKnownHpoIdsError(query)
        if response.is_error:
            raise ModelUnavailableError(
                f"model answered {response.status_code}: {response.text[:200]}"
            )
        try:
            return _parse_ranking(response.json(), query)
        except (ValueError, KeyError, TypeError) as error:
            raise ModelUnavailableError(f"unusable model response: {error}") from error

    def is_healthy(self, timeout: float) -> bool:
        """Whether ``GET /healthz`` answers with a success status within ``timeout`` seconds."""
        try:
            response = self._client.get("/healthz", timeout=timeout)
        except httpx.HTTPError:
            return False
        return response.is_success

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpModelClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _error_code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return None
    if isinstance(detail, dict):
        code = detail.get("error")
        return code if isinstance(code, str) else None
    return None


def _parse_ranking(body: Any, query: tuple[str, ...]) -> ModelRanking:
    scores: dict[str, float] = {}
    for result in body["results"]:
        symbol = result["gene_symbol"]
        if not isinstance(symbol, str):
            raise TypeError(f"gene_symbol {symbol!r} is not a string")
        scores.setdefault(symbol, float(result["score"]))
    specialties = body.get("specialties")
    confidence = body.get("confidence")
    return ModelRanking(
        scores=scores,
        specialties=dict(specialties) if isinstance(specialties, dict) else None,
        confidence=float(confidence) if confidence is not None else None,
        unknown_hpo_ids=tuple(str(i) for i in body.get("unknown_hpo_ids") or ()),
        query_hpo_ids=query,
    )
