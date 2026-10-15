"""Render simulation results as Markdown.

    python -m eval.report eval/results/test.json [more.json ...] [--out report.md]

For runs recorded without early stopping, the config's stop rules are
replayed from the trace (see ``eval.metrics``).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from eval.harness import AgentConfig
from eval.metrics import (
    METRICS,
    NO_STOPS,
    ROUNDS,
    StopRules,
    Trajectory,
    apply_stops,
    paired_bootstrap,
    summarize,
)
from eval.policies import CaseTrace

HEADLINE_METRICS = ("top1", "top5", "top10", "mrr")


def trajectories(payload: Mapping[str, Any]) -> dict[str, list[Trajectory]]:
    meta = payload["meta"]
    config = AgentConfig.from_dict(meta["config"])
    rules = (
        NO_STOPS
        if meta["early_stop"]
        else StopRules(config.confident_threshold, config.min_information_gain)
    )
    return {
        policy: [
            apply_stops(CaseTrace.from_dict(t), rules, uses_gain=policy == "agent") for t in traces
        ]
        for policy, traces in payload["traces"].items()
    }


def pct(value: float) -> str:
    return f"{100 * value:.1f}"


def round_table(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [
        "| política | ronda | top-1 | top-3 | top-5 | top-10 | top-20 | MRR | mediana | "
        "empeora | mejora | preguntas |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for policy, summary in summaries.items():
        rounds = (0,) if policy == "none" else ROUNDS
        for k in rounds:
            row = summary["by_round"][str(k)]
            median = row["median_rank"]
            median_text = ">50" if median > 50 else f"{median:g}"
            lines.append(
                f"| {policy} | {k} | {pct(row['top1'])} | {pct(row['top3'])} | "
                f"{pct(row['top5'])} | {pct(row['top10'])} | {pct(row['top20'])} | "
                f"{row['mrr']:.3f} | {median_text} | {pct(row['worse'])} | "
                f"{pct(row['better'])} | {row['questions']:.2f} |"
            )
    return "\n".join(lines)


def stop_table(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    lines = ["| política | motivos de cierre | s por ronda (media) |", "|---|---|---|"]
    for policy, summary in summaries.items():
        reasons = ", ".join(f"{k} {v}" for k, v in summary["stop_reasons"].items())
        seconds = " / ".join(f"{v * 1000:.0f} ms" for v in summary["mean_request_seconds"].values())
        lines.append(f"| {policy} | {reasons} | {seconds or '—'} |")
    return "\n".join(lines)


def delta_table(by_policy: Mapping[str, Sequence[Trajectory]], reference: str = "agent") -> str:
    if reference not in by_policy or len(by_policy) < 2:
        return ""
    lines = [
        "| comparación | Δ top-1 (pp) | Δ top-5 (pp) | Δ top-10 (pp) | Δ MRR |",
        "|---|---|---|---|---|",
    ]
    for other in by_policy:
        if other == reference:
            continue
        cells = []
        for name in HEADLINE_METRICS:
            result = paired_bootstrap(by_policy[reference], by_policy[other], METRICS[name])
            if name == "mrr":
                cells.append(
                    f"{result['delta']:+.3f} [{result['low']:+.3f}, {result['high']:+.3f}]"
                )
            else:
                cells.append(
                    f"{100 * result['delta']:+.1f} [{100 * result['low']:+.1f}, "
                    f"{100 * result['high']:+.1f}]"
                )
        lines.append(f"| {reference} vs {other} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render(path: Path) -> str:
    payload = json.loads(path.read_text())
    meta = payload["meta"]
    by_policy = trajectories(payload)
    summaries = {policy: summarize(ts) for policy, ts in by_policy.items()}
    config = AgentConfig.from_dict(meta["config"])
    parts = [
        f"### {path.stem}",
        "",
        f"split={meta['split']} casos={meta['cases']} respondedor={meta['answerer']} "
        f"modo={meta['mode']} cierre_anticipado={'sí' if meta['early_stop'] else 'reproducido'} "
        f"tiempo={meta['wall_seconds']} s",
        "",
        f"config: {config.label()} umbral={config.confident_threshold:g} "
        f"ig_min={config.min_information_gain:g}",
        "",
        round_table(summaries),
        "",
        stop_table(summaries),
    ]
    deltas = delta_table(by_policy)
    if deltas:
        parts += [
            "",
            "Diferencias pareadas tras la última ronda (bootstrap 95 %, 2000 remuestreos):",
            "",
            deltas,
        ]
    return "\n".join(parts) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    text = "\n".join(render(path) for path in args.results)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
