"""Question selection Q2b: information gain over the top genes, with a lineage diversity penalty.

This is the selector evaluated as Q2b in EXP-Q-001 (``diagnostic.ar-training``,
``eval/questioning.py``, ``Q2b`` with lambda 0.6). Each round it

1. takes the ``n_candidates`` best genes of the posterior that have an annotated
   disease and renormalises their scores (the candidate posterior);
2. scores every askable term annotated to those genes' diseases by the expected
   information gain of its yes/no answer over that posterior, with
   ``P(term | gene)`` the mean of ``P(term | disease)`` over the gene's
   annotated diseases (the split the re-ranker also uses);
3. picks ``n_questions`` terms greedily, multiplying a term's gain by
   ``1 - diversity_lambda`` when it shares an ancestor below organ-system level
   with an earlier pick or with a question asked in an earlier round. Ties go to
   the smaller HPO number.

Blocked terms are never scored: findings reported or answered "sí" with their
ancestors and descendants, "no" and "No sé" findings with their descendants, and
everything already asked or known.

The gain is returned in bits, without the ``p_unknown`` factor of the legacy
selector, so the round's early stop (``min_information_gain``) reads it the same
way for both selectors.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from diagnosticar_preguntas.domain import CandidateTerm, Evidence, Posterior
from diagnosticar_preguntas.kb.ontology import PHENOTYPIC_ABNORMALITY, ROOT
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import DiseaseProfile, LikelihoodModel
from diagnosticar_preguntas.selection.askability import AskabilityRules, askable_terms
from diagnosticar_preguntas.selection.diversity import NEGLIGIBLE_GAIN

PROBABILITY_FLOOR = 0.02
MIN_GAIN = 1e-9


@dataclass(frozen=True, slots=True)
class Q2bConfig:
    askability: AskabilityRules = field(default_factory=AskabilityRules)
    n_candidates: int = 10
    n_questions: int = 3
    diversity_lambda: float = 0.6
    genes_per_side: int = 5

    def __post_init__(self) -> None:
        if self.n_candidates < 2:
            raise ValueError("n_candidates must be at least 2")
        if self.n_questions < 1:
            raise ValueError("n_questions must be at least 1")
        if not 0 <= self.diversity_lambda <= 1:
            raise ValueError("diversity_lambda must be in [0, 1]")


def _entropy(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log(p)).sum())


def _hpo_number(hpo_id: str) -> int:
    return int(hpo_id.split(":")[1])


class Q2bSelector:
    """Implements ``domain.QuestionSelector``; ``shortlist`` returns the questions of the round."""

    def __init__(
        self,
        kb: KnowledgeBase,
        likelihood: LikelihoodModel,
        config: Q2bConfig | None = None,
    ) -> None:
        self._kb = kb
        self._likelihood = likelihood
        self.config = config or Q2bConfig()
        self._askable = askable_terms(kb, self.config.askability)
        organ_systems = {t for t in kb.term_ids() if PHENOTYPIC_ABNORMALITY in kb.parents(t)}
        self._coarse = frozenset({ROOT, PHENOTYPIC_ABNORMALITY, *organ_systems})

    def shortlist(self, posterior: Posterior, evidence: Evidence, k: int) -> list[CandidateTerm]:
        count = min(k, self.config.n_questions)
        if count <= 0:
            return []
        genes = [
            (g.gene_symbol, g.score, diseases)
            for g in posterior.genes
            if (diseases := self._kb.annotated_diseases_for_gene(g.gene_symbol))
        ][: self.config.n_candidates]
        if len(genes) < 2:
            return []
        scores = np.array([score for _, score, _ in genes], dtype=np.float64)
        total = scores.sum()
        if total <= 0:
            return []
        prior = scores / total
        profiles = [[self._likelihood.profile(d) for d in diseases] for _, _, diseases in genes]

        terms = self._candidates(profiles, evidence)
        scored = self._score(terms, prior, profiles)
        picks = self._pick(scored, count, evidence)
        gene_names = [gene for gene, _, _ in genes]
        return [self._candidate_term(t, scored[t], prior, gene_names) for t in picks]

    def _candidates(
        self, profiles: Sequence[Sequence[DiseaseProfile]], evidence: Evidence
    ) -> list[str]:
        terms: set[str] = set()
        for gene_profiles in profiles:
            for profile in gene_profiles:
                terms |= profile.annotated
        return sorted((terms & self._askable) - self._blocked(evidence))

    def _blocked(self, evidence: Evidence) -> set[str]:
        kb = self._kb
        present = kb.resolve_all(evidence.present)
        absent = kb.resolve_all(evidence.absent)
        unknown = kb.resolve_all(evidence.unknown)
        blocked: set[str] = set(
            evidence.asked_or_known | evidence.asked | present | absent | unknown
        )
        for hpo_id in present:
            blocked |= kb.ancestors(hpo_id) | kb.descendants(hpo_id)
        for hpo_id in absent | unknown:
            blocked |= kb.descendants(hpo_id)
        return blocked

    def _score(
        self,
        terms: Sequence[str],
        prior: np.ndarray,
        profiles: Sequence[Sequence[DiseaseProfile]],
    ) -> dict[str, _Scored]:
        h0 = _entropy(prior)
        scored: dict[str, _Scored] = {}
        for term in terms:
            p = np.clip(
                [
                    sum(profile.probability(term) for profile in gene_profiles) / len(gene_profiles)
                    for gene_profiles in profiles
                ],
                PROBABILITY_FLOOR,
                1 - PROBABILITY_FLOOR,
            )
            p_yes = float(prior @ p)
            post_yes = prior * p / p_yes
            post_no = prior * (1 - p) / (1 - p_yes)
            gain = h0 - p_yes * _entropy(post_yes) - (1 - p_yes) * _entropy(post_no)
            if gain > MIN_GAIN:
                scored[term] = _Scored(gain, p_yes, post_yes, post_no)
        return scored

    def _lineage(self, hpo_id: str) -> frozenset[str]:
        return (self._kb.ancestors(hpo_id) | {hpo_id}) - self._coarse

    def _pick(self, scored: Mapping[str, _Scored], count: int, evidence: Evidence) -> list[str]:
        taken = [self._lineage(t) for t in sorted(self._kb.resolve_all(evidence.asked))]
        pool = sorted(scored, key=lambda t: (-scored[t].gain, t))
        chosen: list[str] = []
        penalty = 1 - self.config.diversity_lambda
        while pool and len(chosen) < count:

            def adjusted(term: str) -> tuple[float, int]:
                shared = any(self._lineage(term) & other for other in taken)
                gain = scored[term].gain
                return (gain * penalty if shared else gain, -_hpo_number(term))

            best = max(pool, key=adjusted)
            pool.remove(best)
            chosen.append(best)
            taken.append(self._lineage(best))
        return chosen

    def _candidate_term(
        self, hpo_id: str, scored: _Scored, prior: np.ndarray, genes: Sequence[str]
    ) -> CandidateTerm:
        term = self._kb.term(hpo_id)
        if term is None:
            raise KeyError(hpo_id)
        return CandidateTerm(
            hpo_id=hpo_id,
            label_es=term.label_es or term.label_en,
            label_en=term.label_en,
            information_gain=scored.gain / math.log(2),
            p_yes=scored.p_yes,
            genes_yes=self._risers(scored.post_yes - prior, genes),
            genes_no=self._risers(scored.post_no - prior, genes),
            requires_testing=term.requires_testing,
        )

    def _risers(self, shift: np.ndarray, genes: Sequence[str]) -> tuple[str, ...]:
        order = sorted(range(len(genes)), key=lambda i: (-shift[i], i))
        rising = [genes[i] for i in order if shift[i] > NEGLIGIBLE_GAIN]
        return tuple(rising[: self.config.genes_per_side])


@dataclass(frozen=True, slots=True)
class _Scored:
    gain: float
    p_yes: float
    post_yes: np.ndarray
    post_no: np.ndarray
