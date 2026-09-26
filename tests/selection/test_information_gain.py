from __future__ import annotations

import numpy as np
import pytest

from diagnosticar_preguntas.selection.information_gain import answer_outcomes, entropy_bits

TWO_GENES = np.array([0, 1], dtype=np.intp)
EQUAL = np.array([0.5, 0.5])


def test_entropy_of_a_uniform_distribution_is_log2_of_its_size() -> None:
    assert entropy_bits(np.full(8, 1 / 8)) == pytest.approx(3.0)
    assert entropy_bits(np.array([1.0, 0.0])) == pytest.approx(0.0)


def test_perfect_split_of_two_equal_genes_gains_one_bit_times_the_answer_rate() -> None:
    likelihoods = np.array([[1.0, 0.0]])

    outcomes = answer_outcomes(likelihoods, EQUAL, TWO_GENES, 2, p_unknown=0.2)

    assert outcomes.information_gain[0] == pytest.approx(0.8)
    assert outcomes.p_yes[0] == pytest.approx(0.5)
    assert outcomes.genes_if_yes[0] == pytest.approx([1.0, 0.0])
    assert outcomes.genes_if_no[0] == pytest.approx([0.0, 1.0])


def test_term_shared_by_every_gene_gains_nothing() -> None:
    likelihoods = np.array([[0.7, 0.7]])

    outcomes = answer_outcomes(likelihoods, EQUAL, TWO_GENES, 2, p_unknown=0.0)

    assert outcomes.information_gain[0] == pytest.approx(0.0, abs=1e-12)
    assert outcomes.genes_if_yes[0] == pytest.approx(EQUAL)


def test_hypotheses_of_the_same_gene_are_aggregated_before_measuring_entropy() -> None:
    likelihoods = np.array([[0.9, 0.1]])
    weights = np.array([0.5, 0.5])
    same_gene = np.array([0, 0], dtype=np.intp)

    outcomes = answer_outcomes(likelihoods, weights, same_gene, 1, p_unknown=0.0)

    assert outcomes.information_gain[0] == pytest.approx(0.0, abs=1e-12)


def test_gain_is_largest_for_the_most_even_split() -> None:
    likelihoods = np.array([[0.9, 0.1], [0.6, 0.4], [0.5, 0.5]])

    gain = answer_outcomes(likelihoods, EQUAL, TWO_GENES, 2, p_unknown=0.0).information_gain

    assert gain[0] > gain[1] > gain[2]
