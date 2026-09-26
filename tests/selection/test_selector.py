from __future__ import annotations

import pytest

from diagnosticar_preguntas.domain import (
    CandidateTerm,
    Evidence,
    GeneScore,
    Hypothesis,
    ModelRanking,
    Posterior,
)
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import LikelihoodConfig, LikelihoodModel
from diagnosticar_preguntas.ranking.reranker import BayesianReranker, RerankConfig
from diagnosticar_preguntas.selection.selector import InformationGainSelector, SelectionConfig
from fixtures.tiny_kb import (
    BLINDNESS,
    DISEASE_A,
    DISEASE_C2,
    FOCAL_SEIZURE,
    GENE_A,
    GENE_B,
    GENE_C,
    GENE_E,
    HYPOTONIA,
    LACTIC_ACIDOSIS,
    MICROCEPHALY,
    SEIZURE,
)

NO_EVIDENCE = Evidence(present=frozenset())
EQUAL = {GENE_A: 0.25, GENE_B: 0.25, GENE_C: 0.25, GENE_E: 0.25}


def _posterior(
    kb: KnowledgeBase, scores: dict[str, float], likelihood: LikelihoodConfig | None = None
) -> Posterior:
    reranker = BayesianReranker(kb, LikelihoodModel(kb, likelihood), RerankConfig(temperature=1.0))
    return reranker.rerank(ModelRanking(scores=scores), NO_EVIDENCE)


def _selector(
    kb: KnowledgeBase, likelihood: LikelihoodConfig | None = None, **config: float
) -> InformationGainSelector:
    return InformationGainSelector(
        kb,
        LikelihoodModel(kb, likelihood),
        SelectionConfig(**config),  # type: ignore[arg-type]
    )


def _by_id(candidates: list[CandidateTerm]) -> dict[str, CandidateTerm]:
    return {c.hpo_id: c for c in candidates}


def test_term_that_perfectly_splits_two_equal_genes_gains_one_bit_times_answer_rate(
    tiny_kb: KnowledgeBase,
) -> None:
    sharp = LikelihoodConfig(background=1e-6, epsilon=1e-6)
    posterior = _posterior(tiny_kb, {GENE_A: 0.5, GENE_B: 0.5}, sharp)

    shortlist = _selector(tiny_kb, sharp, p_unknown=0.2).shortlist(posterior, NO_EVIDENCE, k=10)

    assert shortlist[0].hpo_id == SEIZURE
    assert shortlist[0].information_gain == pytest.approx(0.8, rel=1e-3)
    assert shortlist[0].p_yes == pytest.approx(0.5, rel=1e-3)


def test_term_shared_by_every_gene_is_not_worth_asking(tiny_kb: KnowledgeBase) -> None:
    posterior = Posterior(
        genes=(GeneScore(GENE_A, 0.5, 0.5, 1), GeneScore(GENE_C, 0.5, 0.5, 2)),
        hypotheses=(Hypothesis(GENE_A, DISEASE_A, 0.5), Hypothesis(GENE_C, DISEASE_C2, 0.5)),
    )

    shortlist = _by_id(_selector(tiny_kb).shortlist(posterior, NO_EVIDENCE, k=10))

    assert HYPOTONIA not in shortlist
    assert SEIZURE in shortlist


def test_a_single_candidate_gene_leaves_nothing_to_ask(tiny_kb: KnowledgeBase) -> None:
    posterior = _posterior(tiny_kb, {GENE_A: 1.0})

    assert _selector(tiny_kb).shortlist(posterior, NO_EVIDENCE, k=5) == []


def test_candidates_come_only_from_the_hypotheses_diseases(tiny_kb: KnowledgeBase) -> None:
    posterior = _posterior(tiny_kb, {GENE_A: 0.5, GENE_C: 0.5})

    shortlist = _by_id(_selector(tiny_kb).shortlist(posterior, NO_EVIDENCE, k=10))

    assert MICROCEPHALY not in shortlist
    assert LACTIC_ACIDOSIS not in shortlist


