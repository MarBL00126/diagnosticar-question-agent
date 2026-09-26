from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.fixtures.tiny_kb import (
    ABNORMAL_SPEECH,
    ALL,
    AUTOSOMAL_DOMINANT,
    BLINDNESS,
    CNS_MORPHOLOGY,
    CORPUS_CALLOSUM_AGENESIS,
    DISEASE_A,
    DISEASE_B,
    DISEASE_C1,
    DISEASE_C2,
    DISEASE_D,
    DISEASE_E,
    FOCAL_SEIZURE,
    GENE_A,
    GENE_C,
    GENE_D,
    GENE_E,
    GENERALIZED_SEIZURE,
    HEAD_OR_NECK,
    HYPOTONIA,
    LACTIC_ACIDOSIS,
    MICROCEPHALY,
    NERVOUS_PHYSIOLOGY,
    NERVOUS_SYSTEM,
    OBSOLETE_BLINDNESS,
    PHENOTYPIC_ABNORMALITY,
    SEIZURE,
    SEIZURE_ALT_ID,
    SKULL_SIZE,
    UNKNOWN_TERM,
    build_tiny_kb,
)

from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase, KnowledgeBaseError


def _annotation(kb: KnowledgeBase, disease: str, hpo_id: str) -> Annotation:
    matches = [a for a in kb.annotations(disease) if a.hpo_id == hpo_id]
    assert len(matches) == 1, matches
    return matches[0]


