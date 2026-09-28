"""Question drafting may run at a lighter effort than notes and summaries. No network."""

from __future__ import annotations

from dataclasses import replace

from llm_fakes import round_request

from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider
from diagnosticar_preguntas.llm.provider import effort_for
from diagnosticar_preguntas.llm.tasks import SummaryTask


def summary_task() -> SummaryTask:
    return SummaryTask(Mode.PATIENT, "user_finished", (), ())


def test_question_tasks_use_the_question_effort() -> None:
    assert effort_for(round_request().task, "high", "medium") == "medium"


def test_other_tasks_keep_the_default_effort() -> None:
    assert effort_for(summary_task(), "high", "medium") == "high"


def test_without_a_question_effort_every_task_uses_the_default() -> None:
    assert effort_for(round_request().task, "high", None) == "high"


def test_the_cli_sends_each_task_its_own_effort() -> None:
    cli = ClaudeCliProvider(
        model="claude-opus-5",
        effort="high",
        question_effort="medium",
        timeout_s=30,
        binary="/usr/bin/claude",
    )
    question = cli.build_argv(round_request())
    summary = cli.build_argv(replace(round_request(), task=summary_task()))

    assert question[question.index("--effort") + 1] == "medium"
    assert summary[summary.index("--effort") + 1] == "high"
