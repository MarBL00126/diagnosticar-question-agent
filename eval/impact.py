"""Every derived figure quoted in docs/IMPACTO.md, computed from the saved results.

    python -m eval.impact [--json eval/results/impact.json]

Reads the TEST runs written by ``eval.run_simulation`` (the commands are in
docs/EVALUACION.md, "Cómo reproducir"), ``eval/data/cases.json`` and
``eval/results/live_claude.json``, and prints the figures by IMPACTO.md
section. The coverage section opens ``data/kb.sqlite`` and reads the model's
rankings through the evaluation cache (the local model service answers any
miss); no LLM is called.

"Before" is always the model's own ranking (policy ``none``); "after" is the
rank once the policy's session closed, with the service's stop rules. A true
gene outside the model's top 50 counts as rank 51. Absolute deltas come from
``eval.metrics.paired_bootstrap``; relative changes and ratios are taken over
the same 2000 case resamples (seed 0), so every interval in the document is a
95 % percentile interval over the same draws.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from eval.cases import Case, load
from eval.harness import CASES_PATH, RESULTS_DIR, TOP_K, open_kb, real_model
from eval.metrics import (
    MISSING_RANK,
    ROUNDS,
    Metric,
    Trajectory,
    paired_bootstrap,
    reciprocal,
    summarize,
)
from eval.report import trajectories

RESAMPLES = 2000
SEED = 0
RUNS = {
    "chosen": "test_chosen.json",
    "before_tuning": "test_default.json",
    "dropout": "test_chosen_dropout.json",
    "before_tuning_dropout": "test_default_dropout.json",
    "noiseless": "test_chosen_noiseless.json",
    "medico": "test_chosen_medico.json",
    "ablation_model_sees_present": "test_ablation_msp.json",
    "ablation_t3": "test_ablation_t3.json",
    "ablation_old_stops": "test_ablation_default_stops.json",
}
BASELINES = ("random_askable", "most_frequent_unreported")
RANK_BUCKETS = (("1", 1, 1), ("2-5", 2, 5), ("6-20", 6, 20), ("21-50", 21, 50), (">50", 51, 51))
VOCABULARY_TOP_K = 5229
COVERAGE_TOP = 20
Z_ALPHA = 1.959964
"""Two-sided 5 % test."""
Z_POWER = 0.841621
"""80 % power."""

Runs = Mapping[str, Mapping[str, Sequence[Trajectory]]]
Report = dict[str, Any]


def final_rank(t: Trajectory) -> int:
    rank = t.ranks[-1]
    return MISSING_RANK if rank is None else rank


def hit(k: int) -> Metric:
    return lambda t: float(final_rank(t) <= k)


def miss(k: int) -> Metric:
    return lambda t: float(final_rank(t) > k)


def mrr(t: Trajectory) -> float:
    return reciprocal(t.ranks[-1])


def mean_rank(t: Trajectory) -> float:
    return float(final_rank(t))


def questions(t: Trajectory) -> float:
    return float(t.questions[-1])


def upto(trajectories: Sequence[Trajectory], round_: int) -> list[Trajectory]:
    """The trajectories frozen after ``round_`` rounds of answers."""
    return [replace(t, ranks=t.ranks[: round_ + 1]) for t in trajectories]


def subset(trajectories: Sequence[Trajectory], cases: Iterable[str]) -> list[Trajectory]:
    wanted = set(cases)
    return [t for t in trajectories if t.case_id in wanted]


def _column(trajectories: Sequence[Trajectory], metric: Metric, order: Sequence[str]) -> Any:
    by_case = {t.case_id: t for t in trajectories}
    return np.array([metric(by_case[c]) for c in order], dtype=np.float64)


def resampled_means(columns: Sequence[tuple[Sequence[Trajectory], Metric]]) -> tuple[Any, Any]:
    """Observed column means and their means over the case resamples ``paired_bootstrap`` draws.

    Cases follow the order of the first column, as in ``paired_bootstrap``.
    """
    order = [t.case_id for t in columns[0][0]]
    matrix = np.column_stack([_column(ts, metric, order) for ts, metric in columns])
    rng = np.random.default_rng(SEED)
    samples = rng.integers(0, len(order), size=(RESAMPLES, len(order)))
    return matrix.mean(axis=0), matrix[samples].mean(axis=1)


def interval(point: float, draws: Any) -> dict[str, float]:
    """95 % percentile interval; resamples where a ratio is undefined are left out."""
    finite = draws[np.isfinite(draws)]
    if not len(finite):
        return {"value": float("nan"), "low": float("nan"), "high": float("nan")}
    low, high = np.percentile(finite, [2.5, 97.5])
    return {"value": float(point), "low": float(low), "high": float(high)}


def compare(after: Sequence[Trajectory], before: Sequence[Trajectory], metric: Metric) -> Report:
    """Paired change of ``metric``: absolute (``paired_bootstrap``) and relative to ``before``."""
    observed, draws = resampled_means([(after, metric), (before, metric)])
    with np.errstate(divide="ignore", invalid="ignore"):
        relative = draws[:, 0] / draws[:, 1] - 1
        point = observed[0] / observed[1] - 1 if observed[1] else float("nan")
    return {
        "before": float(observed[1]),
        "after": float(observed[0]),
        "delta": paired_bootstrap(after, before, metric),
        "relative": interval(point, relative),
    }


def gain_ratio(
    agent: Sequence[Trajectory],
    baseline: Sequence[Trajectory],
    none: Sequence[Trajectory],
    metric: Metric,
) -> Report:
    """The agent's gain over ``none`` as a multiple of the baseline's, overall and per question."""
    observed, draws = resampled_means(
        [
            (agent, metric),
            (baseline, metric),
            (none, metric),
            (agent, questions),
            (baseline, questions),
        ]
    )

    def ratios(values: Any) -> tuple[Any, Any]:
        total = (values[..., 0] - values[..., 2]) / (values[..., 1] - values[..., 2])
        return total, total * values[..., 4] / values[..., 3]

    with np.errstate(divide="ignore", invalid="ignore"):
        total_draws, per_question_draws = ratios(draws)
        total, per_question = ratios(observed)
    return {
        "total": interval(float(total), total_draws),
        "per_question": interval(float(per_question), per_question_draws),
        "baseline_gain": paired_bootstrap(baseline, none, metric),
    }


def movement(after: Sequence[Trajectory], before: Sequence[Trajectory]) -> Report:
    """Cases whose true gene moved up, down or stayed, and by how many places."""
    start = {t.case_id: final_rank(t) for t in before}
    shifts = [start[t.case_id] - final_rank(t) for t in after]
    up = [s for s in shifts if s > 0]
    down = [-s for s in shifts if s < 0]
    n = len(shifts)
    return {
        "n": n,
        "up": len(up),
        "down": len(down),
        "same": n - len(up) - len(down),
        "reached_first": sum(1 for t in after if start[t.case_id] > 1 and final_rank(t) == 1),
        "lost_first": sum(1 for t in after if start[t.case_id] == 1 and final_rank(t) > 1),
        "up_share": len(up) / n,
        "down_share": len(down) / n,
        "mean_places_up": statistics.fmean(up) if up else 0.0,
        "median_places_up": statistics.median(up) if up else 0.0,
        "mean_places_down": statistics.fmean(down) if down else 0.0,
        "median_places_down": statistics.median(down) if down else 0.0,
    }


def lift(agent: Sequence[Trajectory], none: Sequence[Trajectory]) -> Report:
    """The standard block: top-1 (and its misses), top-5 and MRR changes, movement, questions."""
    ids = [t.case_id for t in agent]
    base = subset(none, ids)
    return {
        "n": len(agent),
        "top1": compare(agent, base, hit(1)),
        "miss1": compare(agent, base, miss(1)),
        "top5": compare(agent, base, hit(5)),
        "mrr": compare(agent, base, mrr),
        "movement": movement(agent, base),
        "questions": statistics.fmean(t.questions[-1] for t in agent) if agent else 0.0,
    }


def headline(runs: Runs) -> Report:
    agent, none = runs["chosen"]["agent"], runs["chosen"]["none"]
    report: Report = {f"top{k}": compare(agent, none, hit(k)) for k in (1, 3, 5, 10, 20)}
    report["mrr"] = compare(agent, none, mrr)
    report.update({f"miss{k}": compare(agent, none, miss(k)) for k in (1, 5, 10)})
    report["mean_rank"] = compare(agent, none, mean_rank)
    report["median_rank"] = {
        "before": statistics.median(final_rank(t) for t in none),
        "after": statistics.median(final_rank(t) for t in agent),
    }
    report["movement"] = movement(agent, none)
    report["questions"] = statistics.fmean(t.questions[-1] for t in agent)
    return report


def where_it_acts(runs: Runs) -> Report:
    agent, none = runs["chosen"]["agent"], runs["chosen"]["none"]
    asked = [t for t in agent if t.questions[-1] > 0]
    silent = [t for t in agent if t.questions[-1] == 0]
    rounds_with_questions = Counter(
        sum(1 for k in ROUNDS[1:] if t.questions[k] > t.questions[k - 1]) for t in asked
    )
    return {
        "asked": lift(asked, none),
        "asked_rounds": dict(sorted(rounds_with_questions.items())),
        "silent": {
            "n": len(silent),
            "top1": statistics.fmean(hit(1)(t) for t in silent),
            "by_stop_reason": {
                reason: {
                    "n": sum(1 for t in silent if t.stop_reason == reason),
                    "top1": statistics.fmean(hit(1)(t) for t in silent if t.stop_reason == reason),
                }
                for reason in sorted({t.stop_reason for t in silent})
            },
        },
        "confident_stops": {
            "n": sum(1 for t in agent if t.stop_reason == "confident"),
            "top1": statistics.fmean(hit(1)(t) for t in agent if t.stop_reason == "confident"),
        },
    }


def per_round(runs: Runs) -> Report:
    chosen = runs["chosen"]
    report: Report = {}
    for policy in ("agent", *BASELINES):
        ts = chosen[policy]
        by_round = summarize(ts)["by_round"]
        assert isinstance(by_round, dict)
        rows = []
        for k in ROUNDS:
            row = by_round[str(k)]
            entry: Report = {
                "round": k,
                "mrr": row["mrr"],
                "top1": row["top1"],
                "questions": row["questions"],
            }
            if k > 0:
                previous = by_round[str(k - 1)]
                asked = row["questions"] - previous["questions"]
                entry["marginal_mrr"] = paired_bootstrap(upto(ts, k), upto(ts, k - 1), mrr)
                entry["marginal_top1"] = paired_bootstrap(upto(ts, k), upto(ts, k - 1), hit(1))
                entry["marginal_questions"] = asked
                entry["mrr_per_question"] = (row["mrr"] - previous["mrr"]) / asked
                entry["top1_pp_per_question"] = 100 * (row["top1"] - previous["top1"]) / asked
            rows.append(entry)
        last, first = rows[-1], rows[0]
        for entry in rows[1:]:
            entry["share_of_final_mrr_gain"] = (entry["mrr"] - first["mrr"]) / (
                last["mrr"] - first["mrr"]
            )
        report[policy] = {
            "rounds": rows,
            "mrr_per_question": (last["mrr"] - first["mrr"]) / last["questions"],
            "top1_pp_per_question": 100 * (last["top1"] - first["top1"]) / last["questions"],
        }
    return report


def baselines(runs: Runs) -> Report:
    chosen = runs["chosen"]
    agent, none = chosen["agent"], chosen["none"]
    report: Report = {}
    for policy in BASELINES:
        other = chosen[policy]
        report[policy] = {
            "vs_none": lift(other, none),
            "agent_vs": {
                "top1": compare(agent, other, hit(1)),
                "top5": compare(agent, other, hit(5)),
                "mrr": compare(agent, other, mrr),
            },
            "times_the_gain": {
                "top1": gain_ratio(agent, other, none, hit(1)),
                "mrr": gain_ratio(agent, other, none, mrr),
            },
        }
    return report


def robustness(runs: Runs) -> Report:
    report: Report = {}
    for name in ("chosen", "dropout", "noiseless", "medico", "before_tuning_dropout"):
        run = runs[name]
        asked = [t for t in run["agent"] if t.questions[-1] > 0]
        report[name] = {
            "all": lift(run["agent"], run["none"]),
            "asked": lift(asked, run["none"]),
            "agent_vs_most_frequent": {
                "top1": compare(run["agent"], run["most_frequent_unreported"], hit(1)),
                "top10": compare(run["agent"], run["most_frequent_unreported"], hit(10)),
                "mrr": compare(run["agent"], run["most_frequent_unreported"], mrr),
            },
        }
    return report


def worse(none: Sequence[Trajectory]) -> Metric:
    """1 if the true gene ended below where the model alone put it."""
    start = {t.case_id: final_rank(t) for t in none}
    return lambda t: float(final_rank(t) > start[t.case_id])


def tuning(runs: Runs) -> Report:
    none = runs["chosen"]["none"]
    after, before = runs["chosen"]["agent"], runs["before_tuning"]["agent"]
    report: Report = {
        "before_vs_none": lift(before, none),
        "after_vs_before": {
            "top1": compare(after, before, hit(1)),
            "mrr": compare(after, before, mrr),
        },
        "worse": compare(after, before, worse(none)),
        "questions": {
            "before": statistics.fmean(t.questions[-1] for t in before),
            "after": statistics.fmean(t.questions[-1] for t in after),
        },
        "before_tuning_policies": {
            policy: lift(ts, none)
            for policy, ts in runs["before_tuning"].items()
            if policy != "none"
        },
    }
    dropout_after = runs["dropout"]["agent"]
    dropout_before = runs["before_tuning_dropout"]["agent"]
    report["dropout_worse"] = compare(dropout_after, dropout_before, worse(none))
    report["dropout_mrr"] = compare(dropout_after, dropout_before, mrr)
    report["ablations"] = {}
    for name in ("ablation_model_sees_present", "ablation_t3", "ablation_old_stops"):
        variant = runs[name]["agent"]
        report["ablations"][name] = {
            "variant_vs_none": lift(variant, none),
            "chosen_vs_variant": {
                "top1": compare(after, variant, hit(1)),
                "mrr": compare(after, variant, mrr),
            },
        }
    return report


def subgroups(runs: Runs, cases: Sequence[Case]) -> Report:
    agent, none = runs["chosen"]["agent"], runs["chosen"]["none"]
    start = {t.case_id: final_rank(t) for t in none}
    groups: dict[str, dict[str, list[str]]] = {
        "source": {},
        "initial_rank": {label: [] for label, _, _ in RANK_BUCKETS},
        "reported_findings": {},
        "negated_finding": {},
    }
    for case in cases:
        groups["source"].setdefault(case.source, []).append(case.case_id)
        for label, low, high in RANK_BUCKETS:
            if low <= start[case.case_id] <= high:
                groups["initial_rank"][label].append(case.case_id)
        groups["reported_findings"].setdefault(str(len(case.present)), []).append(case.case_id)
        negated = "con" if case.absent else "sin"
        groups["negated_finding"].setdefault(negated, []).append(case.case_id)
    report: Report = {}
    for dimension, members in groups.items():
        report[dimension] = {}
        for label, ids in sorted(members.items()):
            chosen = subset(agent, ids)
            asked = sum(1 for t in chosen if t.questions[-1] > 0)
            report[dimension][label] = {**lift(chosen, none), "asked": asked}
    return report


def live(path: Path) -> Report:
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload["summary"]
    seconds = summary["question_call_seconds"]
    by_mode: dict[str, list[float]] = {}
    for record in payload["rounds"]:
        for call in record["calls"]:
            by_mode.setdefault(record["mode"], []).append(call["seconds"])
    first_choice = sum(1 for r in payload["rounds"] if 1 in r["chosen_positions"])
    return {
        "model": summary["model"],
        "rounds": summary["question_rounds"],
        "rounds_with_retry": summary["rounds_with_retry"],
        "rounds_fell_back": summary["rounds_fell_back"],
        "warnings": sum(len(r["warnings"]) for r in payload["rounds"]),
        "rounds_with_first_candidate": first_choice,
        "share_in_ig_top3": summary["share_chosen_in_ig_top3"],
        "mean_gain_ratio": summary["mean_gain_ratio"],
        "min_gain_ratio": summary["min_gain_ratio"],
        "question_seconds": {
            "n": len(seconds),
            "min": min(seconds),
            "median": statistics.median(seconds),
            "mean": statistics.fmean(seconds),
            "max": max(seconds),
            "by_mode_mean": {m: statistics.fmean(v) for m, v in by_mode.items()},
        },
        "notes_seconds": summary["notes_call_seconds"],
        "notes": {k: v for k, v in payload["notes"].items() if k != "rows"},
        "notes_total": len(payload["notes"]["rows"]),
        "budget_spent_total": summary["budget_spent_total"],
    }


def compute_time(runs: Runs) -> Report:
    """Wall time of the service's own work per request (model calls come from the cache)."""
    report: Report = {}
    for name in ("chosen", "before_tuning"):
        ordered = sorted(1000 * s for t in runs[name]["agent"] for s in t.elapsed_s[:-1])
        report[name] = {
            "question_rounds": len(ordered),
            "median_ms": statistics.median(ordered),
            "p90_ms": ordered[int(0.9 * (len(ordered) - 1))],
            "max_ms": ordered[-1],
            "mean_s_by_round": summarize(runs[name]["agent"])["mean_request_seconds"],
        }
    return report


