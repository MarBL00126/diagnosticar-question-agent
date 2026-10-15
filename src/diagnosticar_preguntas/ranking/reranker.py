"""Bayesian re-ranking of the model's genes with the evidence it did not see.

Hypotheses are (gene, disease) pairs. A gene's prior is its tempered model
probability, split evenly among its annotated diseases; a gene without any
annotated disease keeps a single ``unannotated`` hypothesis. Each hypothesis
is multiplied by factors for the evidence, as ``AnswerWeighting`` says, and a
gene's score is the sum over its hypotheses, renormalised.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from diagnosticar_preguntas.domain import (
    Evidence,
    GeneScore,
    Hypothesis,
    ModelRanking,
    Posterior,
)
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import UNANNOTATED_DISEASE, LikelihoodModel


class AnswerWeighting(StrEnum):
    """How the evidence reweights each disease. ``p`` is ``DiseaseProfile.frequency``.

    ``NONE`` (default): nothing reweights the diseases. The ranking is the
    model's, tempered and split over each gene's diseases so that question
    selection still has hypotheses; with ``RerankStrategy.MODEL_SEES_PRESENT``
    the "sí" answers reach it through the model's query, and "no" answers and
    negated findings are only recorded. No weighting improved the model on
    published case reports (``docs/EVALUACION.md``, sections 11.8 to 11.11).

    ``ASYMMETRIC``: only what a disease's annotations say moves it.
    Every present finding the disease excludes (``NOT`` on it or an ancestor)
    multiplies it by ``exclusion_factor``; every present finding the model did
    not see and the disease annotates, by ``1 + answer_weight * p``; every
    absent finding the disease annotates, by ``1 - answer_weight * p``.
    Anything else leaves it as it is: an annotation that does not mention a
    finding is no evidence either way. On published case reports this was the
    only weighting that stopped lowering the right gene (``docs/EVALUACION.md``,
    section 11.8).

    ``CALIBRATED``: the full likelihood of ``ranking.likelihood``, ``P(t | d)``
    for each unseen present finding and ``1 - P(t | d)`` for each absent one.
    A finding a disease does not mention counts against it (background rate),
    which fits patients drawn from the annotations and hurt real ones.
    """

    NONE = "none"
    ASYMMETRIC = "asymmetric"
    CALIBRATED = "calibrated"


@dataclass(frozen=True, slots=True)
class RerankConfig:
    """``temperature`` > 1 flattens the model's distribution before the answers apply.

    The default 1 keeps the model's probabilities as they are; T = 2 or 3 was
    worse both on simulated and on real patients (``docs/EVALUACION.md``,
    sections 5 and 11.6). ``answer_weight`` and ``exclusion_factor`` only
    apply to ``AnswerWeighting.ASYMMETRIC``; with ``answer_weight`` 0 and
    ``exclusion_factor`` 1 the ranking is the model's.
    """

    top_n: int = 50
    temperature: float = 1.0
    answer_weighting: AnswerWeighting = AnswerWeighting.NONE
    answer_weight: float = 0.3
    exclusion_factor: float = 0.02

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if self.temperature <= 0:
            raise ValueError("temperature must be positive")
        if not 0 <= self.answer_weight < 1:
            raise ValueError("answer_weight must be in [0, 1)")
        if not 0 < self.exclusion_factor <= 1:
            raise ValueError("exclusion_factor must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ResolvedEvidence:
    """The evidence with current ids: every present finding and what the model did not see.

    ``unseen_present`` are the present findings not sent to the model or
    reported unknown by it; the model sees no absent finding.
    """

    present: frozenset[str]
    unseen_present: frozenset[str]
    absent: frozenset[str]


def resolve_evidence(
    kb: KnowledgeBase, ranking: ModelRanking, evidence: Evidence
) -> ResolvedEvidence:
    seen = evidence.present if ranking.query_hpo_ids is None else frozenset(ranking.query_hpo_ids)
    unseen = (evidence.present - seen) | (evidence.present & frozenset(ranking.unknown_hpo_ids))
    return ResolvedEvidence(
        present=kb.resolve_all(evidence.present),
        unseen_present=kb.resolve_all(unseen),
        absent=kb.resolve_all(evidence.absent),
    )


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
        resolved = resolve_evidence(self._kb, ranking, evidence)

        pairs: list[tuple[str, str]] = []
        log_weights: list[float] = []
        for gene, gene_prior in prior.items():
            diseases = self._kb.annotated_diseases_for_gene(gene) or (UNANNOTATED_DISEASE,)
            for disease in diseases:
                pairs.append((gene, disease))
                log_weights.append(
                    _log(gene_prior / len(diseases)) + self._log_likelihood(disease, resolved)
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

    def _log_likelihood(self, disease: str, evidence: ResolvedEvidence) -> float:
        config = self.config
        if config.answer_weighting is AnswerWeighting.NONE:
            return 0.0
        profile = self._likelihood.profile(disease)
        if config.answer_weighting is AnswerWeighting.CALIBRATED:
            present = sum(math.log(profile.probability(t)) for t in evidence.unseen_present)
            absent = sum(math.log1p(-profile.probability(t)) for t in evidence.absent)
            return present + absent
        total = 0.0
        for hpo_id in evidence.present:
            if profile.excludes(hpo_id):
                total += math.log(config.exclusion_factor)
        for hpo_id in evidence.unseen_present:
            if (frequency := profile.frequency(hpo_id)) is not None:
                total += math.log1p(config.answer_weight * frequency)
        for hpo_id in evidence.absent:
            if (frequency := profile.frequency(hpo_id)) is not None:
                total += math.log1p(-config.answer_weight * frequency)
        return total


def _log(value: float) -> float:
    return math.log(value) if value > 0 else -math.inf