def test_ancestors_of_annotations_are_candidates_within_the_distance_limit(
    tiny_kb: KnowledgeBase,
) -> None:
    posterior = _posterior(tiny_kb, {GENE_B: 0.5, GENE_C: 0.5})

    near = _by_id(_selector(tiny_kb, max_ancestor_distance=0).shortlist(posterior, NO_EVIDENCE, 10))
    far = _by_id(_selector(tiny_kb, max_ancestor_distance=1).shortlist(posterior, NO_EVIDENCE, 10))

    assert SEIZURE not in near
    assert SEIZURE in far


def test_known_terms_are_never_asked_again(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(
        present=frozenset({MICROCEPHALY}),
        absent=frozenset({BLINDNESS}),
        unknown=frozenset({HYPOTONIA}),
    )

    shortlist = _by_id(_selector(tiny_kb).shortlist(_posterior(tiny_kb, EQUAL), evidence, k=10))

    assert shortlist
    assert not evidence.asked_or_known & shortlist.keys()


def test_ancestors_of_a_present_finding_are_implied_yes(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset({FOCAL_SEIZURE}))

    shortlist = _by_id(_selector(tiny_kb).shortlist(_posterior(tiny_kb, EQUAL), evidence, k=10))

    assert SEIZURE not in shortlist


@pytest.mark.parametrize("field", ["absent", "unknown"])
def test_descendants_of_an_absent_or_unknown_finding_are_not_asked(
    tiny_kb: KnowledgeBase, field: str
) -> None:
    evidence = Evidence(present=frozenset(), **{field: frozenset({SEIZURE})})  # type: ignore[arg-type]

    shortlist = _by_id(_selector(tiny_kb).shortlist(_posterior(tiny_kb, EQUAL), evidence, k=10))

    assert shortlist
    assert FOCAL_SEIZURE not in shortlist


def test_shortlist_never_pairs_a_term_with_its_ancestor(tiny_kb: KnowledgeBase) -> None:
    shortlist = _selector(tiny_kb).shortlist(_posterior(tiny_kb, EQUAL), NO_EVIDENCE, k=10)

    ids = [c.hpo_id for c in shortlist]
    for term in ids:
        assert not tiny_kb.ancestors(term) & set(ids)


def test_shortlist_respects_k(tiny_kb: KnowledgeBase) -> None:
    selector = _selector(tiny_kb)
    posterior = _posterior(tiny_kb, EQUAL)

    assert len(selector.shortlist(posterior, NO_EVIDENCE, k=2)) == 2
    assert selector.shortlist(posterior, NO_EVIDENCE, k=0) == []


def test_candidate_carries_labels_probability_and_the_genes_it_separates(
    tiny_kb: KnowledgeBase,
) -> None:
    shortlist = _by_id(_selector(tiny_kb).shortlist(_posterior(tiny_kb, EQUAL), NO_EVIDENCE, 10))

    seizure = shortlist[SEIZURE]
    assert (seizure.label_es, seizure.label_en) == ("Convulsiones", "Seizure")
    assert 0 < seizure.p_yes < 1
    assert seizure.information_gain > 0
    assert seizure.genes_yes[0] == GENE_A
    assert GENE_B in seizure.genes_no
    assert not set(seizure.genes_yes) & set(seizure.genes_no)
    assert not seizure.requires_testing


def test_findings_that_need_a_study_are_flagged(tiny_kb: KnowledgeBase) -> None:
    posterior = _posterior(tiny_kb, {GENE_B: 0.5, GENE_E: 0.5})

    shortlist = _by_id(_selector(tiny_kb).shortlist(posterior, NO_EVIDENCE, k=10))

    assert shortlist[LACTIC_ACIDOSIS].requires_testing
    assert not shortlist[MICROCEPHALY].requires_testing


def test_empty_posterior_gives_no_questions(tiny_kb: KnowledgeBase) -> None:
    assert _selector(tiny_kb).shortlist(Posterior(genes=()), NO_EVIDENCE, k=3) == []