def coverage(cases: Sequence[Case]) -> Report:
    """Model genes the knowledge base can reason about: disease annotations exist for them."""
    kb = open_kb()
    model = real_model()
    vocabulary = model.rank(["HP:0001250"], VOCABULARY_TOP_K).scores
    annotated = {g for g in vocabulary if kb.annotated_diseases_for_gene(g)}
    in_top: list[int] = []
    for case in cases:
        scores = model.rank(sorted(case.present), TOP_K).scores
        top = sorted(scores, key=lambda g: -scores[g])[:COVERAGE_TOP]
        in_top.append(sum(1 for g in top if g in annotated))
    return {
        "vocabulary": len(vocabulary),
        "annotated": len(annotated),
        "share": len(annotated) / len(vocabulary),
        f"mean_annotated_in_top{COVERAGE_TOP}": statistics.fmean(in_top),
        f"cases_with_full_top{COVERAGE_TOP}": sum(1 for n in in_top if n == COVERAGE_TOP),
        "cases": len(in_top),
    }


def validation_size(runs: Runs) -> Report:
    """Confirmed cases a paired before/after comparison would need to detect each simulated gain.

    Normal approximation with the spread of the per-case MRR change seen in the
    simulation; real patients may spread differently.
    """
    report: Report = {}
    for name in ("chosen", "dropout"):
        start = {t.case_id: mrr(t) for t in runs[name]["none"]}
        deltas = [mrr(t) - start[t.case_id] for t in runs[name]["agent"]]
        mean, sd = statistics.fmean(deltas), statistics.stdev(deltas)
        report[name] = {
            "mean_mrr_delta": mean,
            "sd_mrr_delta": sd,
            "cases_for_80_power": math.ceil(((Z_ALPHA + Z_POWER) * sd / mean) ** 2),
        }
    return report


