"""Smoke test against the real ``claude`` CLI. Spends subscription quota: run with ``-m live``."""

from __future__ import annotations

import shutil

import pytest
from llm_fakes import CANDIDATES, round_request

from diagnosticar_preguntas.agent.guardrails import PatientVocabulary, QuestionRules, check_round
from diagnosticar_preguntas.domain import Mode
from diagnosticar_preguntas.llm.claude_cli import ClaudeCliProvider

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _only_when_selected(request: pytest.FixtureRequest) -> None:
    if "live" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("spends Claude quota; run with -m live")
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not installed")


def test_claude_cli_drafts_a_valid_patient_round() -> None:
    provider = ClaudeCliProvider(model="claude-opus-5", effort="low", timeout_s=180)

    draft = provider.generate(round_request(Mode.PATIENT))

    rules = QuestionRules(
        mode=Mode.PATIENT,
        shortlist=frozenset(c.hpo_id for c in CANDIDATES),
        already_known=frozenset(),
        expected=2,
        patient_text=PatientVocabulary(
            ("CFTR", "DNAH5", "SCNN1A"), dict[str, str]().get
        ).for_diseases(()),
        labels={c.hpo_id: (c.label_es, c.label_en) for c in CANDIDATES},
    )
    assert check_round(draft, rules).violations == ()
