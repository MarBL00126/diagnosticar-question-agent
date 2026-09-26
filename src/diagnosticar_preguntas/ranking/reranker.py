"""Bayesian re-ranking of the model's genes with the evidence it did not see.

Hypotheses are (gene, disease) pairs. A gene's prior is its tempered model
probability, split evenly among its annotated diseases; a gene without any
annotated disease keeps a single ``unannotated`` hypothesis that explains
every finding at the background rate. The posterior of a hypothesis is its
prior times the likelihood of the unseen evidence (``P(t | d)`` for present
findings, ``1 - P(t | d)`` for absent ones); a gene's score is the sum over
its hypotheses.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from diagnosticar_preguntas.domain import (
    Evidence,
    GeneScore,
    Hypothesis,
    ModelRanking,
    Posterior,
)
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import UNANNOTATED_DISEASE, LikelihoodModel


@dataclass(frozen=True, slots=True)
class RerankConfig:
    """``temperature`` > 1 flattens the model's distribution before the answers apply.

    The default 1 keeps the model's probabilities as they are: flattening them
    (T = 2, 3 or 5) let a few noisy answers push the right gene down in the
    evaluation (``docs/EVALUACION.md``, section 5). Real patients may make the
    model more overconfident than simulated ones; measure before raising it.
    """

    top_n: int = 50
    temperature: float = 1.0

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if self.temperature <= 0:
            raise ValueError("temperature must be positive")


@dataclass(frozen=True, slots=True)
class UnseenEvidence:
    """Resolved findings the model did not take into account."""

    present: frozenset[str]
    absent: frozenset[str]


def unseen_evidence(kb: KnowledgeBase, ranking: ModelRanking, evidence: Evidence) -> UnseenEvidence:
    seen = evidence.present if ranking.query_hpo_ids is None else frozenset(ranking.query_hpo_ids)
    present = (evidence.present - seen) | (evidence.present & frozenset(ranking.unknown_hpo_ids))
    return UnseenEvidence(present=kb.resolve_all(present), absent=kb.resolve_all(evidence.absent))


def tempered_prior(scores: Mapping[str, float], temperature: float) -> dict[str, float]:
    """``p ** (1 / T)`` renormalised; uniform when every score is zero."""
    tempered = {gene: max(score, 0.0) ** (1.0 / temperature) for gene, score in scores.items()}
    total = sum(tempered.values())
    if total <= 0:
        return {gene: 1.0 / len(scores) for gene in scores}
    return {gene: value / total for gene, value in tempered.items()}


class BayesianReranker:
    """Implements ``domain.Reranker``."""

    def __init__(
        self,
        kb: KnowledgeBase,
        likelihood: LikelihoodModel,
        config: RerankConfig | None = None,
    ) -> None:
        self._kb = kb
        self._likelihood = likelihood
        self.config = config or RerankConfig()

    def rerank(self, ranking: ModelRanking, evidence: Evidence) -> Posterior:
        ordered = sorted(ranking.scores.items(), key=lambda item: -item[1])
        model_rank = {gene: rank for rank, (gene, _) in enumerate(ordered, start=1)}
        considered = dict(ordered[: self.config.top_n])
        if not considered:
            return Posterior(genes=())
        prior = tempered_prior(considered, self.config.temperature)
        unseen = unseen_evidence(self._kb, ranking, evidence)

        pairs: list[tuple[str, str]] = []
        log_weights: list[float] = []
        for gene, gene_prior in prior.items():
            diseases = self._kb.annotated_diseases_for_gene(gene) or (UNANNOTATED_DISEASE,)
            for disease in diseases:
                pairs.append((gene, disease))
                log_weights.append(
                    _log(gene_prior / len(diseases)) + self._log_likelihood(disease, unseen)
                )

        peak = max(log_weights)
        weights = [math.exp(value - peak) for value in log_weights]
        total = sum(weights)
        hypotheses = sorted(
            (
                Hypothesis(gene, disease, weight / total)
                for (gene, disease), weight in zip(pairs, weights, strict=True)
            ),
            key=lambda h: (-h.weight, h.gene_symbol, h.disease_id),
        )
        gene_scores: dict[str, float] = dict.fromkeys(prior, 0.0)
        for hypothesis in hypotheses:
            gene_scores[hypothesis.gene_symbol] += hypothesis.weight
        genes = sorted(
            (
                GeneScore(gene, score, considered[gene], model_rank[gene])
                for gene, score in gene_scores.items()
            ),
            key=lambda g: (-g.score, g.model_rank),
        )
        return Posterior(genes=tuple(genes), hypotheses=tuple(hypotheses))

    def _log_likelihood(self, disease: str, unseen: UnseenEvidence) -> float:
        profile = self._likelihood.profile(disease)
        present = sum(math.log(profile.probability(t)) for t in unseen.present)
        absent = sum(math.log1p(-profile.probability(t)) for t in unseen.absent)
        return present + absent


def _log(value: float) -> float:
    return math.log(value) if value > 0 else -math.inf
