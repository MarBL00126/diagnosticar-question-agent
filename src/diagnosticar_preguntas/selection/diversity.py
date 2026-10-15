"""Greedy diversification of a question shortlist (maximal marginal relevance).

Each pick maximises ``gain * (1 - redundancy_penalty * similarity)``, where
``similarity`` is the largest absolute cosine between the candidate's shift
of the gene posterior and that of any term already picked. Two questions
that move the same genes are worth little more than one, whichever answer
does the moving, hence the absolute value. Candidates related to a pick
(e.g. ancestor and descendant) are dropped from the pool.

Carbonell and Goldstein, "The use of MMR, diversity-based reranking for
reordering documents and producing summaries", SIGIR 1998.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from diagnosticar_preguntas.selection.information_gain import FloatArray

NEGLIGIBLE_GAIN = 1e-9


def pick_diverse(
    names: Sequence[str],
    gain: FloatArray,
    shifts: FloatArray,
    k: int,
    *,
    related: Callable[[str, str], bool],
    redundancy_penalty: float,
    pool_size: int,
) -> list[int]:
    """Indices of up to ``k`` candidates, in pick order.

    Only the ``pool_size`` best candidates by gain are considered, and
    candidates with a negligible gain never are.
    """
    order = sorted(range(len(names)), key=lambda i: (-gain[i], names[i]))
    pool = [i for i in order[:pool_size] if gain[i] > NEGLIGIBLE_GAIN]
    norms = np.linalg.norm(shifts, axis=1)

    def marginal(candidate: int, picks: list[int]) -> float:
        similarity = 0.0
        for pick in picks:
            denominator = norms[candidate] * norms[pick]
            if denominator > 0:
                cosine = abs(float(shifts[candidate] @ shifts[pick])) / denominator
                similarity = max(similarity, cosine)
        return float(gain[candidate]) * (1.0 - redundancy_penalty * similarity)

    picks: list[int] = []
    while pool and len(picks) < k:
        best = max(pool, key=lambda i: marginal(i, picks))
        picks.append(best)
        pool = [i for i in pool if i != best and not related(names[i], names[best])]
    return picks
