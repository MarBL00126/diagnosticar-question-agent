from __future__ import annotations

import numpy as np

from diagnosticar_preguntas.selection.diversity import pick_diverse


def _never_related(first: str, second: str) -> bool:
    return False


def test_second_pick_prefers_a_term_that_splits_other_genes() -> None:
    names = ["X", "Y", "Z"]
    gain = np.array([1.0, 0.95, 0.7])
    shifts = np.array([[1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, 1.0, -1.0]])

    picks = pick_diverse(
        names, gain, shifts, 2, related=_never_related, redundancy_penalty=0.5, pool_size=10
    )

    assert [names[i] for i in picks] == ["X", "Z"]


def test_without_penalty_the_shortlist_is_ordered_by_gain() -> None:
    names = ["X", "Y", "Z"]
    gain = np.array([1.0, 0.95, 0.7])
    shifts = np.array([[1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [0.0, 1.0, -1.0]])

    picks = pick_diverse(
        names, gain, shifts, 3, related=_never_related, redundancy_penalty=0.0, pool_size=10
    )

    assert [names[i] for i in picks] == ["X", "Y", "Z"]


def test_opposite_shifts_count_as_redundant() -> None:
    names = ["X", "Y", "Z"]
    gain = np.array([1.0, 0.95, 0.7])
    shifts = np.array([[1.0, -1.0, 0.0], [-1.0, 1.0, 0.0], [0.0, 1.0, -1.0]])

    picks = pick_diverse(
        names, gain, shifts, 2, related=_never_related, redundancy_penalty=0.5, pool_size=10
    )

    assert [names[i] for i in picks] == ["X", "Z"]


def test_related_terms_are_never_picked_together() -> None:
    names = ["parent", "child", "other"]
    gain = np.array([1.0, 0.9, 0.1])
    shifts = np.eye(3)

    picks = pick_diverse(
        names,
        gain,
        shifts,
        3,
        related=lambda a, b: {a, b} == {"parent", "child"},
        redundancy_penalty=0.0,
        pool_size=10,
    )

    assert [names[i] for i in picks] == ["parent", "other"]


def test_terms_without_gain_are_never_picked() -> None:
    picks = pick_diverse(
        ["X", "Y"],
        np.array([0.3, 0.0]),
        np.eye(2),
        2,
        related=_never_related,
        redundancy_penalty=0.5,
        pool_size=10,
    )

    assert picks == [0]
