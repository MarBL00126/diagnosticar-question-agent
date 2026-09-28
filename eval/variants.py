"""Structural variants of how the answers reweight the genes, on real and de-circularised patients.

    python -m eval.variants [--workers 4]

Question selection is the same everywhere (information gain over the code's
likelihood); only what the answers do to the ranking changes. ``p`` is the
noisy-OR of the frequencies annotated to a term or its descendants for a
disease (0.3 when unstated), the quantity ``ranking/likelihood.py`` uses
before its background floor and clipping.

* ``V1`` enrich only: every "sí" is sent to the model (``model_sees_present``)
  and nothing else reweights the genes; the model alone decides.
* ``V2`` explicit exclusions: V1, plus every present finding (reported or
  "sí") multiplies by epsilon the diseases that exclude it (``NOT`` or
  ``Excluded`` on the term or an ancestor). Every other disease, and every
  "no", is neutral: an unannotated finding is no evidence either way.
* ``V2_literal``: V1, plus a "no" (or a reported absent finding) multiplies by
  epsilon the diseases that exclude the term. This is the variant as first
  worded; it punishes the diseases that agree with the answer and is run only
  to show that.
* ``V3_k``: ``model_sees_present``; a "sí" (or a present term the model does
  not know) multiplies the diseases annotated with it by ``1 + k·p``, the
  diseases that exclude it by epsilon and the rest by 1; a "no" (or a reported
  absent finding) multiplies the annotated diseases by ``1 - k·p`` and the rest
  by 1. Equivalent to a likelihood with ``P(t | d) = (1 + k·p) / 2`` for
  annotated diseases and 1/2, no information, for the others.
* ``V3fp_k``: V3 with ``fixed_prior``: the "sí" answers are not sent to the
  model, so each counts once.
* ``V4``: the configuration ``eval.explore`` picked (``model_sees_present``,
  epsilon 0.1) with the code's likelihood.
* ``V5``: the code defaults; ``V5_before``: the configuration before tuning.

Scenarios: A (real patients without their article in the knowledge base), B
(annotation hold-out), C (cross-source, both directions) and the simulated
TEST split, all from ``eval.audit``, whose ``none`` traces are the baseline.
A is split by gene into the halves of ``eval.explore``: the variant with the
best MRR on the selection half is the one this run would recommend, and every
variant is reported on the held-out half. Writes
``eval/results/audit/variants/``; nothing here changes a default.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from eval.cases import annotated_phenotypes, eligible_diseases, load, model_vocabulary
from eval.explore import gene_halves
from eval.harness import AgentConfig, open_kb
from eval.metrics import Trajectory, paired_bootstrap, reciprocal
from eval.report import trajectories

from diagnosticar_preguntas.domain import Evidence, ModelRanking, Posterior
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodConfig, LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker, UnseenEvidence
from diagnosticar_preguntas.ranking.strategy import RerankStrategy
from eval import audit

VARIANTS_DIR = audit.AUDIT_DIR / "variants"
EPSILON = 0.02
# Background and clipping this small leave the likelihood's noisy-OR untouched.
RAW = LikelihoodConfig(background=1e-9, missing_frequency=0.3, epsilon=1e-9)
MSP = RerankStrategy.MODEL_SEES_PRESENT
FIXED = RerankStrategy.FIXED_PRIOR
SCENARIOS = ("A", "B", "C_orpha", "C_omim", "simulated")
METRICS = {
    "mrr": lambda t: reciprocal(t.ranks[-1]),
    "top1": lambda t: float(t.ranks[-1] is not None and t.ranks[-1] <= 1),
    "top5": lambda t: float(t.ranks[-1] is not None and t.ranks[-1] <= 5),
}


@dataclass(frozen=True, slots=True)
class Weighting:
    """Multiplicative factors for the evidence; ``None`` leaves that kind of evidence neutral."""

    reward: float | None = None
    penalty: float | None = None
    exclusion: float | None = None
    exclusion_on_no: bool = False


class WeightedReranker(BayesianReranker):
    """``BayesianReranker`` whose likelihood is replaced by the factors of a ``Weighting``.

    The base class multiplies in only what the model did not see; these
    variants decide per kind of evidence instead, so each round's evidence is
    classified in ``rerank``: every present finding (for exclusions), the ones
    to reward (answered "sí", or unknown to the model), and every absent one.
    ``reported`` are the findings of the request's ``present_hpo_ids``.
    """

    def __init__(
        self,
        kb: KnowledgeBase,
        weighting: Weighting,
        reported: frozenset[str],
    ) -> None:
        super().__init__(kb, LikelihoodModel(kb, RAW))
        self._view = kb
        self._weighting = weighting
        self._reported = kb.resolve_all(reported)
        self._exclusions: dict[str, frozenset[str]] = {}
        self._present: frozenset[str] = frozenset()
        self._rewarded: frozenset[str] = frozenset()
        self._absent: frozenset[str] = frozenset()

    def rerank(self, ranking: ModelRanking, evidence: Evidence) -> Posterior:
        kb = self._view
        self._present = kb.resolve_all(evidence.present)
        unknown = kb.resolve_all(ranking.unknown_hpo_ids) & self._present
        self._rewarded = (self._present - self._reported) | unknown
        self._absent = kb.resolve_all(evidence.absent)
        return super().rerank(ranking, evidence)

    def _excludes(self, disease: str, hpo_id: str) -> bool:
        roots = self._exclusions.get(disease)
        if roots is None:
            roots = frozenset(a.hpo_id for a in self._view.annotations(disease) if a.negated)
            self._exclusions[disease] = roots
        return bool(roots) and (
            hpo_id in roots or not roots.isdisjoint(self._view.ancestors(hpo_id))
        )

    def _log_likelihood(self, disease: str, unseen: UnseenEvidence) -> float:
        weighting = self._weighting
        annotated = self._likelihood.profile(disease).supported
        total = 0.0
        for hpo_id in self._present:
            p = annotated.get(hpo_id)
            if p is None:
                if (
                    weighting.exclusion is not None
                    and not weighting.exclusion_on_no
                    and self._excludes(disease, hpo_id)
                ):
                    total += math.log(weighting.exclusion)
            elif weighting.reward is not None and hpo_id in self._rewarded:
                total += math.log1p(weighting.reward * p)
        for hpo_id in self._absent:
            p = annotated.get(hpo_id)
            if p is None:
                if (
                    weighting.exclusion is not None
                    and weighting.exclusion_on_no
                    and self._excludes(disease, hpo_id)
                ):
                    total += math.log(weighting.exclusion)
            elif weighting.penalty is not None:
                total += math.log1p(-weighting.penalty * p)
        return total


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    description: str
    config: AgentConfig
    weighting: Weighting | None = None

    def reranker(self) -> audit.RerankerFor | None:
        weighting = self.weighting
        if weighting is None:
            return None
        return lambda case, kb: WeightedReranker(kb, weighting, frozenset(case.present))


def _config(strategy: RerankStrategy) -> AgentConfig:
    return AgentConfig(strategy=strategy)


VARIANTS = (
    Variant("V1", "enrich only", _config(MSP), Weighting()),
    Variant("V2", "enrich + explicit exclusions", _config(MSP), Weighting(exclusion=EPSILON)),
    Variant(
        "V2_literal",
        "enrich + a 'no' punishes the diseases that exclude the term (as first worded)",
        _config(MSP),
        Weighting(exclusion=EPSILON, exclusion_on_no=True),
    ),
    *(
        Variant(
            f"V3{suffix}_k{k:g}".replace(".", ""),
            f"asymmetric likelihood, k={k:g}, {strategy.value}",
            _config(strategy),
            Weighting(reward=k, penalty=k, exclusion=EPSILON),
        )
        for strategy, suffix in ((MSP, ""), (FIXED, "fp"))
        for k in (0.3, 0.5)
    ),
    Variant(
        "V4",
        "exploration's choice: model_sees_present, epsilon 0.1",
        AgentConfig(strategy=MSP, epsilon=0.1),
    ),
    Variant("V5", "code defaults (fixed_prior, tuned)", AgentConfig()),
    Variant(
        "V5_before",
        "configuration before tuning",
        AgentConfig(
            strategy=MSP,
            temperature=3.0,
            background=0.05,
            missing_frequency=0.5,
            confident_threshold=0.8,
            min_information_gain=0.01,
        ),
    ),
)


def scenario_runs(kb: KnowledgeBase) -> dict[str, audit.Run]:
    model_genes = model_vocabulary()
    pool = annotated_phenotypes(
        kb, (d for ds in eligible_diseases(kb, model_genes).values() for d in ds)
    )
    runs = [
        *(r for r in audit.real_patient_runs(kb, model_genes) if r.name == "A"),
        *(r for r in audit.hold_out_runs(kb, pool) if r.name == "B"),
        *(r for r in audit.cross_source_runs(kb, model_genes, pool) if r.name in SCENARIOS),
        audit.Run(
            "simulated",
            "simulated TEST split",
            load("test"),
            lambda c: kb,
            audit._simulated(kb),
        ),
    ]
    return {r.name: r for r in runs}


def plan(scenarios: Mapping[str, audit.Run]) -> list[audit.Run]:
    return [
        replace(
            run,
            name=f"{variant.name}__{name}",
            description=f"{variant.name} ({variant.description}) on {name}",
            config=variant.config,
            policies=("agent",),
            reranker=variant.reranker(),
        )
        for variant in VARIANTS
        for name, run in scenarios.items()
    ]


# --- figures ---------------------------------------------------------------------------------


@dataclass(slots=True)
class Scenario:
    none: list[Trajectory]
    subsets: dict[str, frozenset[str]] = field(default_factory=dict)


def _informative(payload: Mapping[str, Any]) -> frozenset[str]:
    """Cases where the agent got at least one "sí" or "no"."""
    return frozenset(
        t["case_id"]
        for t in payload["traces"]["agent"]
        if any(a in ("si", "no") for r in t["rounds"] for a in r["answers"])
    )


def _block(agent: Sequence[Trajectory], none: Sequence[Trajectory]) -> dict[str, Any]:
    if not agent:
        return {"n": 0}
    by_case = {t.case_id: t for t in none}
    base = [by_case[t.case_id] for t in agent]
    worse = sum(
        1
        for a, b in zip(agent, base, strict=True)
        if audit._rank(a.ranks[-1]) > audit._rank(b.ranks[-1])
    )
    better = sum(
        1
        for a, b in zip(agent, base, strict=True)
        if audit._rank(a.ranks[-1]) < audit._rank(b.ranks[-1])
    )
    return {
        "n": len(agent),
        "none_mrr": sum(METRICS["mrr"](t) for t in base) / len(base),
        "agent_mrr": sum(METRICS["mrr"](t) for t in agent) / len(agent),
        "worse": worse / len(agent),
        "better": better / len(agent),
        **{name: paired_bootstrap(agent, base, metric) for name, metric in METRICS.items()},
    }


def summary(scenarios: Mapping[str, audit.Run]) -> dict[str, Any]:
    base_none = {name: trajectories(audit._load(name))["none"] for name in SCENARIOS}
    a_cases = scenarios["A"].cases
    pick_genes, report_genes = gene_halves([c.gene for c in a_cases])
    halves = {
        "selection": frozenset(c.case_id for c in a_cases if c.gene in pick_genes),
        "held_out": frozenset(c.case_id for c in a_cases if c.gene in report_genes),
    }
    result: dict[str, Any] = {"halves": {k: len(v) for k, v in halves.items()}, "variants": {}}
    for variant in VARIANTS:
        rows: dict[str, Any] = {}
        for name in SCENARIOS:
            payload = json.loads((VARIANTS_DIR / f"{variant.name}__{name}.json").read_text())
            agent = trajectories(payload)["agent"]
            informative = _informative(payload)
            none = base_none[name]
            blocks = {
                "all": _block(agent, none),
                "informative": _block([t for t in agent if t.case_id in informative], none),
                "answers": audit.answer_counts(payload["traces"]["agent"]),
            }
            if name == "A":
                for half, ids in halves.items():
                    blocks[half] = _block([t for t in agent if t.case_id in ids], none)
                    blocks[f"{half}_informative"] = _block(
                        [t for t in agent if t.case_id in ids & informative], none
                    )
            rows[name] = blocks
        result["variants"][variant.name] = {
            "description": variant.description,
            "config": variant.config.to_dict(),
            "weighting": None
            if variant.weighting is None
            else {
                "reward": variant.weighting.reward,
                "penalty": variant.weighting.penalty,
                "exclusion": variant.weighting.exclusion,
                "exclusion_on_no": variant.weighting.exclusion_on_no,
            },
            **rows,
        }
    selection = {v: r["A"]["selection"]["agent_mrr"] for v, r in result["variants"].items()}
    result["selected_on_selection_half"] = max(selection, key=lambda v: selection[v])
    return result


def _fmt(c: Mapping[str, float], scale: float = 1.0, digits: int = 3) -> str:
    return (
        f"{scale * c['delta']:+.{digits}f} [{scale * c['low']:+.{digits}f}; "
        f"{scale * c['high']:+.{digits}f}]"
    )


def print_table(result: Mapping[str, Any]) -> None:
    print(f"selected on the selection half: {result['selected_on_selection_half']}")
    header = "| variant | A held-out MRR | A held-out top-1 | A held-out top-5 | worse | "
    header += "A informative MRR (n) | B MRR | C_orpha MRR | C_omim MRR | simulated MRR |"
    print(header)
    print("|" + "---|" * 10)
    for name, row in result["variants"].items():
        held, inf = row["A"]["held_out"], row["A"]["held_out_informative"]
        print(
            f"| {name} | {_fmt(held['mrr'])} | {_fmt(held['top1'], 100, 1)} | "
            f"{_fmt(held['top5'], 100, 1)} | {100 * held['worse']:.1f} % | "
            f"{_fmt(inf['mrr'])} ({inf['n']}) | {_fmt(row['B']['all']['mrr'])} | "
            f"{_fmt(row['C_orpha']['all']['mrr'])} | {_fmt(row['C_omim']['all']['mrr'])} | "
            f"{_fmt(row['simulated']['all']['mrr'])} |"
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)

    kb = open_kb()
    audit._STATE["kb"] = kb
    scenarios = scenario_runs(kb)
    if not args.summary_only:
        audit.execute(plan(scenarios), args.workers, out_dir=VARIANTS_DIR)
    result = summary(scenarios)
    (VARIANTS_DIR / "summary.json").write_text(json.dumps(result, indent=1))
    print_table(result)


if __name__ == "__main__":
    main()
