"""Expected information gain of yes/no questions over the gene posterior.

Everything is vectorised over candidate terms: with ``C`` candidates, ``H``
hypotheses and ``G`` genes, ``likelihoods`` is ``C x H`` and each outcome is
computed with a couple of matrix products, which keeps a round in the low
milliseconds for thousands of candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.intp]


def entropy_bits(distributions: FloatArray) -> FloatArray:
    """Shannon entropy in bits along the last axis; zero probabilities contribute nothing."""
    logs = np.zeros_like(distributions)
    np.log2(distributions, out=logs, where=distributions > 0)
    result: FloatArray = -(distributions * logs).sum(axis=-1)
    return result


@dataclass(frozen=True, slots=True)
class AnswerOutcomes:
    """Per candidate: ``p_yes``, the gene posterior after each answer, and the gain in bits."""

    genes_now: FloatArray
    p_yes: FloatArray
    genes_if_yes: FloatArray
    genes_if_no: FloatArray
    information_gain: FloatArray


def answer_outcomes(
    likelihoods: FloatArray,
    weights: FloatArray,
    gene_of: IntArray,
    n_genes: int,
    p_unknown: float,
) -> AnswerOutcomes:
    """Outcomes of asking each candidate.

    ``likelihoods[c, h]`` is ``P(yes | hypothesis h)`` for candidate ``c``,
    ``weights`` the hypothesis posterior (summing to 1) and ``gene_of[h]`` the
    gene index of each hypothesis. An answer of "No sé" (probability
    ``p_unknown``) leaves the posterior unchanged, so it scales the gain by
    ``1 - p_unknown``.
    """
    membership = np.zeros((weights.shape[0], n_genes))
    membership[np.arange(weights.shape[0]), gene_of] = 1.0
    genes_now = weights @ membership
    joint_yes = (likelihoods * weights) @ membership
    joint_no = np.clip(genes_now - joint_yes, 0.0, None)
    p_yes = joint_yes.sum(axis=1)
    p_no = joint_no.sum(axis=1)
    genes_if_yes = joint_yes / p_yes[:, None]
    genes_if_no = joint_no / p_no[:, None]
    expected = p_yes * entropy_bits(genes_if_yes) + p_no * entropy_bits(genes_if_no)
    gain = (1.0 - p_unknown) * np.clip(entropy_bits(genes_now) - expected, 0.0, None)
    return AnswerOutcomes(genes_now, p_yes, genes_if_yes, genes_if_no, gain)
