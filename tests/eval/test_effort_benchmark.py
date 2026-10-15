"""The effort benchmark's budget, cache, time replay, statistics and decision rule."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from eval.effort_benchmark import (
    MeteredProvider,
    choice_metrics,
    decide,
    production_outcome,
    sign_test,
    write_wrapper,
)
from eval.live_claude import BudgetExhaustedError

from diagnosticar_preguntas.domain import AnswerValue, CandidateTerm, Mode
from diagnosticar_preguntas.llm.prompting import notes_request
from diagnosticar_preguntas.llm.tasks import NoteItem, NotesTask
from diagnosticar_preguntas.llm.template import TemplateProvider


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock")
def test_wrapper_counts_each_call_and_refuses_beyond_the_limit(tmp_path: Path) -> None:
    real = tmp_path / "real.sh"
    real.write_text('#!/usr/bin/env bash\ncat\necho " $*"\n')
    real.chmod(0o755)
    counter = tmp_path / "count"
    wrapper = write_wrapper(tmp_path / "wrapper.sh", real=str(real), counter=counter, limit=2)

    runs = [
        subprocess.run([str(wrapper), "-p", "x"], input="hola", capture_output=True, text=True)
        for _ in range(3)
    ]

    assert [r.returncode for r in runs] == [0, 0, 75]
    assert runs[0].stdout.strip() == "hola -p x"
    assert counter.read_text().strip() == "2"


def _notes_request() -> Any:
    item = NoteItem("HP:0001250", "Convulsiones", AnswerValue.YES, "a veces")
    return notes_request(NotesTask(Mode.PATIENT, (item,)))


def test_metered_provider_refuses_without_budget_and_replays_from_cache(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    provider = MeteredProvider(
        "medium", limit=1, counter=counter, cache_dir=tmp_path / "cache", inner=TemplateProvider()
    )
    counter.write_text("1")
    with pytest.raises(BudgetExhaustedError):
        provider.generate(_notes_request())

    counter.write_text("0")
    fresh = provider.generate(_notes_request())
    counter.write_text("1")
    replayed = provider.generate(_notes_request())

    assert replayed == fresh
    assert [c.outcome for c in provider.calls] == ["budget_exhausted", "ok", "ok"]
    assert [c.cached for c in provider.calls] == [False, False, True]
    high = MeteredProvider(
        "high", limit=1, counter=counter, cache_dir=tmp_path / "cache", inner=TemplateProvider()
    )
    with pytest.raises(BudgetExhaustedError):
        high.generate(_notes_request())


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        ([30.0], "ok"),
        ([61.0], "timeout"),
        ([58.0, 12.0], "ok"),
        ([50.0, 30.0], "timeout"),
        ([59.0, 7.0, 3.0], "sin_tiempo"),
    ],
)
def test_production_outcome_replays_the_round_limits(seconds: list[float], expected: str) -> None:
    assert production_outcome(seconds, cap=60, budget=75, min_call=10) == expected


def test_sign_test() -> None:
    assert sign_test(0, 0) == 1.0
    assert sign_test(5, 5) == 1.0
    assert sign_test(9, 1) == pytest.approx(22 / 1024)


def test_choice_metrics_compare_with_the_best_and_the_template_choice() -> None:
    candidates = [
        CandidateTerm(f"HP:000000{i}", f"t{i}", f"t{i}", information_gain=g, p_yes=0.5)
        for i, g in enumerate((0.5, 0.2, 0.4, 0.1))
    ]

    metrics = choice_metrics(candidates, ["HP:0000000", "HP:0000003"])

    assert metrics["ig_ranks"] == [1, 4]
    assert metrics["positions"] == [1, 4]
    assert metrics["gain_vs_best"] == pytest.approx(0.6 / 0.9)
    assert metrics["gain_vs_template"] == pytest.approx(0.6 / 0.7)


def _summary(judge_questions: tuple[int, int, int] = (3, 4, 5), cut: float = 0.4) -> dict[str, Any]:
    guarded = {"first_try_pass": 12, "fallbacks": 0}
    notes = {"flips": 0, "acceptable_n": 24, "exact_n": 23, "failed_batches": 0}
    high, medium, tie = judge_questions
    return {
        "questions": {"high": guarded, "medium": guarded, "latency_cut": cut},
        "summaries": {"high": guarded, "medium": guarded, "latency_cut": cut},
        "notes": {"high": notes, "medium": notes, "latency_cut": cut},
        "judge": {
            "questions": {
                "high": high,
                "medium": medium,
                "tie": tie,
                "p": sign_test(high, medium),
                "fidelity_items": {"high": 1, "medium": 1},
            },
            "summaries": {
                "high": 2,
                "medium": 2,
                "tie": 1,
                "p": 1.0,
                "fidelity_items": {"high": 0, "medium": 0},
            },
        },
    }


def test_decide_picks_medium_when_it_is_as_good_and_faster() -> None:
    assert decide(_summary())["verdict"] == "medium"
    assert decide(_summary(cut=0.1))["verdict"] == "high"


def test_decide_splits_when_the_judge_clearly_prefers_high_for_one_task() -> None:
    decision = decide(_summary(judge_questions=(10, 0, 2)))

    assert decision["verdict"] == "split"
    assert decision["effort_by_task"] == {
        "questions": "high",
        "notes": "medium",
        "summaries": "medium",
    }
