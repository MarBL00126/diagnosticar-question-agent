"""Shortlist of symptoms worth asking next, by expected information gain.

Candidates are the askable terms annotated to the current hypotheses' diseases,
plus their ancestors up to ``max_ancestor_distance`` steps (a broader
question like "Seizure" often splits the genes better than any specific
seizure type). Terms whose answer is already implied are dropped: ancestors
of a present finding are implied "sí", descendants of an absent finding are
implied "no", and descendants of a "No sé" would most likely get the same
answer. Descendants of a present finding are dropped too, although their
answer is not implied: the model is validated with "no" answers to terms
unrelated to what was reported (``docs/NEGACIONES.md``, section 9), and a "no"
to a subtype of a finding the patient has is not that.

The shortlist is then diversified (see ``diversity``): questions that would
move the same genes are penalised, and terms in an ancestor/descendant
relation are never asked together.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from diagnosticar_preguntas.domain import CandidateTerm, Evidence, Posterior
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import DiseaseProfile, LikelihoodModel
from diagnosticar_preguntas.selection.askability import AskabilityRules, askable_terms
from diagnosticar_preguntas.selection.diversity import NEGLIGIBLE_GAIN, pick_diverse
from diagnosticar_preguntas.selection.information_gain import (
    AnswerOutcomes,
    FloatArray,
    answer_outcomes,
)


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    askability: AskabilityRules = field(default_factory=AskabilityRules)
    max_ancestor_distance: int = 2
    p_unknown: float = 0.2
    genes_per_side: int = 5
    redundancy_penalty: float = 0.5
    diversity_pool: int = 50

    def __post_init__(self) -> None:
        if self.max_ancestor_distance < 0:
            raise ValueError("max_ancestor_distance cannot be negative")
        if not 0 <= self.p_unknown < 1:
            raise ValueError("p_unknown must be in [0, 1)")
        if not 0 <= self.redundancy_penalty <= 1:
            raise ValueError("redundancy_penalty must be in [0, 1]")


class InformationGainSelector:
    """Implements ``domain.QuestionSelector``."""

    def __init__(
        self,
        kb: KnowledgeBase,
        likelihood: LikelihoodModel,
        config: SelectionConfig | None = None,
    ) -> None:
        self._kb = kb
        self._likelihood = likelihood
        self.config = config or SelectionConfig()
        self._askable = askable_terms(kb, self.config.askability)
        self._nearby_cache: dict[str, frozenset[str]] = {}

    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        hypotheses = posterior.hypotheses
        if k <= 0 or not hypotheses:
            return []
        genes = [gene.gene_symbol for gene in posterior.genes]
        gene_index = {gene: i for i, gene in enumerate(genes)}
        profiles = [self._likelihood.profile(h.disease_id) for h in hypotheses]
        candidates = self._candidates(profiles, evidence)
        if not candidates:
            return []

        weights = np.array([h.weight for h in hypotheses], dtype=np.float64)
        weights /= weights.sum()
        gene_of = np.array([gene_index[h.gene_symbol] for h in hypotheses], dtype=np.intp)
        outcomes = answer_outcomes(
            self._likelihood_matrix(candidates, profiles),
            weights,
            gene_of,
            len(genes),
            self.config.p_unknown,
        )
        picks = pick_diverse(
            candidates,
            outcomes.information_gain,
            outcomes.genes_if_yes - outcomes.genes_now,
            k,
            related=self._related,
            redundancy_penalty=self.config.redundancy_penalty,
            pool_size=self.config.diversity_pool,
        )
        return [self._candidate_term(candidates[i], i, outcomes, genes) for i in picks]

    def _candidates(self, profiles: Sequence[DiseaseProfile], evidence: Evidence) -> list[str]:
        kb = self._kb
        present = kb.resolve_all(evidence.present)
        absent = kb.resolve_all(evidence.absent)
        unknown = kb.resolve_all(evidence.unknown)
        blocked: set[str] = set(evidence.asked_or_known | present | absent | unknown)
        for hpo_id in present:
            blocked |= kb.ancestors(hpo_id) | kb.descendants(hpo_id)
        for hpo_id in absent | unknown:
            blocked |= kb.descendants(hpo_id)
        nearby: set[str] = set()
        for profile in profiles:
            for hpo_id in profile.annotated:
                nearby |= self._nearby(hpo_id)
        return sorted((nearby & self._askable) - blocked)

    def _nearby(self, hpo_id: str) -> frozenset[str]:
        """The term and its ancestors up to ``max_ancestor_distance`` steps away."""
        if (cached := self._nearby_cache.get(hpo_id)) is not None:
            return cached
        found = {hpo_id}
        frontier = {hpo_id}
        for _ in range(self.config.max_ancestor_distance):
            frontier = {p for term in frontier for p in self._kb.parents(term)} - found
            found |= frontier
        result = frozenset(found)
        self._nearby_cache[hpo_id] = result
        return result

    @staticmethod
    def _likelihood_matrix(candidates: list[str], profiles: Sequence[DiseaseProfile]) -> FloatArray:
        index = {hpo_id: i for i, hpo_id in enumerate(candidates)}
        matrix = np.empty((len(candidates), len(profiles)), dtype=np.float64)
        for column, profile in enumerate(profiles):
            matrix[:, column] = profile.default
            for row, value in profile.deviations(index):
                matrix[row, column] = value
        return matrix

    def _related(self, first: str, second: str) -> bool:
        return first in self._kb.ancestors(second) or second in self._kb.ancestors(first)

    def _candidate_term(
        self, hpo_id: str, row: int, outcomes: AnswerOutcomes, genes: list[str]
    ) -> CandidateTerm:
        term = self._kb.term(hpo_id)
        if term is None:
            raise KeyError(hpo_id)
        return CandidateTerm(
            hpo_id=hpo_id,
            label_es=term.label_es or term.label_en,
            label_en=term.label_en,
            information_gain=float(outcomes.information_gain[row]),
            p_yes=float(outcomes.p_yes[row]),
            genes_yes=self._risers(outcomes.genes_if_yes[row] - outcomes.genes_now, genes),
            genes_no=self._risers(outcomes.genes_if_no[row] - outcomes.genes_now, genes),
            requires_testing=term.requires_testing,
        )

    def _risers(self, shift: FloatArray, genes: list[str]) -> tuple[str, ...]:
        order = sorted(range(len(genes)), key=lambda i: (-shift[i], i))
        rising = [genes[i] for i in order if shift[i] > NEGLIGIBLE_GAIN]
        return tuple(rising[: self.config.genes_per_side])
