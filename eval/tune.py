"""Tune the agent on the TUNING split only.

Stage 1 is a full factorial over the re-ranking knobs (strategy, temperature,
likelihood background, missing frequency); stage 2 sweeps the likelihood's
answer-noise floor ``epsilon`` around the stage-1 winner. Stage 3 explores
temperatures around the winner, below 1 included, but is only reported: a
temperature below 1 sharpens the model beyond its own softmax, which these
in-distribution cases reward and real patients would not.

Every configuration is played once without early stopping; the stop rules
(confidence threshold, minimum information gain, and the selector's
``p_unknown``, which only rescales the gains) are replayed afterwards for a
grid of values. Among configurations within ``MRR_TOLERANCE`` of the best, the
one that changes the fewest code defaults wins.

    python -m eval.tune [--out eval/results/tuning_grid.json]

Writes the whole grid and ``eval/results/chosen_config.json``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from eval.cases import load
from eval.harness import RESULTS_DIR, AgentConfig
from eval.metrics import NO_STOPS, StopRules, apply_stops, summarize
from eval.policies import CaseTrace
from eval.run_simulation import simulate

from diagnosticar_preguntas.ranking.strategy import RerankStrategy

STRATEGIES = (RerankStrategy.MODEL_SEES_PRESENT, RerankStrategy.FIXED_PRIOR)
TEMPERATURES = (1.0, 2.0, 3.0, 5.0)
BACKGROUNDS = (0.02, 0.05, 0.1)
MISSING_FREQUENCIES = (0.3, 0.5)
EPSILONS = (0.02, 0.05, 0.1)
EXPLORATORY_TEMPERATURES = (0.5, 0.75, 1.5)
CONFIDENT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
MIN_GAINS = (0.0, 0.01, 0.02, 0.05, 0.1)
P_UNKNOWNS = (0.1, 0.2, 0.3)
RUN_P_UNKNOWN = 0.2
# Differences below this are noise on 200 cases; ties go to the cheaper setting.
MRR_TOLERANCE = 0.005


def stage1_configs() -> list[AgentConfig]:
    return [
        AgentConfig(strategy=s, temperature=t, background=b, missing_frequency=m)
        for s, t, b, m in itertools.product(
            STRATEGIES, TEMPERATURES, BACKGROUNDS, MISSING_FREQUENCIES
        )
    ]


def stage2_configs(best: AgentConfig) -> list[AgentConfig]:
    configs = []
    for epsilon in EPSILONS:
        if epsilon == best.epsilon:
            continue
        for background in BACKGROUNDS:
            if background >= epsilon:
                configs.append(replace(best, epsilon=epsilon, background=background))
    return configs


def stage3_configs(best: AgentConfig) -> list[AgentConfig]:
    return [replace(best, temperature=t) for t in EXPLORATORY_TEMPERATURES]


def changes_from_default(config: AgentConfig) -> int:
    default = AgentConfig().to_dict()
    return sum(1 for key, value in config.to_dict().items() if default[key] != value)


def pick_config(rows: Sequence[dict[str, Any]]) -> AgentConfig:
    top = max(r["mrr"] for r in rows)
    close = [r for r in rows if r["mrr"] >= top - MRR_TOLERANCE]
    row = min(
        close,
        key=lambda r: (changes_from_default(AgentConfig.from_dict(r["config"])), -r["mrr"]),
    )
    return AgentConfig.from_dict(row["config"])


def final_row(traces: Sequence[CaseTrace], rules: StopRules) -> dict[str, Any]:
    summary = summarize([apply_stops(t, rules, uses_gain=True) for t in traces])
    by_round: dict[str, dict[str, float]] = summary["by_round"]  # type: ignore[assignment]
    return {
        "round0_mrr": by_round["0"]["mrr"],
        "round1_mrr": by_round["1"]["mrr"],
        "mrr": by_round["3"]["mrr"],
        "top1": by_round["3"]["top1"],
        "top5": by_round["3"]["top5"],
        "top10": by_round["3"]["top10"],
        "worse": by_round["3"]["worse"],
        "questions": by_round["3"]["questions"],
        "stop_reasons": summary["stop_reasons"],
    }


def evaluate(results: dict[tuple[AgentConfig, str], list[CaseTrace]]) -> list[dict[str, Any]]:
    """One row per config: never stopping early, plus the MRR under the code's stop rules."""
    default = AgentConfig()
    default_rules = StopRules(default.confident_threshold, default.min_information_gain)
    rows = []
    for (config, _), traces in results.items():
        with_stops = final_row(traces, default_rules)
        rows.append(
            {
                "config": config.to_dict(),
                **final_row(traces, NO_STOPS),
                "mrr_default_stops": with_stops["mrr"],
                "questions_default_stops": with_stops["questions"],
            }
        )
    return rows


