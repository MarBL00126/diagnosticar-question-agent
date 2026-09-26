"""Play simulated sessions for a split and a set of policies; write every trace to JSON.

    python -m eval.run_simulation --split test --policies none,agent \\
        --config eval/results/chosen_config.json --out eval/results/test.json

Traces are recorded with the service's real stop rules unless
``--no-early-stop`` is given (used by the tuning grid, which replays the stop
rules afterwards). ``python -m eval.report`` renders the summaries.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from eval.answerer import ANSWERERS
from eval.cases import Case, load
from eval.harness import (
    MODEL_URL,
    AgentConfig,
    CachedModelClient,
    Pipeline,
    build_pipeline,
    open_kb,
    real_model,
)
from eval.policies import POLICIES, CaseTrace, run_case

from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase

_state: dict[str, Any] = {}


def _init_worker(gate: Any) -> None:
    _state["gate"] = gate


def _kb() -> KnowledgeBase:
    if "kb" not in _state:
        _state["kb"] = open_kb()
    kb: KnowledgeBase = _state["kb"]
    return kb


def _pipeline(config: AgentConfig) -> Pipeline:
    pipelines: dict[AgentConfig, Pipeline] = _state.setdefault("pipelines", {})
    if config not in pipelines:
        pipelines[config] = build_pipeline(_kb(), config)
    return pipelines[config]


def _model() -> CachedModelClient:
    if "model" not in _state:
        _state["model"] = real_model(_state.get("gate"))
    model: CachedModelClient = _state["model"]
    return model


def _run_chunk(job: dict[str, Any]) -> list[dict[str, Any]]:
    config = AgentConfig.from_dict(job["config"])
    pipeline = _pipeline(config)
    answerer = ANSWERERS[job["answerer"]]
    mode = Mode(job["mode"])
    traces = []
    for case_data in job["cases"]:
        case = Case.from_dict(case_data)
        trace = run_case(
            job["policy"],
            case,
            pipeline,
            _model(),
            answerer,
            mode=mode,
            early_stop=job["early_stop"],
        )
        traces.append(trace.to_dict())
    return traces


def simulate(
    cases: Sequence[Case],
    policies: Sequence[str],
    configs: Sequence[AgentConfig],
    *,
    answerer: str = "default",
    mode: Mode = Mode.PATIENT,
    early_stop: bool = True,
    workers: int = 8,
    chunk: int = 25,
) -> dict[tuple[AgentConfig, str], list[CaseTrace]]:
    """Traces for every (config, policy), in case order. Runs in forked worker processes."""
    _kb()
    jobs: list[tuple[AgentConfig, str, dict[str, Any]]] = []
    for config in configs:
        for policy in policies:
            for start in range(0, len(cases), chunk):
                jobs.append(
                    (
                        config,
                        policy,
                        {
                            "config": config.to_dict(),
                            "policy": policy,
                            "answerer": answerer,
                            "mode": mode.value,
                            "early_stop": early_stop,
                            "cases": [c.to_dict() for c in cases[start : start + chunk]],
                        },
                    )
                )
    context = multiprocessing.get_context("fork")
    gate = context.BoundedSemaphore(4)
    results: dict[tuple[AgentConfig, str], list[CaseTrace]] = {
        (c, p): [] for c in configs for p in policies
    }
    with ProcessPoolExecutor(
        workers, mp_context=context, initializer=_init_worker, initargs=(gate,)
    ) as pool:
        outputs = pool.map(_run_chunk, [job for _, _, job in jobs])
        for (config, policy, _), output in zip(jobs, outputs, strict=True):
            results[(config, policy)].extend(CaseTrace.from_dict(t) for t in output)
    return results


def load_config(path: Path | None) -> AgentConfig:
    if path is None:
        return AgentConfig()
    return AgentConfig.from_dict(json.loads(path.read_text())["config"])


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--split", choices=("tuning", "test"), required=True)
    parser.add_argument("--policies", default=",".join(POLICIES))
    parser.add_argument(
        "--config", type=Path, help="JSON with a 'config' object (default: code defaults)"
    )
    parser.add_argument("--answerer", choices=sorted(ANSWERERS), default="default")
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.PATIENT.value)
    parser.add_argument("--no-early-stop", action="store_true")
    parser.add_argument("--limit", type=int, help="only the first N cases")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    unknown = sorted(set(policies) - set(POLICIES))
    if unknown:
        sys.exit(f"unknown policies: {unknown}")
    config = load_config(args.config)
    cases = load(args.split)[: args.limit]
    started = time.perf_counter()
    results = simulate(
        cases,
        policies,
        [config],
        answerer=args.answerer,
        mode=Mode(args.mode),
        early_stop=not args.no_early_stop,
        workers=args.workers,
    )
    wall = time.perf_counter() - started
    payload = {
        "meta": {
            "split": args.split,
            "cases": len(cases),
            "policies": policies,
            "config": config.to_dict(),
            "answerer": args.answerer,
            "mode": args.mode,
            "early_stop": not args.no_early_stop,
            "kb_version": _kb().version,
            "model_url": MODEL_URL,
            "wall_seconds": round(wall, 1),
        },
        "traces": {p: [t.to_dict() for t in results[(config, p)]] for p in policies},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload))
    print(f"{len(cases)} cases x {len(policies)} policies in {wall:.1f}s -> {args.out}")


if __name__ == "__main__":
    main()
