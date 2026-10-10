"""An in-memory stand-in for the diagnostic.ar model service."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from diagnosticar_preguntas.domain import NO_CASE, ModelRanking, ModelUnavailableError, PatientCase
from diagnosticar_preguntas.ranking.model_client import NoKnownHpoIdsError

Scores = Mapping[str, float] | Callable[[tuple[str, ...]], Mapping[str, float]]


class FakeModelClient:
    """Implements ``domain.ModelClient`` with fixed or query-dependent scores.

    ``known_hpo_ids`` (default: everything) decides which terms are reported
    unknown, as the real service does. Every call is recorded in ``calls``,
    and the case it carried in ``cases``.
    """

    def __init__(
        self,
        scores: Scores,
        *,
        known_hpo_ids: Iterable[str] | None = None,
        specialties: Mapping[str, Any] | None = None,
        confidence: float | None = None,
        unavailable: bool = False,
    ) -> None:
        self._scores = scores
        self._known = None if known_hpo_ids is None else frozenset(known_hpo_ids)
        self._specialties = specialties
        self._confidence = confidence
        self._unavailable = unavailable
        self.calls: list[tuple[tuple[str, ...], int]] = []
        self.cases: list[PatientCase] = []

    def rank(
        self,
        hpo_ids: Sequence[str],
        top_k: int,
        excluded_hpo_ids: Sequence[str] = (),
        *,
        case: PatientCase = NO_CASE,
    ) -> ModelRanking:
        query = tuple(hpo_ids)
        self.calls.append((query, top_k))
        self.cases.append(case)
        if self._unavailable:
            raise ModelUnavailableError("fake model is down")
        unknown = tuple(i for i in query if self._known is not None and i not in self._known)
        if query and len(unknown) == len(query):
            raise NoKnownHpoIdsError(query)
        scores = self._scores(query) if callable(self._scores) else self._scores
        top = sorted(scores.items(), key=lambda item: -item[1])[:top_k]
        return ModelRanking(
            scores=dict(top),
            specialties=self._specialties,
            confidence=self._confidence,
            unknown_hpo_ids=unknown,
            query_hpo_ids=query,
        )