def load_runs(results: Path) -> dict[str, dict[str, list[Trajectory]]]:
    runs = {}
    for name, filename in RUNS.items():
        path = results / filename
        if not path.is_file():
            raise SystemExit(f"missing {path}: see docs/EVALUACION.md, 'Cómo reproducir'")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["meta"]["split"] != "test":
            raise SystemExit(f"{path} is not a TEST run")
        runs[name] = trajectories(payload)
    return runs


def compute(results: Path, *, with_coverage: bool = True) -> Report:
    runs = load_runs(results)
    cases = load("test", CASES_PATH)
    report: Report = {
        "1_headline": headline(runs),
        "2_where_it_acts": where_it_acts(runs),
        "3_per_round": per_round(runs),
        "4_baselines": baselines(runs),
        "5_robustness": robustness(runs),
        "6_tuning": tuning(runs),
        "7_subgroups": subgroups(runs, cases),
        "8_live": live(results / "live_claude.json"),
        "9_compute": compute_time(runs),
    }
    if with_coverage:
        report["9_coverage"] = coverage(cases)
    report["10_validation"] = validation_size(runs)
    return report


def _walk(value: Any, path: str, emit: Callable[[str, str], None]) -> None:
    if isinstance(value, dict):
        if {"delta", "low", "high"} <= value.keys() or {"value", "low", "high"} <= value.keys():
            point = value.get("delta", value.get("value"))
            emit(path, f"{point:+.4f} [{value['low']:+.4f}, {value['high']:+.4f}]")
            return
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), emit)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", emit)
    elif isinstance(value, float):
        emit(path, f"{value:.4f}")
    else:
        emit(path, str(value))


def render(report: Report) -> str:
    lines: list[str] = []
    _walk(report, "", lambda key, text: lines.append(f"{key} = {text}"))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--results", type=Path, default=RESULTS_DIR)
    parser.add_argument("--json", type=Path, help="also write every figure as JSON")
    parser.add_argument(
        "--no-coverage",
        action="store_true",
        help="skip the coverage section (needs data/kb.sqlite and the model cache or service)",
    )
    args = parser.parse_args(argv)
    report = compute(args.results, with_coverage=not args.no_coverage)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(render(report))


if __name__ == "__main__":
    main()
