"""Per-round metrics, post-hoc stop rules and paired bootstrap intervals.

A trace records every request round played with the service's stop rules
disabled. A stop at request round ``s`` would only have frozen the ranking of
that round, so any pair of thresholds can be replayed exactly from the trace
(``apply_stops``). ``p_unknown`` of the selector scales every information gain
by ``1 - p_unknown`` and changes nothing else, so it is replayed the same way.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from eval.harness import TOP_K
from eval.policies import CaseTrace

ROUNDS = (0, 1, 2, 3)
HIT_AT = (1, 3, 5, 10, 20)
MISSING_RANK = TOP_K + 1


@dataclass(frozen=True, slots=True)
class StopRules:
    confident_threshold: float
    min_information_gain: float
    gain_scale: float = 1.0
    """Ratio ``(1 - p_new) / (1 - p_run)`` when replaying another ``p_unknown``."""


NO_STOPS = StopRules(confident_threshold=1.0, min_information_gain=0.0)


@dataclass(frozen=True, slots=True)
class Trajectory:
    """True-gene rank after 0..3 rounds of answers (``None``: outside the top 50)."""

    case_id: str
    ranks: tuple[int | None, ...]
    questions: tuple[int, ...]
    stop_reason: str
    elapsed_s: tuple[float, ...]


def apply_stops(trace: CaseTrace, rules: StopRules, *, uses_gain: bool) -> Trajectory:
    rounds = trace.rounds
    if trace.policy == "none":
        rank = rounds[0].true_rank
        return Trajectory(
            trace.case_id, (rank,) * len(ROUNDS), (0,) * len(ROUNDS), "no_questions", ()
        )
    stop_index = len(rounds) - 1
    reason = rounds[-1].finish_reason or "max_rounds"
    for index, record in enumerate(rounds):
        if index >= len(ROUNDS) - 1:
            break
        if record.top_score >= rules.confident_threshold:
            stop_index, reason = index, "confident"
            break
        if (
            uses_gain
            and record.max_gain is not None
            and record.max_gain * rules.gain_scale < rules.min_information_gain
        ):
            stop_index, reason = index, "no_useful_questions"
            break
        if record.finish_reason is not None:
            stop_index, reason = index, record.finish_reason
            break
    ranks = tuple(rounds[min(k, stop_index)].true_rank for k in ROUNDS)
    asked = [len(r.asked) if i < stop_index else 0 for i, r in enumerate(rounds)]
    questions = tuple(sum(asked[:k]) for k in ROUNDS)
    elapsed = tuple(r.elapsed_s for r in rounds[: stop_index + 1])
    return Trajectory(trace.case_id, ranks, questions, reason, elapsed)


def _rank(value: int | None) -> int:
    return MISSING_RANK if value is None else value


def reciprocal(value: int | None) -> float:
    return 0.0 if value is None else 1.0 / value


def summarize(trajectories: Sequence[Trajectory]) -> dict[str, object]:
    """Metrics by round, plus stop reasons and timing."""
    n = len(trajectories)
    by_round: dict[str, dict[str, float]] = {}
    for k in ROUNDS:
        ranks = [t.ranks[k] for t in trajectories]
        base = [t.ranks[0] for t in trajectories]
        row: dict[str, float] = {
            f"top{h}": sum(1 for r in ranks if r is not None and r <= h) / n for h in HIT_AT
        }
        row["mrr"] = sum(reciprocal(r) for r in ranks) / n
        row["median_rank"] = float(statistics.median(_rank(r) for r in ranks))
        row["worse"] = sum(1 for r, b in zip(ranks, base, strict=True) if _rank(r) > _rank(b)) / n
        row["better"] = sum(1 for r, b in zip(ranks, base, strict=True) if _rank(r) < _rank(b)) / n
        row["questions"] = sum(t.questions[k] for t in trajectories) / n
        by_round[str(k)] = row
    elapsed_by_round: dict[str, float] = {}
    for index in range(len(ROUNDS) + 1):
        values = [t.elapsed_s[index] for t in trajectories if len(t.elapsed_s) > index]
        if values:
            elapsed_by_round[str(index + 1)] = statistics.fmean(values)
    return {
        "n": n,
        "by_round": by_round,
        "stop_reasons": dict(Counter(t.stop_reason for t in trajectories).most_common()),
        "mean_request_seconds": elapsed_by_round,
    }


Metric = Callable[[Trajectory], float]

METRICS: Mapping[str, Metric] = {
    "top1": lambda t: float(t.ranks[-1] is not None and t.ranks[-1] <= 1),
    "top5": lambda t: float(t.ranks[-1] is not None and t.ranks[-1] <= 5),
    "top10": lambda t: float(t.ranks[-1] is not None and t.ranks[-1] <= 10),
    "mrr": lambda t: reciprocal(t.ranks[-1]),
}


def paired_bootstrap(
    first: Sequence[Trajectory],
    second: Sequence[Trajectory],
    metric: Metric,
    *,
    resamples: int = 2000,
    seed: int = 0,
) -> dict[str, float]:
    """Mean of ``metric(first) - metric(second)`` over the same cases, with a 95% percentile CI."""
    by_case = {t.case_id: t for t in second}
    pairs = [(metric(t), metric(by_case[t.case_id])) for t in first if t.case_id in by_case]
    deltas = np.array([a - b for a, b in pairs], dtype=np.float64)
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, len(deltas), size=(resamples, len(deltas)))
    means = deltas[samples].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return {"delta": float(deltas.mean()), "low": float(low), "high": float(high), "n": len(pairs)}
