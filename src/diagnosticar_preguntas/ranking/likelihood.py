"""Probability that a patient with a given disease shows a given finding.

For disease ``d`` and term ``t``, ``P(t | d)`` follows the ontology:

* ``d`` is annotated with ``t`` or a descendant of ``t``: noisy-OR of the
  frequencies of those annotations (having any of them means having ``t``).
  An annotation without a stated frequency uses ``missing_frequency``, an
  explicit prior: treating unknown frequencies as certain makes every
  disease that mentions a finding indistinguishable. The result is never
  below the background rate, because an annotation cannot make a finding
  rarer than in unrelated patients (and this is what guarantees that a
  negated finding never favours the diseases that list it). A stated
  frequency of zero (``0/n`` in a small cohort) supports nothing and
  excludes nothing.
* otherwise, ``d`` carries a ``NOT`` annotation on ``t`` or an ancestor of
  ``t``: ``epsilon`` (the finding is explicitly excluded).
* otherwise: ``background``, the rate of an unrelated finding.

Every value is finally clipped to ``[epsilon, 1 - epsilon]``, which models
answer noise: a single "no" never rules a disease out completely.

The re-ranker's default weighting does not use ``P(t | d)`` itself but its two
ingredients, which ``DiseaseProfile`` also exposes: ``frequency`` (the
noisy-OR before the background floor and the clipping) and ``excludes`` (a
``NOT`` applies). The question selector always uses ``P(t | d)``.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from threading import Lock

from diagnosticar_preguntas.kb.store import KnowledgeBase

UNANNOTATED_DISEASE = "unannotated"


@dataclass(frozen=True, slots=True)
class LikelihoodConfig:
    background: float = 0.1
    missing_frequency: float = 0.3
    epsilon: float = 0.02

    def __post_init__(self) -> None:
        if not 0 < self.epsilon < 0.5:
            raise ValueError("epsilon must be in (0, 0.5)")
        if not self.epsilon <= self.background <= 1 - self.epsilon:
            raise ValueError("background must be in [epsilon, 1 - epsilon]")
        if not 0 <= self.missing_frequency <= 1:
            raise ValueError("missing_frequency must be in [0, 1]")

    def clip(self, probability: float) -> float:
        return min(max(probability, self.epsilon), 1 - self.epsilon)


class DiseaseProfile:
    """``P(t | d)`` for every term ``t``, precomputed for the terms the disease supports."""

    def __init__(
        self,
        *,
        supported: Mapping[str, float],
        frequencies: Mapping[str, float],
        annotated: frozenset[str],
        excluded: frozenset[str],
        kb: KnowledgeBase,
        config: LikelihoodConfig,
    ) -> None:
        self._supported = supported
        self._frequencies = frequencies
        self._excluded = excluded
        self._kb = kb
        self.annotated = annotated
        self.default = config.clip(config.background)
        self._excluded_value = config.epsilon

    @property
    def supported(self) -> Mapping[str, float]:
        """Terms with a positive annotation on them or a descendant, and their probability."""
        return self._supported

    def probability(self, hpo_id: str) -> float:
        if (value := self._supported.get(hpo_id)) is not None:
            return value
        if self._negated(hpo_id):
            return self._excluded_value
        return self.default

    def frequency(self, hpo_id: str) -> float | None:
        """Noisy-OR of the frequencies annotated to the term or a descendant.

        Before the background floor and the clipping of ``probability``;
        ``None`` when the disease annotates neither the term nor a descendant.
        """
        return self._frequencies.get(hpo_id)

    def excludes(self, hpo_id: str) -> bool:
        """A ``NOT`` on the term or an ancestor applies: ``probability`` is ``epsilon``.

        A positive annotation of the term or a descendant takes precedence, as
        in ``probability``.
        """
        return hpo_id not in self._supported and self._negated(hpo_id)

    def _negated(self, hpo_id: str) -> bool:
        return bool(self._excluded) and (
            hpo_id in self._excluded or not self._excluded.isdisjoint(self._kb.ancestors(hpo_id))
        )

    def deviations(self, index: Mapping[str, int]) -> Iterator[tuple[int, float]]:
        """Positions in ``index`` whose probability is not ``default``, with that probability.

        Lets callers fill a dense matrix touching only the few terms a disease
        says something about.
        """
        for hpo_id, value in self._supported.items():
            if (position := index.get(hpo_id)) is not None:
                yield position, value
        for root in self._excluded:
            for hpo_id in (root, *self._kb.descendants(root)):
                position = index.get(hpo_id)
                if position is not None and hpo_id not in self._supported:
                    yield position, self._excluded_value


class LikelihoodModel:
    """Builds and caches a ``DiseaseProfile`` per disease."""

    def __init__(
        self, kb: KnowledgeBase, config: LikelihoodConfig | None = None, cache_size: int = 4096
    ) -> None:
        self.kb = kb
        self.config = config or LikelihoodConfig()
        self._cache: OrderedDict[str, DiseaseProfile] = OrderedDict()
        self._cache_size = cache_size
        self._lock = Lock()

    def profile(self, disease_id: str) -> DiseaseProfile:
        with self._lock:
            if (cached := self._cache.get(disease_id)) is not None:
                self._cache.move_to_end(disease_id)
                return cached
        profile = self._build(disease_id)
        with self._lock:
            self._cache[disease_id] = profile
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return profile

    def probability(self, disease_id: str, hpo_id: str) -> float:
        return self.profile(disease_id).probability(hpo_id)

    def _build(self, disease_id: str) -> DiseaseProfile:
        config = self.config
        absent_all: dict[str, float] = {}
        annotated: set[str] = set()
        excluded: set[str] = set()
        annotations = () if disease_id == UNANNOTATED_DISEASE else self.kb.annotations(disease_id)
        for annotation in annotations:
            if annotation.negated:
                excluded.add(annotation.hpo_id)
                continue
            frequency = (
                config.missing_frequency if annotation.frequency is None else annotation.frequency
            )
            if frequency <= 0:
                continue
            annotated.add(annotation.hpo_id)
            for term in (annotation.hpo_id, *self.kb.ancestors(annotation.hpo_id)):
                absent_all[term] = absent_all.get(term, 1.0) * (1.0 - frequency)
        frequencies = {term: 1.0 - none_present for term, none_present in absent_all.items()}
        supported = {
            term: config.clip(max(config.background, frequency))
            for term, frequency in frequencies.items()
        }
        return DiseaseProfile(
            supported=supported,
            frequencies=frequencies,
            annotated=frozenset(annotated),
            excluded=frozenset(excluded),
            kb=self.kb,
            config=config,
        )