def test_closure_holds_every_ancestor_but_not_the_term_itself(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.ancestors(FOCAL_SEIZURE) == {
        SEIZURE,
        NERVOUS_PHYSIOLOGY,
        NERVOUS_SYSTEM,
        PHENOTYPIC_ABNORMALITY,
        ALL,
    }
    assert tiny_kb.parents(FOCAL_SEIZURE) == {SEIZURE}


def test_closure_follows_every_parent_of_a_term(tiny_kb: KnowledgeBase) -> None:
    assert {SKULL_SIZE, HEAD_OR_NECK, CNS_MORPHOLOGY, NERVOUS_SYSTEM} <= tiny_kb.ancestors(
        MICROCEPHALY
    )


def test_descendants_are_the_inverse_of_ancestors(tiny_kb: KnowledgeBase) -> None:
    for term in tiny_kb.term_ids():
        for ancestor in tiny_kb.ancestors(term):
            assert term in tiny_kb.descendants(ancestor)
    assert tiny_kb.descendants(SEIZURE) == {FOCAL_SEIZURE, GENERALIZED_SEIZURE}


def test_depth_is_the_shortest_distance_from_the_root(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.depth(ALL) == 0
    assert tiny_kb.depth(PHENOTYPIC_ABNORMALITY) == 1
    assert tiny_kb.depth(SEIZURE) == 4
    assert tiny_kb.depth(MICROCEPHALY) == 4


def test_alternative_and_obsolete_ids_resolve_to_the_current_term(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.resolve(SEIZURE) == SEIZURE
    assert tiny_kb.resolve(SEIZURE_ALT_ID) == SEIZURE
    assert tiny_kb.resolve(OBSOLETE_BLINDNESS) == BLINDNESS
    assert tiny_kb.resolve(UNKNOWN_TERM) is None
    assert tiny_kb.resolve_all([SEIZURE_ALT_ID, UNKNOWN_TERM, HYPOTONIA]) == {SEIZURE, HYPOTONIA}


def test_annotation_on_an_obsolete_term_is_filed_under_its_replacement(
    tiny_kb: KnowledgeBase,
) -> None:
    assert _annotation(tiny_kb, DISEASE_E, BLINDNESS).frequency == pytest.approx(0.895)


def test_annotation_on_an_unknown_term_is_dropped_and_counted(tiny_kb: KnowledgeBase) -> None:
    assert all(a.hpo_id != UNKNOWN_TERM for a in tiny_kb.annotations(DISEASE_E))
    assert tiny_kb.meta["dropped_annotations"] == "1"


def test_not_qualifier_becomes_a_negated_annotation(tiny_kb: KnowledgeBase) -> None:
    assert _annotation(tiny_kb, DISEASE_B, SEIZURE).negated


def test_excluded_frequency_becomes_a_negated_annotation(tiny_kb: KnowledgeBase) -> None:
    annotation = _annotation(tiny_kb, DISEASE_E, HYPOTONIA)

    assert annotation.negated
    assert annotation.frequency is None


def test_missing_frequency_is_stored_as_null_not_as_certain(tiny_kb: KnowledgeBase) -> None:
    assert _annotation(tiny_kb, DISEASE_B, LACTIC_ACIDOSIS).frequency is None


def test_zero_fraction_is_kept_as_a_zero_frequency(tiny_kb: KnowledgeBase) -> None:
    annotation = _annotation(tiny_kb, DISEASE_E, LACTIC_ACIDOSIS)

    assert annotation.frequency == 0.0
    assert not annotation.negated


def test_repeated_rows_are_merged_into_their_mean_frequency(tiny_kb: KnowledgeBase) -> None:
    assert _annotation(tiny_kb, DISEASE_C2, HYPOTONIA).frequency == pytest.approx(0.5)


def test_onset_is_kept_when_stated(tiny_kb: KnowledgeBase) -> None:
    assert _annotation(tiny_kb, DISEASE_A, SEIZURE).onset == "HP:0003593"
    assert _annotation(tiny_kb, DISEASE_A, HYPOTONIA).onset is None


def test_non_phenotype_aspects_are_ignored(tiny_kb: KnowledgeBase) -> None:
    assert all(a.hpo_id != AUTOSOMAL_DOMINANT for a in tiny_kb.annotations(DISEASE_A))


def test_gene_links_merge_both_gene_tables_and_skip_loci_without_symbol(
    tiny_kb: KnowledgeBase,
) -> None:
    assert tiny_kb.diseases_for_gene(GENE_C) == (DISEASE_C1, DISEASE_C2)
    assert tiny_kb.diseases_for_gene(GENE_E) == (DISEASE_E,)
    assert tiny_kb.genes_for_disease(DISEASE_D) == (GENE_D,)
    assert "-" not in set(tiny_kb.genes())


def test_diseases_without_phenotypes_are_not_annotated_diseases(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.diseases_for_gene(GENE_D) == (DISEASE_D,)
    assert tiny_kb.annotated_diseases_for_gene(GENE_D) == ()
    assert tiny_kb.annotated_diseases_for_gene(GENE_A) == (DISEASE_A,)
    assert tiny_kb.disease_name(DISEASE_A) == "Seizure disorder A"


def test_labels_come_in_both_languages_and_missing_spanish_is_none(
    tiny_kb: KnowledgeBase,
) -> None:
    assert tiny_kb.label_es(SEIZURE) == "Convulsiones"
    assert tiny_kb.label_en(SEIZURE) == "Seizure"
    assert tiny_kb.label_es(GENERALIZED_SEIZURE) is None
    assert tiny_kb.label_es(UNKNOWN_TERM) is None


def test_requires_testing_marks_study_subtrees(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.requires_testing(LACTIC_ACIDOSIS)
    assert tiny_kb.requires_testing(CORPUS_CALLOSUM_AGENESIS)
    assert not tiny_kb.requires_testing(SEIZURE)
    assert not tiny_kb.requires_testing(ABNORMAL_SPEECH)


def test_observable_parent_overrides_a_study_parent(tiny_kb: KnowledgeBase) -> None:
    assert CNS_MORPHOLOGY in tiny_kb.ancestors(MICROCEPHALY)
    assert not tiny_kb.requires_testing(MICROCEPHALY)


def test_only_terms_below_phenotypic_abnormality_are_phenotypes(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.is_phenotypic_abnormality(SEIZURE)
    assert not tiny_kb.is_phenotypic_abnormality(PHENOTYPIC_ABNORMALITY)
    assert not tiny_kb.is_phenotypic_abnormality(AUTOSOMAL_DOMINANT)


def test_version_names_schema_and_hpo_release(tiny_kb: KnowledgeBase) -> None:
    assert tiny_kb.version == "kb1+hpo.2026-02-16"


def test_opening_a_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeBaseError, match="not found"):
        KnowledgeBase.open(tmp_path / "absent.sqlite")


def test_a_database_from_another_schema_version_is_refused(tmp_path: Path) -> None:
    built = build_tiny_kb(tmp_path / "build").path
    stale = tmp_path / "stale.sqlite"
    shutil.copy(built, stale)
    with sqlite3.connect(stale) as connection:
        connection.execute("UPDATE meta SET value = '0' WHERE key = 'kb_schema_version'")

    with pytest.raises(KnowledgeBaseError, match="rebuild"):
        KnowledgeBase.open(stale)


def test_the_database_is_opened_read_only(tmp_path: Path) -> None:
    path = build_tiny_kb(tmp_path).path
    before = path.read_bytes()

    KnowledgeBase.open(path)

    assert path.read_bytes() == before
    assert not path.with_name("kb.sqlite-journal").exists()
