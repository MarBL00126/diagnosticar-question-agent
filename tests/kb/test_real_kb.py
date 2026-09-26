"""Checks on the real knowledge base; skipped when ``data/kb.sqlite`` has not been built."""

from __future__ import annotations

import pytest

from diagnosticar_preguntas.kb.requires_testing import OBSERVABLE_ROOTS, REQUIRES_TESTING_ROOTS
from diagnosticar_preguntas.kb.sources import HPO_VERSION, SOURCES
from diagnosticar_preguntas.kb.store import KnowledgeBase
from fixtures.real_kb import real_kb_path

REAL_KB = real_kb_path()

pytestmark = pytest.mark.skipif(not REAL_KB.is_file(), reason="data/kb.sqlite not built")


@pytest.fixture(scope="module")
def real_kb() -> KnowledgeBase:
    return KnowledgeBase.open(REAL_KB)


def test_real_kb_is_built_from_the_pinned_release(real_kb: KnowledgeBase) -> None:
    assert real_kb.hpo_version == HPO_VERSION
    for source in SOURCES:
        assert real_kb.meta[f"source.{source.name}.pinned"] == "true"
        assert real_kb.meta[f"source.{source.name}.sha256"] == source.sha256


def test_real_kb_labels_every_curated_root_with_its_hpo_name(real_kb: KnowledgeBase) -> None:
    for hpo_id, label in {**REQUIRES_TESTING_ROOTS, **OBSERVABLE_ROOTS}.items():
        assert real_kb.label_en(hpo_id) == label


@pytest.mark.parametrize(
    ("hpo_id", "requires_testing"),
    [
        ("HP:0001250", False),  # Seizure
        ("HP:0000252", False),  # Microcephaly, also a brain-morphology term
        ("HP:0000256", False),  # Macrocephaly
        ("HP:0002650", False),  # Scoliosis
        ("HP:0001945", False),  # Fever, filed under homeostasis
        ("HP:0001892", False),  # Abnormal bleeding
        ("HP:0002353", True),  # EEG abnormality
        ("HP:0003128", True),  # Lactic acidosis
        ("HP:0001629", True),  # Ventricular septal defect
        ("HP:0001274", True),  # Agenesis of corpus callosum
        ("HP:0001903", True),  # Anemia
        ("HP:0000510", True),  # Rod-cone dystrophy
    ],
)
def test_real_kb_flags_findings_that_need_a_study(
    real_kb: KnowledgeBase, hpo_id: str, requires_testing: bool
) -> None:
    assert real_kb.requires_testing(hpo_id) is requires_testing
