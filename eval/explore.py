"""Exploration, reported only: can existing parameters make the agent safe outside the simulator?

    python -m eval.explore [--workers 4]

Plays the real patients of ``eval.audit``'s run A (knowledge base without each
patient's article) with a grid over the parameters that decide how hard a
single answer pushes the ranking: strategy, temperature, and the likelihood's
background and epsilon. The patients are split by gene into two seeded
halves. The configuration with the highest MRR on the first half is then
reported on the second half, on B and C, and on the simulated TEST split, none
of which took part in the choice, next to the code defaults.

Nothing here changes a default: these are the same patients that measure the
agent, so a configuration picked here needs a fresh set of cases to be
confirmed. Reuses the ``none`` traces of ``python -m eval.audit`` and writes
``eval/results/audit/explore/``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from eval.cases import annotated_phenotypes, eligible_diseases, load, model_vocabulary
from eval.harness import AgentConfig, open_kb, stable_seed
from eval.metrics import METRICS, Trajectory, paired_bootstrap, reciprocal
from eval.report import trajectories

from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from eval import audit

EXPLORE_DIR = audit.AUDIT_DIR / "explore"
STRATEGIES = (RerankStrategy.FIXED_PRIOR, RerankStrategy.MODEL_SEES_PRESENT)
TEMPERATURES = (0.5, 1.0)
BACKGROUNDS = (0.1, 0.2, 0.3, 0.5)
EPSILONS = (0.02, 0.1, 0.2)
# Where the chosen configuration is checked: exploration run -> audit run with the same cases.
CHECKS = {
    "E_B": "B",
    "E_C_orpha": "C_orpha",
    "E_C_omim": "C_omim",
    "E_simulated": "simulated",
}


def grid() -> list[AgentConfig]:
    return [
        AgentConfig(strategy=s, temperature=t, background=b, epsilon=e)
        for s, t, b, e in itertools.product(STRATEGIES, TEMPERATURES, BACKGROUNDS, EPSILONS)
        if e <= b
    ]


def gene_halves(genes: Sequence[str]) -> tuple[frozenset[str], frozenset[str]]:
    """(pick, report): a seeded split of ``genes`` in two halves."""
    order = sorted(genes)
    random.Random(stable_seed(audit.SEED, "explore-split")).shuffle(order)
    middle = len(order) // 2
    return frozenset(order[:middle]), frozenset(order[middle:])


def _agent(payload: Mapping[str, Any]) -> list[Trajectory]:
    return trajectories(payload)["agent"]


def _none(name: str) -> list[Trajectory]:
    return trajectories(audit._load(name))["none"]


def _mrr(ts: Sequence[Trajectory]) -> float:
    return sum(reciprocal(t.ranks[-1]) for t in ts) / len(ts)


def _within(ts: Sequence[Trajectory], ids: frozenset[str]) -> list[Trajectory]:
    return [t for t in ts if t.case_id in ids]


def _compare(first: Sequence[Trajectory], second: Sequence[Trajectory]) -> dict[str, Any]:
    return {name: paired_bootstrap(first, second, metric) for name, metric in METRICS.items()}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    kb = open_kb()
    audit._STATE["kb"] = kb
    model_genes = model_vocabulary()
    pool = annotated_phenotypes(
        kb, (d for ds in eligible_diseases(kb, model_genes).values() for d in ds)
    )
    (base,) = [r for r in audit.real_patient_runs(kb, model_genes) if r.name == "A"]
    configs = grid()
    runs = [
        replace(
            base,
            name=f"E_{index:02d}",
            description=f"as A, agent with {config.label()} eps={config.epsilon:g}",
            config=config,
            policies=("agent",),
        )
        for index, config in enumerate(configs)
    ]
    audit.execute(runs, args.workers, out_dir=EXPLORE_DIR)

    pick_genes, report_genes = gene_halves([c.gene for c in base.cases])
    pick = frozenset(c.case_id for c in base.cases if c.gene in pick_genes)
    report = frozenset(c.case_id for c in base.cases if c.gene in report_genes)
    none_a = _none("A")
    default = _agent(audit._load("A"))
    rows = []
    for run in runs:
        agent = _agent(json.loads((EXPLORE_DIR / f"{run.name}.json").read_text()))
        rows.append(
            {
                "run": run.name,
                "config": run.config.to_dict(),
                "pick_mrr": _mrr(_within(agent, pick)),
                "pick_delta_vs_none": _mrr(_within(agent, pick)) - _mrr(_within(none_a, pick)),
                "all_delta_vs_none": _mrr(agent) - _mrr(none_a),
                "agent": agent,
            }
        )
    best = max(rows, key=lambda r: r["pick_mrr"])
    chosen = AgentConfig.from_dict(best["config"])

    real_runs = [
        *(r for r in audit.hold_out_runs(kb, pool) if r.name == "B"),
        *(r for r in audit.cross_source_runs(kb, model_genes, pool) if r.name.startswith("C_o")),
    ]
    checks = [
        replace(r, name=f"E_{r.name}", config=chosen, policies=("agent",))
        for r in real_runs
        if f"E_{r.name}" in CHECKS
    ]
    simulated = audit.Run(
        "E_simulated",
        "simulated TEST split, agent with the explored configuration",
        load("test"),
        lambda c: kb,
        audit._simulated(kb),
        config=chosen,
        policies=("agent",),
    )
    audit.execute([*checks, simulated], args.workers, out_dir=EXPLORE_DIR)

    held_out = {
        "n": len(report),
        "chosen_vs_none": _compare(_within(best["agent"], report), _within(none_a, report)),
        "default_vs_none": _compare(_within(default, report), _within(none_a, report)),
        "chosen_vs_default": _compare(_within(best["agent"], report), _within(default, report)),
    }
    elsewhere = {}
    for name, reference in CHECKS.items():
        agent = _agent(json.loads((EXPLORE_DIR / f"{name}.json").read_text()))
        reference_payload = audit._load(reference)
        elsewhere[reference] = {
            "n": len(agent),
            "chosen_vs_none": _compare(agent, trajectories(reference_payload)["none"]),
            "chosen_vs_default": _compare(agent, trajectories(reference_payload)["agent"]),
        }
    result = {
        "grid_size": len(rows),
        "pick_cases": len(pick),
        "report_cases": len(report),
        "chosen": chosen.to_dict(),
        "grid": [{k: v for k, v in r.items() if k != "agent"} for r in rows],
        "held_out_half": held_out,
        "elsewhere": elsewhere,
    }
    (EXPLORE_DIR / "summary.json").write_text(json.dumps(result, indent=1))
    _print(result)


def _fmt(c: Mapping[str, float]) -> str:
    return f"{c['delta']:+.3f} [{c['low']:+.3f}; {c['high']:+.3f}]"


def _print(result: Mapping[str, Any]) -> None:
    print("chosen:", result["chosen"])
    for row in sorted(result["grid"], key=lambda r: -r["pick_mrr"])[:10]:
        config = AgentConfig.from_dict(row["config"])
        print(
            f"  {config.label()} eps={config.epsilon:g}: pick MRR {row['pick_mrr']:.3f} "
            f"(vs none {row['pick_delta_vs_none']:+.3f}; all {row['all_delta_vs_none']:+.3f})"
        )
    held = result["held_out_half"]
    print(f"held-out half (n={held['n']}):")
    for key in ("chosen_vs_none", "default_vs_none", "chosen_vs_default"):
        print(f"  {key}: MRR {_fmt(held[key]['mrr'])} top-1 {_fmt(held[key]['top1'])}")
    for name, check in result["elsewhere"].items():
        print(
            f"{name} (n={check['n']}): chosen vs none MRR {_fmt(check['chosen_vs_none']['mrr'])}"
            f"; chosen vs default MRR {_fmt(check['chosen_vs_default']['mrr'])}"
        )


if __name__ == "__main__":
    main()
