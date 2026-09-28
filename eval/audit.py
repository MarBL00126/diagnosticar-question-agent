"""De-circularised evaluations: real patients, held-out annotations, independent curation.

    python -m eval.audit [--only A,B,C,S] [--workers 4]
    python -m eval.audit --summary-only

The simulation of ``eval.run_simulation`` draws patients from the same HPO
annotations the agent reasons with. These runs remove that shared knowledge
in three ways, always with the code defaults (nothing is re-tuned), the
template provider (no LLM) and the policies ``none``, ``agent`` and
``most_frequent_unreported``:

A  Real patients (``eval.phenopackets``): published case reports answer from
   their own record, "no sé" when the article does not mention the finding.
   By default the agent's knowledge base drops every annotation row citing
   the patient's article, because HPO derives part of its annotations from
   these same case reports. Variants: the full knowledge base, clinician
   mode, a record's silence read as "no" (a sensitivity bound, not a
   realistic patient), and up to 5 patients per gene instead of 1.
B  Annotation hold-out: for every TEST case, a random half of the true
   disease's positive annotations is hidden from the agent and the patient
   is drawn only from that half. ``B_control`` keeps the same patients and
   gives the agent the full knowledge base.
C  Cross-source: the patient comes from an Orphanet disease and the agent
   only knows the gene's OMIM diseases (``C_orpha``), or the reverse
   (``C_omim``); genes of the TUNING split are left out. The ``_control``
   runs give the agent both sources.
S  Sensitivity of A to the parameters tuned on simulated patients: one
   parameter changed at a time. Reported only; never used to pick defaults.
   B and C also get a ``_msp`` run with ``model_sees_present``, the only
   change that did not hurt in A.

Traces go to ``eval/results/audit/<run>.json`` in ``eval.run_simulation``'s
format, and every figure of docs/EVALUACION.md (section 11) to
``eval/results/audit/summary.json``.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from eval.answerer import DEFAULT, SimulatedPatient
from eval.cases import (
    Case,
    annotated_phenotypes,
    eligible_diseases,
    load,
    model_vocabulary,
    positive_annotations,
    sample_negated,
    sample_present,
)
from eval.harness import (
    MODEL_URL,
    RESULTS_DIR,
    AgentConfig,
    CachedModelClient,
    Pipeline,
    build_pipeline,
    open_kb,
    real_model,
    stable_seed,
)
from eval.kb_views import AnnotationOverlay, HpoaReferences, hold_out
from eval.metrics import METRICS, MISSING_RANK, Trajectory, paired_bootstrap, reciprocal
from eval.phenopackets import (
    RELEASE,
    SHA256,
    RecordedPatient,
    locate_or_download,
    read_archive,
    select,
)
from eval.policies import Patient, run_case
from eval.report import trajectories

from diagnosticar_preguntas.domain import AnswerValue, Mode
from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase
from diagnosticar_preguntas.ranking.reranker import BayesianReranker
from diagnosticar_preguntas.ranking.strategy import RerankStrategy

AUDIT_DIR = RESULTS_DIR / "audit"
SEED = 20260928
POLICIES = ("none", "agent", "most_frequent_unreported")
CROSS_SOURCE_CASES = 300
CAP_PER_GENE = 5
SIMULATED_RUN = RESULTS_DIR / "test_chosen.json"
COMPARISONS = (
    ("agent", "none"),
    ("agent", "most_frequent_unreported"),
    ("most_frequent_unreported", "none"),
)
SENSITIVITY: Mapping[str, Mapping[str, Any]] = {
    "S_t2": {"temperature": 2.0},
    "S_t3": {"temperature": 3.0},
    "S_bg005": {"background": 0.05},
    "S_mf05": {"missing_frequency": 0.5},
    "S_msp": {"strategy": RerankStrategy.MODEL_SEES_PRESENT},
    "S_before_tuning": {
        "strategy": RerankStrategy.MODEL_SEES_PRESENT,
        "temperature": 3.0,
        "background": 0.05,
        "missing_frequency": 0.5,
        "confident_threshold": 0.8,
        "min_information_gain": 0.01,
    },
}
# Pairs of runs over the same cases whose agents differ only in what they know or how they
# are configured: (first, second) is reported as first - second.
MSP: Mapping[str, Any] = {"strategy": RerankStrategy.MODEL_SEES_PRESENT}
CROSS_RUN = (
    ("A", "A_full_kb"),
    ("B", "B_control"),
    ("C_orpha", "C_orpha_control"),
    ("C_omim", "C_omim_control"),
    *((name, "A") for name in SENSITIVITY),
    ("B_msp", "B"),
    ("C_orpha_msp", "C_orpha"),
    ("C_omim_msp", "C_omim"),
)

KbFor = Callable[[Case], KnowledgeBase]
PatientFor = Callable[[Case, Mode], Patient]
# Replaces the pipeline's re-ranker for one case, given the agent's view of the knowledge base.
RerankerFor = Callable[[Case, KnowledgeBase], BayesianReranker]


@dataclass(slots=True)
class Run:
    name: str
    description: str
    cases: list[Case]
    agent_kb: KbFor
    patient: PatientFor
    mode: Mode = Mode.PATIENT
    config: AgentConfig = field(default_factory=AgentConfig)
    policies: tuple[str, ...] = POLICIES
    answerer: str = DEFAULT.name
    case_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    reranker: RerankerFor | None = None


# Filled before the worker processes fork, so they inherit the cases and views.
_RUNS: dict[str, Run] = {}
_STATE: dict[str, Any] = {}


def _simulated(kb: KnowledgeBase) -> PatientFor:
    return lambda case, mode: SimulatedPatient(kb, case, DEFAULT, mode)


def real_patient_runs(kb: KnowledgeBase, model_genes: frozenset[str]) -> list[Run]:
    archive = locate_or_download()
    selection = select(kb, read_archive(archive), model_genes, real_model())
    one = selection.per_gene(1)
    capped = selection.per_gene(CAP_PER_GENE)
    everyone = {r.case.case_id: r for r in capped}
    genes = {r.case.gene for r in capped}
    references = HpoaReferences.read(
        kb, diseases={d for g in genes for d in kb.diseases_for_gene(g)}
    )
    tuning_genes = {c.gene for c in load("tuning")}

    overrides: dict[str, dict[str, tuple[Annotation, ...]]] = {}
    case_meta: dict[str, dict[str, Any]] = {}
    for real in capped:
        case = real.case
        diseases = kb.diseases_for_gene(case.gene)
        cited = [d for d in diseases if not references.references(d).isdisjoint(real.pmids)]
        overrides[case.case_id] = {d: references.annotations_without(d, real.pmids) for d in cited}
        view = AnnotationOverlay(kb, overrides[case.case_id])
        case_meta[case.case_id] = {
            "gene": case.gene,
            "disease_id": case.disease_id,
            "pmids": sorted(real.pmids),
            "observed": len(real.observed),
            "excluded": len(real.excluded),
            "article_cited_by_gene_annotations": bool(cited),
            "article_cited_by_disease_annotations": case.disease_id in cited,
            "gene_annotated_full_kb": bool(kb.annotated_diseases_for_gene(case.gene)),
            "gene_annotated_without_article": bool(view.annotated_diseases_for_gene(case.gene)),
            "gene_in_tuning_split": case.gene in tuning_genes,
        }

    def without_article(case: Case) -> KnowledgeBase:
        return AnnotationOverlay(kb, overrides[case.case_id])

    def recorded(unrecorded: AnswerValue) -> PatientFor:
        def patient(case: Case, mode: Mode) -> Patient:
            real = everyone[case.case_id]
            return RecordedPatient(kb, real.observed, real.excluded, unrecorded)

        return patient

    honest = recorded(AnswerValue.UNKNOWN)
    meta = {
        "release": RELEASE,
        "sha256": SHA256,
        "phenopackets": selection.total,
        "excluded": dict(selection.excluded),
        "eligible": sum(len(v) for v in selection.by_gene.values()),
        "eligible_genes": len(selection.by_gene),
    }
    one_cases = [r.case for r in one]
    one_meta = {c.case_id: case_meta[c.case_id] for c in one_cases}
    base = {"case_meta": one_meta, "meta": meta, "answerer": "recorded"}
    runs = [
        Run(
            "A",
            "real patients, one per gene, knowledge base without the patient's article",
            one_cases,
            without_article,
            honest,
            **base,
        ),
        Run("A_full_kb", "as A, full knowledge base", one_cases, lambda c: kb, honest, **base),
        Run(
            "A_medico",
            "as A, clinician mode",
            one_cases,
            without_article,
            honest,
            mode=Mode.CLINICIAN,
            **base,
        ),
        Run(
            "A_unrecorded_no",
            "as A, a finding the article does not mention is answered 'no' (sensitivity bound)",
            one_cases,
            without_article,
            recorded(AnswerValue.NO),
            **base,
        ),
        Run(
            "A_cap5",
            f"as A, up to {CAP_PER_GENE} patients per gene",
            [r.case for r in capped],
            without_article,
            honest,
            answerer="recorded",
            case_meta=case_meta,
            meta=meta,
        ),
    ]
    runs += [
        Run(
            name,
            f"as A, agent with {changes}",
            one_cases,
            without_article,
            honest,
            config=replace(AgentConfig(), **changes),
            policies=("none", "agent"),
            **base,
        )
        for name, changes in SENSITIVITY.items()
    ]
    return runs


def hold_out_runs(kb: KnowledgeBase, negation_pool: Sequence[str]) -> list[Run]:
    cases: list[Case] = []
    known: dict[str, tuple[Annotation, ...]] = {}
    hidden: dict[str, tuple[Annotation, ...]] = {}
    case_meta: dict[str, dict[str, Any]] = {}
    for test_case in load("test"):
        disease = test_case.disease_id
        held = hold_out(kb, disease, SEED)
        rng = random.Random(stable_seed("hold-out-case", SEED, disease))
        present = sample_present(kb, held.hidden, rng, rng.randint(3, 6))
        absent = sample_negated(kb, disease, present, negation_pool, rng)
        case = replace(
            test_case,
            case_id=f"holdout-{test_case.case_id}",
            split="holdout",
            present=present,
            absent=absent,
        )
        cases.append(case)
        known[case.case_id], hidden[case.case_id] = held.known, held.hidden
        case_meta[case.case_id] = {
            "positive_annotations": len(positive_annotations(kb, disease)),
            "hidden": len(held.hidden),
        }

    def masked(case: Case) -> KnowledgeBase:
        return AnnotationOverlay(kb, {case.disease_id: known[case.case_id]})

    def patient(case: Case, mode: Mode) -> Patient:
        view = AnnotationOverlay(kb, {case.disease_id: hidden[case.case_id]})
        return SimulatedPatient(view, case, DEFAULT, mode)

    return [
        Run(
            "B",
            "hold-out: patient from a random half of the annotations, agent knows the other half",
            cases,
            masked,
            patient,
            case_meta=case_meta,
        ),
        Run(
            "B_control",
            "as B, agent with the full knowledge base",
            cases,
            lambda c: kb,
            patient,
            case_meta=case_meta,
        ),
        Run(
            "B_msp",
            f"as B, agent with {dict(MSP)}",
            cases,
            masked,
            patient,
            config=replace(AgentConfig(), **MSP),
            policies=("none", "agent"),
            case_meta=case_meta,
        ),
    ]


def cross_source_runs(
    kb: KnowledgeBase, model_genes: frozenset[str], negation_pool: Sequence[str]
) -> list[Run]:
    tuning_genes = {c.gene for c in load("tuning")}
    eligible = eligible_diseases(kb, model_genes)
    runs: list[Run] = []
    for source, other in (("ORPHA", "OMIM"), ("OMIM", "ORPHA")):
        by_gene: dict[str, list[str]] = {}
        for disease in eligible[source]:
            gene = kb.genes_for_disease(disease)[0]
            if gene in tuning_genes:
                continue
            if any(
                d.startswith(f"{other}:") and positive_annotations(kb, d)
                for d in kb.diseases_for_gene(gene)
            ):
                by_gene.setdefault(gene, []).append(disease)
        genes = sorted(by_gene)
        random.Random(stable_seed(SEED, "cross-source", source)).shuffle(genes)
        cases: list[Case] = []
        for index, gene in enumerate(genes[:CROSS_SOURCE_CASES]):
            disease = random.Random(stable_seed(SEED, "cross-disease", gene)).choice(by_gene[gene])
            rng = random.Random(stable_seed(SEED, "cross-case", disease))
            present = sample_present(kb, positive_annotations(kb, disease), rng, rng.randint(3, 6))
            cases.append(
                Case(
                    case_id=f"cross-{source.lower()}-{index:03d}",
                    split="cross-source",
                    source=source,
                    disease_id=disease,
                    disease_name=kb.disease_name(disease),
                    gene=gene,
                    present=present,
                    absent=sample_negated(kb, disease, present, negation_pool, rng),
                )
            )

        def only_other(case: Case, prefix: str = f"{source}:") -> KnowledgeBase:
            hidden = [d for d in kb.diseases_for_gene(case.gene) if d.startswith(prefix)]
            return AnnotationOverlay(kb, dict.fromkeys(hidden, ()))

        name = f"C_{source.lower()}"
        meta = {"eligible_genes": len(genes), "tuning_genes_left_out": True}
        runs.append(
            Run(
                name,
                f"patient from {source} annotations, agent knows only the gene's {other} diseases",
                cases,
                only_other,
                _simulated(kb),
                meta=meta,
            )
        )
        runs.append(
            Run(
                f"{name}_control",
                f"as {name}, agent with both sources",
                cases,
                lambda c: kb,
                _simulated(kb),
                meta=meta,
            )
        )
        runs.append(
            Run(
                f"{name}_msp",
                f"as {name}, agent with {dict(MSP)}",
                cases,
                only_other,
                _simulated(kb),
                config=replace(AgentConfig(), **MSP),
                policies=("none", "agent"),
                meta=meta,
            )
        )
    return runs


def _pipeline(kb: KnowledgeBase, config: AgentConfig) -> Pipeline:
    if kb is not _STATE["kb"]:
        return build_pipeline(kb, config)
    pipelines: dict[AgentConfig, Pipeline] = _STATE.setdefault("pipelines", {})
    if config not in pipelines:
        pipelines[config] = build_pipeline(kb, config)
    return pipelines[config]


def _model() -> CachedModelClient:
    if "model" not in _STATE:
        _STATE["model"] = real_model(_STATE.get("gate"))
    model: CachedModelClient = _STATE["model"]
    return model


def _init_worker(gate: Any) -> None:
    _STATE["gate"] = gate


def _run_job(job: tuple[str, str, int, int]) -> list[dict[str, Any]]:
    name, policy, start, stop = job
    run = _RUNS[name]
    traces = []
    for case in run.cases[start:stop]:
        view = run.agent_kb(case)
        pipeline = _pipeline(view, run.config)
        if run.reranker is not None:
            pipeline = replace(pipeline, reranker=run.reranker(case, view))
        trace = run_case(
            policy,
            case,
            pipeline,
            _model(),
            DEFAULT,
            mode=run.mode,
            early_stop=True,
            patient=None if policy == "none" else run.patient(case, run.mode),
        )
        traces.append(trace.to_dict())
    return traces


def execute(runs: Sequence[Run], workers: int, out_dir: Path = AUDIT_DIR, chunk: int = 20) -> None:
    for run in runs:
        _RUNS[run.name] = run
    jobs = [
        (run.name, policy, start, start + chunk)
        for run in runs
        for policy in run.policies
        for start in range(0, len(run.cases), chunk)
    ]
    context = multiprocessing.get_context("fork")
    gate = context.BoundedSemaphore(4)
    collected: dict[tuple[str, str], list[dict[str, Any]]] = {
        (r.name, p): [] for r in runs for p in r.policies
    }
    started = time.perf_counter()
    with ProcessPoolExecutor(
        workers, mp_context=context, initializer=_init_worker, initargs=(gate,)
    ) as pool:
        for (name, policy, _, _), traces in zip(jobs, pool.map(_run_job, jobs), strict=True):
            collected[(name, policy)].extend(traces)
    wall = time.perf_counter() - started
    out_dir.mkdir(parents=True, exist_ok=True)
    for run in runs:
        payload = {
            "meta": {
                "run": run.name,
                "description": run.description,
                "cases": len(run.cases),
                "policies": list(run.policies),
                "config": run.config.to_dict(),
                "answerer": run.answerer,
                "mode": run.mode.value,
                "early_stop": True,
                "kb_version": _STATE["kb"].version,
                "model_url": MODEL_URL,
                "wall_seconds_all_runs": round(wall, 1),
                **run.meta,
            },
            "case_list": [c.to_dict() for c in run.cases],
            "case_meta": run.case_meta,
            "traces": {p: collected[(run.name, p)] for p in run.policies},
        }
        (out_dir / f"{run.name}.json").write_text(json.dumps(payload))
    print(f"{len(jobs)} jobs over {len(runs)} runs in {wall:.0f}s")


# --- figures ---------------------------------------------------------------------------------


def final(t: Trajectory, k: int) -> float:
    return float(t.ranks[-1] is not None and t.ranks[-1] <= k)


def policy_figures(ts: Sequence[Trajectory]) -> dict[str, float]:
    n = len(ts)
    return {
        "top1": sum(final(t, 1) for t in ts) / n,
        "top3": sum(final(t, 3) for t in ts) / n,
        "top5": sum(final(t, 5) for t in ts) / n,
        "top10": sum(final(t, 10) for t in ts) / n,
        "top50": sum(final(t, 50) for t in ts) / n,
        "mrr": sum(reciprocal(t.ranks[-1]) for t in ts) / n,
        "questions": sum(t.questions[-1] for t in ts) / n,
    }


def _rank(value: int | None) -> int:
    return MISSING_RANK if value is None else value


def movement(after: Sequence[Trajectory], before: Sequence[Trajectory]) -> dict[str, float]:
    by_case = {t.case_id: t for t in before}
    pairs = [(_rank(t.ranks[-1]), _rank(by_case[t.case_id].ranks[-1])) for t in after]
    n = len(pairs)
    return {
        "worse": sum(1 for a, b in pairs if a > b) / n,
        "better": sum(1 for a, b in pairs if a < b) / n,
        "reach_first": sum(1 for a, b in pairs if a == 1 and b > 1),
        "lose_first": sum(1 for a, b in pairs if a > 1 and b == 1),
    }


def compare(first: Sequence[Trajectory], second: Sequence[Trajectory]) -> dict[str, Any]:
    return {name: paired_bootstrap(first, second, metric) for name, metric in METRICS.items()}


def answer_counts(traces: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for trace in traces:
        for record in trace["rounds"]:
            counts.update(record["answers"])
    return dict(counts)


def run_figures(payload: Mapping[str, Any], subset: set[str] | None = None) -> dict[str, Any]:
    by_policy = trajectories(payload)
    if subset is not None:
        by_policy = {p: [t for t in ts if t.case_id in subset] for p, ts in by_policy.items()}
    figures: dict[str, Any] = {
        "n": len(by_policy["none"]),
        "policies": {p: policy_figures(ts) for p, ts in by_policy.items()},
        "comparisons": {},
        "movement": {},
    }
    for first, second in COMPARISONS:
        if first in by_policy and second in by_policy:
            key = f"{first}_vs_{second}"
            figures["comparisons"][key] = compare(by_policy[first], by_policy[second])
            figures["movement"][key] = movement(by_policy[first], by_policy[second])
    if "agent" in by_policy:
        agent = by_policy["agent"]
        asked = {t.case_id for t in agent if t.questions[-1] > 0}
        figures["asked_cases"] = len(asked)
        if asked:
            figures["asked_agent_vs_none"] = compare(
                [t for t in agent if t.case_id in asked],
                [t for t in by_policy["none"] if t.case_id in asked],
            )
        # The first ranking already applies the reported negated finding (and any reported term
        # the model does not know); everything after it is the answers.
        before_answers = [replace(t, ranks=t.ranks[:1]) for t in agent]
        figures["before_answers_vs_none"] = compare(before_answers, by_policy["none"])
        figures["answers_vs_before_answers"] = compare(agent, before_answers)
        traces = [t for t in payload["traces"]["agent"] if subset is None or t["case_id"] in subset]
        figures["answers"] = answer_counts(traces)
        figures["stop_reasons"] = dict(
            Counter(
                (t["rounds"][-1]["finish_reason"] or "max_rounds") for t in traces
            ).most_common()
        )
    return figures


def _load(name: str) -> dict[str, Any]:
    path = SIMULATED_RUN if name == "simulated" else AUDIT_DIR / f"{name}.json"
    payload: dict[str, Any] = json.loads(path.read_text())
    return payload


def summary() -> dict[str, Any]:
    names = ["simulated", *sorted(p.stem for p in AUDIT_DIR.glob("*.json") if p.stem != "summary")]
    payloads = {name: _load(name) for name in names}
    result: dict[str, Any] = {"runs": {}, "cross_run": {}, "subsets": {}}
    for name, payload in payloads.items():
        result["runs"][name] = {
            "description": payload["meta"].get("description", "simulated TEST split"),
            "meta": {k: v for k, v in payload["meta"].items() if k != "description"},
            **run_figures(payload),
        }
    for first, second in CROSS_RUN:
        if first in payloads and second in payloads:
            a = trajectories(payloads[first])["agent"]
            b = trajectories(payloads[second])["agent"]
            result["cross_run"][f"{first}_minus_{second}"] = compare(a, b)
    if "A" in payloads:
        meta = payloads["A"]["case_meta"]
        subsets = {
            "genes_not_in_tuning": {c for c, m in meta.items() if not m["gene_in_tuning_split"]},
            "article_cited": {c for c, m in meta.items() if m["article_cited_by_gene_annotations"]},
            "article_not_cited": {
                c for c, m in meta.items() if not m["article_cited_by_gene_annotations"]
            },
        }
        for key, ids in subsets.items():
            result["subsets"][f"A.{key}"] = run_figures(payloads["A"], ids)
            if "A_full_kb" in payloads:
                result["subsets"][f"A_full_kb.{key}"] = run_figures(payloads["A_full_kb"], ids)
        result["a_case_counts"] = {
            key: sum(1 for m in meta.values() if m[key])
            for key in (
                "article_cited_by_gene_annotations",
                "article_cited_by_disease_annotations",
                "gene_annotated_full_kb",
                "gene_annotated_without_article",
                "gene_in_tuning_split",
            )
        }
    result["split_hygiene"] = split_hygiene()
    result["simulator_vs_agent"] = simulator_vs_agent()
    return result


def split_hygiene() -> dict[str, Any]:
    tuning, test = load("tuning"), load("test")
    return {
        "tuning_cases": len(tuning),
        "test_cases": len(test),
        "shared_genes": len({c.gene for c in tuning} & {c.gene for c in test}),
        "shared_diseases": len({c.disease_id for c in tuning} & {c.disease_id for c in test}),
        "distinct_genes": len({c.gene for c in tuning + test}),
    }


def simulator_vs_agent() -> dict[str, Any]:
    """The simulated family's answer rates next to the agent's likelihood parameters."""
    a = DEFAULT
    agent = AgentConfig()
    unrelated_yes = a.spurious_yes * (1 - a.flip) + (1 - a.spurious_yes) * a.flip
    return {
        "answerer": {
            "missing_frequency": a.missing_frequency,
            "spurious_yes": a.spurious_yes,
            "flip": a.flip,
            "p_unknown": a.p_unknown,
            "p_unknown_testing": a.p_unknown_testing,
            "p_yes_given_answered_unrelated": unrelated_yes,
            "p_yes_given_answered_excluded": a.flip,
            "p_no_given_answered_present": a.flip,
        },
        "agent": {
            "missing_frequency": agent.missing_frequency,
            "background": agent.background,
            "epsilon": agent.epsilon,
            "p_unknown": agent.p_unknown,
            "temperature": agent.temperature,
        },
        "case_generator_missing_frequency": 0.5,
    }


def print_table(result: Mapping[str, Any]) -> None:
    print(
        "| run | n | none top-1 | agent top-1 | Δ top-1 agent-none | Δ MRR agent-none | "
        "Δ MRR agent-heuristic | questions | sí/no/no sé |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for name, run in result["runs"].items():
        policies, comparisons = run["policies"], run["comparisons"]
        if "agent_vs_none" not in comparisons:
            continue
        top1 = comparisons["agent_vs_none"]["top1"]
        mrr = comparisons["agent_vs_none"]["mrr"]
        heuristic = comparisons.get("agent_vs_most_frequent_unreported", {}).get("mrr")
        answers = run.get("answers", {})
        print(
            f"| {name} | {run['n']} | {100 * policies['none']['top1']:.1f} | "
            f"{100 * policies['agent']['top1']:.1f} | "
            f"{100 * top1['delta']:+.1f} [{100 * top1['low']:+.1f}; {100 * top1['high']:+.1f}] | "
            f"{mrr['delta']:+.3f} [{mrr['low']:+.3f}; {mrr['high']:+.3f}] | "
            + (
                f"{heuristic['delta']:+.3f} [{heuristic['low']:+.3f}; {heuristic['high']:+.3f}]"
                if heuristic
                else "—"
            )
            + f" | {policies['agent']['questions']:.2f} | "
            f"{answers.get('si', 0)}/{answers.get('no', 0)}/{answers.get('no_se', 0)} |"
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--only", default="A,B,C,S", help="experiments to run (A, B, C, S)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.summary_only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}
        kb = open_kb()
        _STATE["kb"] = kb
        model_genes = model_vocabulary()
        pool = annotated_phenotypes(
            kb, (d for ds in eligible_diseases(kb, model_genes).values() for d in ds)
        )
        runs: list[Run] = []
        if only & {"A", "S"}:
            real = real_patient_runs(kb, model_genes)
            runs += [r for r in real if r.name[0] in only]
        if "B" in only:
            runs += hold_out_runs(kb, pool)
        if "C" in only:
            runs += cross_source_runs(kb, model_genes, pool)
        execute(runs, args.workers)
    result = summary()
    (AUDIT_DIR / "summary.json").write_text(json.dumps(result, indent=1))
    print_table(result)


if __name__ == "__main__":
    main()
