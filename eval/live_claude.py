"""Small live evaluation of the Claude provider, under a hard budget of CLI calls.

1. Round 1 of 8 TEST cases in patient mode and 4 in clinician mode, through
   ``RoundService`` with ``PREGUNTAS_LLM_PROVIDER=claude-cli``: guardrail
   rejections and fallbacks, latency, and where Claude's chosen terms sit in
   the information-gain shortlist (the cost of its judgment against pure IG).
2. The golden notes in ``eval/data/notas.jsonl``, batched into NotesTask calls:
   accuracy of the effective answer, and that no answer flips between sí and no.

    python -m eval.live_claude [--max-calls 20]

Every call is counted in ``eval/results/live_budget.json`` before it starts,
across runs, and answers are cached on disk, so a rerun costs nothing.
Writes ``eval/results/live_claude.json`` and ``eval/results/live_questions.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.cases import Case, load
from eval.harness import (
    CACHE_DIR,
    DATA_DIR,
    RESULTS_DIR,
    AgentConfig,
    build_pipeline,
    open_kb,
    real_model,
    round_service,
    settings_for,
)
from eval.policies import RecordingSelector
from eval.run_simulation import load_config
from pydantic import BaseModel

from diagnosticar_preguntas.agent.evidence import interpret_answers
from diagnosticar_preguntas.api.schemas import AnswerIn, RoundRequest
from diagnosticar_preguntas.config import LLMProviderName
from diagnosticar_preguntas.domain import AnswerValue, Mode
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.llm.factory import build_provider
from diagnosticar_preguntas.llm.prompting import notes_request
from diagnosticar_preguntas.llm.provider import LLMError, LLMProvider, LLMRequest
from diagnosticar_preguntas.llm.tasks import NoteItem, NotesTask, QuestionTask
from diagnosticar_preguntas.llm.template import TemplateProvider

BUDGET_PATH = RESULTS_DIR / "live_budget.json"
NOTES_PATH = DATA_DIR / "notas.jsonl"
PATIENT_CASES = 8
CLINICIAN_CASES = 4
NOTE_BATCHES = 3


class BudgetExhaustedError(LLMError):
    pass


@dataclass(slots=True)
class CallRecord:
    task: str
    cached: bool
    seconds: float
    outcome: str
    corrections: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    output: dict[str, Any] | None = None


class BudgetedProvider:
    """Counts, caches and records every call to ``inner``; refuses beyond ``limit`` calls."""

    def __init__(self, inner: LLMProvider, limit: int, cache_dir: Path = CACHE_DIR / "llm") -> None:
        self._inner = inner
        self.limit = limit
        self._cache_dir = cache_dir
        self.records: list[CallRecord] = []

    @property
    def model_name(self) -> str | None:
        return self._inner.model_name

    @staticmethod
    def spent() -> int:
        if not BUDGET_PATH.is_file():
            return 0
        return int(json.loads(BUDGET_PATH.read_text())["calls"])

    def _spend(self) -> None:
        spent = self.spent()
        if spent >= self.limit:
            raise BudgetExhaustedError(f"live budget of {self.limit} calls exhausted")
        BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
        BUDGET_PATH.write_text(json.dumps({"calls": spent + 1}))

    def generate[T: BaseModel](
        self, request: LLMRequest[T], *, timeout_s: float | None = None
    ) -> T:
        key = hashlib.sha256(
            json.dumps([self.model_name, request.system, request.user]).encode()
        ).hexdigest()
        path = self._cache_dir / f"{key}.json"
        task = request.task
        record = CallRecord(
            task=type(task).__name__,
            cached=path.is_file(),
            seconds=0.0,
            outcome="ok",
            corrections=list(task.corrections) if isinstance(task, QuestionTask) else [],
            candidates=[c.hpo_id for c in task.candidates]
            if isinstance(task, QuestionTask)
            else [],
        )
        self.records.append(record)
        if record.cached:
            cached = json.loads(path.read_text())
            record.seconds = cached["seconds"]
            record.output = cached["output"]
            return request.output.model_validate(cached["output"])
        try:
            self._spend()
        except BudgetExhaustedError:
            record.outcome = "budget_exhausted"
            raise
        started = time.perf_counter()
        try:
            result = self._inner.generate(request, timeout_s=timeout_s)
        except LLMError as error:
            record.outcome = type(error).__name__
            record.seconds = time.perf_counter() - started
            raise
        record.seconds = time.perf_counter() - started
        record.output = result.model_dump()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seconds": record.seconds, "output": record.output}))
        return result


def claude_provider(limit: int) -> BudgetedProvider:
    os.environ["PREGUNTAS_LLM_PROVIDER"] = LLMProviderName.CLAUDE_CLI.value
    settings = settings_for(AgentConfig(), early_stop=True, llm_provider=LLMProviderName.CLAUDE_CLI)
    return BudgetedProvider(build_provider(settings), limit)


def pick_cases(kb: KnowledgeBase, config: AgentConfig) -> list[Case]:
    """TEST cases whose first round asks questions under ``config``, alternating sources."""
    pipeline = build_pipeline(kb, config)
    model = real_model()
    by_source: dict[str, list[Case]] = {"OMIM": [], "ORPHA": []}
    needed = (PATIENT_CASES + CLINICIAN_CASES) // 2
    for case in load("test"):
        if len(by_source[case.source]) >= needed:
            continue
        service = round_service(
            pipeline,
            model=model,
            selector=pipeline.selector,
            llm=TemplateProvider(),
            settings=settings_for(config, early_stop=True),
        )
        response = service.run(_round1(case, Mode.PATIENT))
        if response.status == "questions":
            by_source[case.source].append(case)
    return [c for pair in zip(by_source["OMIM"], by_source["ORPHA"], strict=True) for c in pair]


def _round1(case: Case, mode: Mode) -> RoundRequest:
    return RoundRequest(
        mode=mode,
        round=1,
        present_hpo_ids=list(case.present),
        absent_hpo_ids=list(case.absent),
        answers=[],
    )


def live_round(
    kb: KnowledgeBase, config: AgentConfig, case: Case, mode: Mode, provider: BudgetedProvider
) -> dict[str, Any]:
    pipeline = build_pipeline(kb, config)
    selector = RecordingSelector(pipeline.selector)
    service = round_service(
        pipeline,
        model=real_model(),
        selector=selector,
        llm=provider,
        settings=settings_for(config, early_stop=True, llm_provider=LLMProviderName.CLAUDE_CLI),
    )
    first = len(provider.records)
    started = time.perf_counter()
    response = service.run(_round1(case, mode))
    elapsed = time.perf_counter() - started
    calls = provider.records[first:]
    candidates = calls[0].candidates if calls else []
    shortlist = {c.hpo_id: c for c in selector.shortlists[-1]} if selector.shortlists else {}
    chosen = [q.hpo_id for q in response.questions]
    n = len(chosen)
    positions = [candidates.index(h) + 1 if h in candidates else None for h in chosen]
    by_gain = sorted(
        (h for h in candidates if h in shortlist), key=lambda h: -shortlist[h].information_gain
    )
    gain_chosen = sum(shortlist[h].information_gain for h in chosen if h in shortlist)
    gain_template = sum(shortlist[h].information_gain for h in candidates[:n] if h in shortlist)
    return {
        "case_id": case.case_id,
        "gene": case.gene,
        "disease": case.disease_name,
        "mode": mode.value,
        "present": [kb.label_es(h) or h for h in case.present],
        "absent": [kb.label_es(h) or h for h in case.absent],
        "status": response.status,
        "warnings": response.warnings,
        "fell_back": any("sin IA" in w for w in response.warnings),
        "calls": [
            {
                "seconds": round(c.seconds, 1),
                "cached": c.cached,
                "outcome": c.outcome,
                "corrections": c.corrections,
            }
            for c in calls
        ],
        "round_seconds": round(elapsed, 1),
        "candidates": [
            {
                "hpo_id": h,
                "label": shortlist[h].label_es if h in shortlist else h,
                "information_gain": round(shortlist[h].information_gain, 4)
                if h in shortlist
                else None,
                "requires_testing": shortlist[h].requires_testing if h in shortlist else None,
            }
            for h in candidates
        ],
        "chosen_positions": positions,
        "chosen_ig_ranks": [by_gain.index(h) + 1 if h in by_gain else None for h in chosen],
        "gain_ratio": gain_chosen / gain_template if gain_template > 0 else None,
        "questions": [q.model_dump(exclude_none=True) for q in response.questions],
    }


def load_notes() -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in NOTES_PATH.read_text(encoding="utf-8").splitlines() if line
    ]


def live_notes(kb: KnowledgeBase, provider: BudgetedProvider) -> dict[str, Any]:
    notes = load_notes()
    size = -(-len(notes) // NOTE_BATCHES)
    rows: list[dict[str, Any]] = []
    failed_batches = 0
    for start in range(0, len(notes), size):
        batch = notes[start : start + size]
        items = tuple(
            NoteItem(
                n["hpo_id"],
                kb.label_es(n["hpo_id"]) or n["hpo_id"],
                AnswerValue(n["boton"]),
                n["nota"],
            )
            for n in batch
        )
        answers = [AnswerIn(hpo_id=n["hpo_id"], answer=n["boton"], note=n["nota"]) for n in batch]
        try:
            draft = provider.generate(notes_request(NotesTask(Mode.PATIENT, items)))
            readings = {
                r.hpo_id: r for r in draft.readings if r.hpo_id in {i.hpo_id for i in items}
            }
        except LLMError as error:
            failed_batches += 1
            readings = {}
            print(f"notes batch failed: {error}")
        interpreted = interpret_answers(answers, readings)
        for note, answer in zip(batch, interpreted, strict=True):
            reading = readings.get(note["hpo_id"])
            effective = answer.effective_answer.value
            rows.append(
                {
                    "id": note["id"],
                    "label": kb.label_es(note["hpo_id"]),
                    "boton": note["boton"],
                    "nota": note["nota"],
                    "esperado": note["esperado"],
                    "aceptables": note["aceptables"],
                    "consistent": reading.consistent if reading else None,
                    "summary": reading.summary if reading else None,
                    "efectiva": effective,
                    "exacta": effective == note["esperado"],
                    "aceptable": effective in note["aceptables"],
                    "flip": {note["boton"], effective} == {"si", "no"},
                }
            )
    strict = [r for r in rows if len(r["aceptables"]) == 1]
    return {
        "rows": rows,
        "failed_batches": failed_batches,
        "exact": sum(r["exacta"] for r in rows) / len(rows),
        "acceptable": sum(r["aceptable"] for r in rows) / len(rows),
        "strict_items": len(strict),
        "strict_accuracy": sum(r["exacta"] for r in strict) / len(strict) if strict else None,
        "flips": sum(r["flip"] for r in rows),
        "missing_readings": sum(1 for r in rows if r["consistent"] is None),
    }


def render_markdown(rounds: Sequence[dict[str, Any]], notes: dict[str, Any]) -> str:
    lines = [
        "# Preguntas generadas por Claude (ronda 1), para revisión humana",
        "",
        "Cada bloque: caso simulado, lo informado, los candidatos por ganancia de información",
        "(en el orden diversificado del selector) y las preguntas finales. Posición = lugar",
        "del síntoma elegido en esa lista; rango IG = lugar por ganancia de información pura.",
        "",
    ]
    for r in rounds:
        lines += [
            f"## {r['case_id']} · modo {r['mode']} · gen real {r['gene']}",
            "",
            f"- Enfermedad: {r['disease']}",
            f"- Presentes: {', '.join(r['present'])}",
            f"- Ausentes: {', '.join(r['absent']) or '—'}",
            f"- Llamadas: {len(r['calls'])} · "
            + " / ".join(f"{c['seconds']} s ({c['outcome']})" for c in r["calls"])
            + (" · **cayó a plantilla**" if r["fell_back"] else ""),
            "- Candidatos: "
            + "; ".join(
                f"{i}. {c['label']} ({c['information_gain']} bits"
                + (", estudio" if c["requires_testing"] else "")
                + ")"
                for i, c in enumerate(r["candidates"], start=1)
            ),
            "",
        ]
        for q, position, ig_rank in zip(
            r["questions"], r["chosen_positions"], r["chosen_ig_ranks"], strict=True
        ):
            lines.append(
                f"1. **{q['text']}** (posición {position}, rango IG {ig_rank}; `{q['hpo_id']}`)"
            )
            if q.get("help"):
                lines.append(f"   - Ayuda: {q['help']}")
            if q.get("rationale"):
                lines.append(f"   - Fundamento: {q['rationale']}")
        for call in r["calls"]:
            for correction in call["corrections"]:
                lines.append(f"   - Corrección pedida en el reintento: {correction}")
        lines.append("")
    lines += [
        "# Lectura de notas",
        "",
        "| id | síntoma | botón | nota | esperado | efectiva | resumen |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in notes["rows"]:
        mark = "" if row["exacta"] else (" (aceptable)" if row["aceptable"] else " ✗")
        lines.append(
            f"| {row['id']} | {row['label']} | {row['boton']} | {row['nota']} | "
            f"{row['esperado']} | {row['efectiva']}{mark} | {row['summary'] or ''} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--config", type=Path, default=RESULTS_DIR / "chosen_config.json")
    args = parser.parse_args(argv)

    kb = open_kb()
    config = load_config(args.config if args.config.is_file() else None)
    provider = claude_provider(args.max_calls)
    started = time.perf_counter()
    cases = pick_cases(kb, config)
    # Reserve the note batches so retries on questions cannot starve them.
    provider.limit = args.max_calls - NOTE_BATCHES
    rounds = [
        live_round(kb, config, case, Mode.PATIENT, provider) for case in cases[:PATIENT_CASES]
    ]
    rounds += [
        live_round(kb, config, case, Mode.CLINICIAN, provider)
        for case in cases[PATIENT_CASES : PATIENT_CASES + CLINICIAN_CASES]
    ]
    provider.limit = args.max_calls
    notes = live_notes(kb, provider)
    fresh = [r for r in provider.records if not r.cached and r.outcome != "budget_exhausted"]
    question_calls = [r for r in provider.records if r.task == "QuestionTask"]
    positions = [p for r in rounds for p in r["chosen_positions"] if p is not None]
    ig_ranks = [p for r in rounds for p in r["chosen_ig_ranks"] if p is not None]
    ratios = [r["gain_ratio"] for r in rounds if r["gain_ratio"] is not None]
    summary = {
        "model": provider.model_name,
        "config": config.to_dict(),
        "budget_spent_total": BudgetedProvider.spent(),
        "calls_this_run": len(fresh),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "question_rounds": len(rounds),
        "rounds_with_retry": sum(1 for r in rounds if len(r["calls"]) > 1),
        "rounds_fell_back": sum(1 for r in rounds if r["fell_back"]),
        "question_call_seconds": [round(r.seconds, 1) for r in question_calls],
        "question_call_median_s": statistics.median(r.seconds for r in question_calls)
        if question_calls
        else None,
        "notes_call_seconds": [
            round(r.seconds, 1) for r in provider.records if r.task == "NotesTask"
        ],
        "chosen_positions": positions,
        "chosen_ig_ranks": ig_ranks,
        "share_chosen_in_shortlist_top3": sum(1 for p in positions if p <= 3) / len(positions)
        if positions
        else None,
        "share_chosen_in_ig_top3": sum(1 for p in ig_ranks if p <= 3) / len(ig_ranks)
        if positions
        else None,
        "mean_gain_ratio": statistics.fmean(ratios) if ratios else None,
        "min_gain_ratio": min(ratios) if ratios else None,
        "notes": {k: v for k, v in notes.items() if k != "rows"},
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "live_claude.json").write_text(
        json.dumps(
            {"summary": summary, "rounds": rounds, "notes": notes}, ensure_ascii=False, indent=1
        )
    )
    (RESULTS_DIR / "live_questions.md").write_text(render_markdown(rounds, notes), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