def stop_grid(traces: Sequence[CaseTrace]) -> list[dict[str, Any]]:
    rows = []
    for threshold, min_gain, p_unknown in itertools.product(
        CONFIDENT_THRESHOLDS, MIN_GAINS, P_UNKNOWNS
    ):
        rules = StopRules(threshold, min_gain, gain_scale=(1 - p_unknown) / (1 - RUN_P_UNKNOWN))
        rows.append(
            {
                "confident_threshold": threshold,
                "min_information_gain": min_gain,
                "p_unknown": p_unknown,
                **final_row(traces, rules),
            }
        )
    return rows


def best_of(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Highest MRR; among rows within the tolerance, the one asking fewest questions."""
    top = max(r["mrr"] for r in rows)
    close = [r for r in rows if r["mrr"] >= top - MRR_TOLERANCE]
    return min(close, key=lambda r: (r["questions"], -r["mrr"]))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "tuning_grid.json")
    parser.add_argument("--workers", type=int, default=14)
    args = parser.parse_args(argv)

    cases = load("tuning")
    started = time.perf_counter()

    def run(configs: Sequence[AgentConfig]) -> dict[tuple[AgentConfig, str], list[CaseTrace]]:
        return simulate(cases, ["agent"], configs, early_stop=False, workers=args.workers)

    stage1 = run(stage1_configs())
    stage1_rows = evaluate(stage1)
    stage2 = run(stage2_configs(pick_config(stage1_rows)))
    stage2_rows = evaluate(stage2)
    rows = stage1_rows + stage2_rows
    best = pick_config(rows)
    stage3 = run(stage3_configs(best))
    stage3_rows = evaluate(stage3)
    all_traces = {**stage1, **stage2, **stage3}
    stops = stop_grid(all_traces[(best, "agent")])
    # p_unknown only rescales the gains, i.e. it is min_information_gain in disguise; keep it.
    chosen_stops = best_of([r for r in stops if r["p_unknown"] == RUN_P_UNKNOWN])
    default_stops = stop_grid(all_traces[(AgentConfig(), "agent")])
    chosen = replace(
        best,
        confident_threshold=chosen_stops["confident_threshold"],
        min_information_gain=chosen_stops["min_information_gain"],
        p_unknown=chosen_stops["p_unknown"],
    )
    wall = time.perf_counter() - started
    payload = {
        "meta": {"split": "tuning", "cases": len(cases), "wall_seconds": round(wall, 1)},
        "stage1": stage1_rows,
        "stage2": stage2_rows,
        "stage3_exploratory": stage3_rows,
        "stops_for_best": stops,
        "stops_for_default": default_stops,
        "chosen": chosen.to_dict(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1))
    (RESULTS_DIR / "chosen_config.json").write_text(
        json.dumps({"config": chosen.to_dict(), "tuning": chosen_stops}, indent=1)
    )
    print(f"grid of {len(rows) + len(stage3_rows)} configs in {wall:.0f}s; chosen: {chosen}")


if __name__ == "__main__":
    main()
