"""The documented example of ``POST /v1/rounds`` must stay valid against the schemas.

The platform copies it into its frontend mocks, so a drift here would ship as
a mock the real service can no longer produce.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from diagnosticar_preguntas.api.schemas import RoundRequest, RoundResponse
from diagnosticar_preguntas.kb.store import KnowledgeBase
from fixtures.real_kb import real_kb_path

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "contrato" / "rondas-ejemplo.json"

pytestmark = pytest.mark.skipif(not EXAMPLE.is_file(), reason="docs/ is not in the image")


@pytest.fixture(scope="module")
def example() -> dict[str, Any]:
    body: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return body


@pytest.mark.parametrize("key", ["round1_request", "round2_request"])
def test_example_requests_validate(example: dict[str, Any], key: str) -> None:
    RoundRequest.model_validate(example[key])


@pytest.mark.parametrize("key", ["round1_response", "round2_response"])
def test_example_responses_validate(example: dict[str, Any], key: str) -> None:
    RoundResponse.model_validate(example[key])


def test_example_answers_are_the_questions_of_round_one(example: dict[str, Any]) -> None:
    asked = [q["hpo_id"] for q in example["round1_response"]["questions"]]

    assert [a["hpo_id"] for a in example["round2_request"]["answers"]] == asked


@pytest.mark.skipif(not real_kb_path().is_file(), reason="data/kb.sqlite not built")
def test_example_labels_are_the_official_spanish_labels(example: dict[str, Any]) -> None:
    kb = KnowledgeBase.open(real_kb_path())

    for question in example["round1_response"]["questions"]:
        assert question["label"] == kb.label_es(question["hpo_id"])
