from __future__ import annotations

from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.selection.askability import AskabilityRules, askable_terms
from fixtures.tiny_kb import (
    ABNORMAL_SPEECH,
    AUTOSOMAL_DOMINANT,
    BLINDNESS,
    FOCAL_SEIZURE,
    GENERALIZED_SEIZURE,
    HYPOTONIA,
    LACTIC_ACIDOSIS,
    NERVOUS_PHYSIOLOGY,
    NERVOUS_SYSTEM,
    OBSOLETE_BLINDNESS,
    PHENOTYPIC_ABNORMALITY,
    SEIZURE,
    VISUAL_IMPAIRMENT,
)


def test_specific_symptoms_are_askable(tiny_kb: KnowledgeBase) -> None:
    askable = askable_terms(tiny_kb, AskabilityRules())

    assert {SEIZURE, FOCAL_SEIZURE, HYPOTONIA, BLINDNESS, VISUAL_IMPAIRMENT} <= askable
    assert LACTIC_ACIDOSIS in askable


def test_only_phenotypic_abnormalities_are_askable(tiny_kb: KnowledgeBase) -> None:
    askable = askable_terms(tiny_kb, AskabilityRules(min_depth=0))

    assert AUTOSOMAL_DOMINANT not in askable
    assert PHENOTYPIC_ABNORMALITY not in askable


def test_organ_system_terms_are_too_generic_to_ask(tiny_kb: KnowledgeBase) -> None:
    assert NERVOUS_SYSTEM not in askable_terms(tiny_kb, AskabilityRules())


def test_grouping_terms_are_not_askable_but_leaves_with_similar_names_are(
    tiny_kb: KnowledgeBase,
) -> None:
    askable = askable_terms(tiny_kb, AskabilityRules())

    assert NERVOUS_PHYSIOLOGY not in askable
    assert ABNORMAL_SPEECH in askable


def test_terms_covering_too_many_findings_are_not_askable(tiny_kb: KnowledgeBase) -> None:
    askable = askable_terms(tiny_kb, AskabilityRules(max_descendants=1))

    assert SEIZURE not in askable
    assert FOCAL_SEIZURE in askable


def test_terms_without_spanish_label_are_skipped_unless_allowed(tiny_kb: KnowledgeBase) -> None:
    assert GENERALIZED_SEIZURE not in askable_terms(tiny_kb, AskabilityRules())
    assert GENERALIZED_SEIZURE in askable_terms(
        tiny_kb, AskabilityRules(require_spanish_label=False)
    )


def test_obsolete_terms_are_never_askable(tiny_kb: KnowledgeBase) -> None:
    rules = AskabilityRules(min_depth=0, require_spanish_label=False)

    assert OBSOLETE_BLINDNESS not in askable_terms(tiny_kb, rules)
