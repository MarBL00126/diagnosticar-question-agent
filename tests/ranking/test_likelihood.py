from __future__ import annotations

import pytest

from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import (
    UNANNOTATED_DISEASE,
    LikelihoodConfig,
    LikelihoodModel,
)
from fixtures.tiny_kb import (
    BLINDNESS,
    DISEASE_A,
    DISEASE_B,
    DISEASE_C1,
    DISEASE_C2,
    DISEASE_E,
    FOCAL_SEIZURE,
    GENERALIZED_SEIZURE,
    HYPOTONIA,
    LACTIC_ACIDOSIS,
    MICROCEPHALY,
    NERVOUS_PHYSIOLOGY,
    SEIZURE,
    VISUAL_IMPAIRMENT,
)

CONFIG = LikelihoodConfig(background=0.05, missing_frequency=0.5, epsilon=0.02)


@pytest.fixture
def model(tiny_kb: KnowledgeBase) -> LikelihoodModel:
    return LikelihoodModel(tiny_kb, CONFIG)


def test_directly_annotated_term_uses_its_frequency(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_A, HYPOTONIA) == pytest.approx(0.5)
    assert model.probability(DISEASE_B, MICROCEPHALY) == pytest.approx(0.75)


def test_ancestor_of_an_annotation_inherits_its_frequency(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_C1, SEIZURE) == pytest.approx(0.8)


def test_descendant_of_an_annotation_gets_only_the_background(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_C1, GENERALIZED_SEIZURE) == pytest.approx(0.05)


def test_several_matching_annotations_combine_by_noisy_or(model: LikelihoodModel) -> None:
    expected = 1 - (1 - 0.5) * (1 - 0.545)

    assert model.probability(DISEASE_C2, VISUAL_IMPAIRMENT) == pytest.approx(expected)


def test_missing_frequency_uses_the_configured_prior_not_certainty(
    tiny_kb: KnowledgeBase, model: LikelihoodModel
) -> None:
    cautious = LikelihoodModel(tiny_kb, LikelihoodConfig(missing_frequency=0.3))

    assert model.probability(DISEASE_B, LACTIC_ACIDOSIS) == pytest.approx(0.5)
    assert cautious.probability(DISEASE_B, LACTIC_ACIDOSIS) == pytest.approx(0.3)


def test_negated_term_and_its_descendants_are_nearly_impossible(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_B, SEIZURE) == pytest.approx(0.02)
    assert model.probability(DISEASE_B, FOCAL_SEIZURE) == pytest.approx(0.02)


def test_negating_a_term_says_nothing_about_its_ancestors(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_B, NERVOUS_PHYSIOLOGY) == pytest.approx(0.05)


def test_excluded_frequency_is_treated_as_a_negation(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_E, HYPOTONIA) == pytest.approx(0.02)


def test_zero_frequency_annotation_is_neutral(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_E, LACTIC_ACIDOSIS) == pytest.approx(0.05)


def test_rare_annotation_never_falls_below_the_background(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_A, BLINDNESS) == pytest.approx(0.05)


def test_certain_findings_are_clipped_to_model_answer_noise(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_A, SEIZURE) == pytest.approx(0.98)


def test_unrelated_term_gets_the_background_rate(model: LikelihoodModel) -> None:
    assert model.probability(DISEASE_A, MICROCEPHALY) == pytest.approx(0.05)


def test_unannotated_hypothesis_explains_everything_at_background(
    tiny_kb: KnowledgeBase, model: LikelihoodModel
) -> None:
    for term in tiny_kb.term_ids():
        assert model.probability(UNANNOTATED_DISEASE, term) == pytest.approx(0.05)


def test_sparse_deviations_agree_with_pointwise_probabilities(
    tiny_kb: KnowledgeBase, model: LikelihoodModel
) -> None:
    terms = sorted(tiny_kb.term_ids())
    index = {term: i for i, term in enumerate(terms)}
    for disease in (DISEASE_A, DISEASE_B, DISEASE_C1, DISEASE_C2, DISEASE_E):
        profile = model.profile(disease)
        dense = [profile.default] * len(terms)
        for position, value in profile.deviations(index):
            dense[position] = value

        assert dense == [profile.probability(term) for term in terms]


def test_profiles_are_cached_per_disease(model: LikelihoodModel) -> None:
    assert model.profile(DISEASE_A) is model.profile(DISEASE_A)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"epsilon": 0.0},
        {"epsilon": 0.6},
        {"background": 0.01, "epsilon": 0.02},
        {"missing_frequency": 1.5},
    ],
)
def test_inconsistent_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        LikelihoodConfig(**kwargs)


def test_frequency_is_the_noisy_or_before_floor_and_clipping(model: LikelihoodModel) -> None:
    assert model.profile(DISEASE_A).frequency(SEIZURE) == pytest.approx(1.0)
    assert model.profile(DISEASE_A).frequency(BLINDNESS) == pytest.approx(0.025)
    assert model.profile(DISEASE_C2).frequency(VISUAL_IMPAIRMENT) == pytest.approx(
        1 - (1 - 0.5) * (1 - 0.545)
    )
    assert model.profile(DISEASE_C1).frequency(SEIZURE) == pytest.approx(0.8)
    assert model.profile(DISEASE_B).frequency(LACTIC_ACIDOSIS) == pytest.approx(0.5)


def test_frequency_is_none_when_nothing_is_annotated(model: LikelihoodModel) -> None:
    assert model.profile(DISEASE_C1).frequency(GENERALIZED_SEIZURE) is None
    assert model.profile(DISEASE_B).frequency(SEIZURE) is None
    assert model.profile(DISEASE_E).frequency(LACTIC_ACIDOSIS) is None
    assert model.profile(UNANNOTATED_DISEASE).frequency(SEIZURE) is None


def test_excludes_covers_the_negated_term_and_its_descendants(model: LikelihoodModel) -> None:
    assert model.profile(DISEASE_B).excludes(SEIZURE)
    assert model.profile(DISEASE_B).excludes(FOCAL_SEIZURE)
    assert not model.profile(DISEASE_B).excludes(NERVOUS_PHYSIOLOGY)
    assert model.profile(DISEASE_E).excludes(HYPOTONIA)
    assert not model.profile(DISEASE_A).excludes(SEIZURE)
    assert not model.profile(UNANNOTATED_DISEASE).excludes(SEIZURE)


def test_excludes_is_exactly_when_probability_is_epsilon(
    tiny_kb: KnowledgeBase, model: LikelihoodModel
) -> None:
    for disease in (DISEASE_A, DISEASE_B, DISEASE_C1, DISEASE_C2, DISEASE_E):
        profile = model.profile(disease)
        for term in tiny_kb.term_ids():
            expected = profile.probability(term) == pytest.approx(CONFIG.epsilon)
            assert profile.excludes(term) is expected, (disease, term)
