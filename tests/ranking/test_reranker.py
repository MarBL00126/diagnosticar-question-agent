from __future__ import annotations

import pytest

from diagnosticar_preguntas.domain import Evidence, ModelRanking, Posterior
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.likelihood import (
    UNANNOTATED_DISEASE,
    LikelihoodConfig,
    LikelihoodModel,
)
from diagnosticar_preguntas.ranking.reranker import BayesianReranker, RerankConfig, tempered_prior
from fixtures.tiny_kb import (
    BLINDNESS,
    DISEASE_C1,
    DISEASE_C2,
    GENE_A,
    GENE_B,
    GENE_C,
    GENE_D,
    GENE_E,
    HYPOTONIA,
    MICROCEPHALY,
    SEIZURE,
    SEIZURE_ALT_ID,
)

EQUAL = {GENE_A: 0.25, GENE_B: 0.25, GENE_C: 0.25, GENE_E: 0.25}


def _reranker(kb: KnowledgeBase, temperature: float = 1.0, top_n: int = 50) -> BayesianReranker:
    return BayesianReranker(
        kb, LikelihoodModel(kb, LikelihoodConfig()), RerankConfig(top_n, temperature)
    )


def _scores(posterior: Posterior) -> dict[str, float]:
    return {gene.gene_symbol: gene.score for gene in posterior.genes}


def _ranking(scores: dict[str, float], query: tuple[str, ...] | None = None) -> ModelRanking:
    return ModelRanking(scores=scores, query_hpo_ids=query)


def test_tempering_flattens_the_model_distribution() -> None:
    prior = tempered_prior({GENE_A: 0.8, GENE_B: 0.2}, temperature=3.0)

    assert prior[GENE_A] / prior[GENE_B] == pytest.approx(4 ** (1 / 3))
    assert sum(prior.values()) == pytest.approx(1.0)


def test_temperature_one_keeps_the_model_distribution() -> None:
    assert tempered_prior({GENE_A: 0.6, GENE_B: 0.2}, 1.0) == pytest.approx(
        {GENE_A: 0.75, GENE_B: 0.25}
    )


def test_all_zero_scores_give_a_uniform_prior() -> None:
    assert tempered_prior({GENE_A: 0.0, GENE_B: 0.0}, 3.0) == {GENE_A: 0.5, GENE_B: 0.5}


def test_without_unseen_evidence_the_posterior_is_the_tempered_prior(
    tiny_kb: KnowledgeBase,
) -> None:
    scores = {GENE_A: 0.5, GENE_B: 0.3, GENE_C: 0.2}
    evidence = Evidence(present=frozenset({SEIZURE}))

    posterior = _reranker(tiny_kb, temperature=3.0).rerank(_ranking(scores), evidence)

    assert _scores(posterior) == pytest.approx(tempered_prior(scores, 3.0))


def test_a_genes_prior_is_split_evenly_among_its_diseases(tiny_kb: KnowledgeBase) -> None:
    posterior = _reranker(tiny_kb).rerank(_ranking({GENE_C: 1.0}), Evidence(frozenset()))

    assert {h.disease_id: h.weight for h in posterior.hypotheses} == pytest.approx(
        {DISEASE_C1: 0.5, DISEASE_C2: 0.5}
    )


def test_gene_without_annotated_disease_gets_one_unannotated_hypothesis(
    tiny_kb: KnowledgeBase,
) -> None:
    posterior = _reranker(tiny_kb).rerank(_ranking({GENE_D: 1.0}), Evidence(frozenset()))

    assert [(h.gene_symbol, h.disease_id) for h in posterior.hypotheses] == [
        (GENE_D, UNANNOTATED_DISEASE)
    ]


def test_present_terms_the_model_saw_are_not_counted_again(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset({SEIZURE}))

    posterior = _reranker(tiny_kb).rerank(_ranking(EQUAL, query=(SEIZURE,)), evidence)

    assert _scores(posterior) == pytest.approx(EQUAL)


def test_present_terms_unknown_to_the_model_are_applied(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset({SEIZURE}))
    ranking = ModelRanking(scores=EQUAL, unknown_hpo_ids=(SEIZURE,), query_hpo_ids=(SEIZURE,))

    scores = _scores(_reranker(tiny_kb).rerank(ranking, evidence))

    assert scores[GENE_A] > scores[GENE_C] > scores[GENE_E] > scores[GENE_B]


def test_present_terms_not_sent_to_the_model_are_applied(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset({MICROCEPHALY, SEIZURE}))

    scores = _scores(_reranker(tiny_kb).rerank(_ranking(EQUAL, query=(MICROCEPHALY,)), evidence))

    assert max(scores, key=lambda gene: scores[gene]) == GENE_A


def test_absent_finding_lowers_the_genes_that_present_it(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset(), absent=frozenset({SEIZURE}))

    scores = _scores(_reranker(tiny_kb).rerank(_ranking(EQUAL), evidence))

    assert scores[GENE_A] < scores[GENE_C] < scores[GENE_E]
    assert scores[GENE_B] > scores[GENE_E]


def test_absent_term_never_favours_hypotheses_annotated_with_it(tiny_kb: KnowledgeBase) -> None:
    reranker = _reranker(tiny_kb)
    scores = {GENE_A: 0.3, GENE_B: 0.1, GENE_C: 0.4, GENE_D: 0.1, GENE_E: 0.1}
    baseline = Evidence(present=frozenset(), absent=frozenset({HYPOTONIA}))
    before = {
        (h.gene_symbol, h.disease_id): h.weight
        for h in reranker.rerank(_ranking(scores), baseline).hypotheses
    }

    for term in sorted(tiny_kb.term_ids()):
        evidence = Evidence(present=frozenset(), absent=baseline.absent | {term})
        after = {
            (h.gene_symbol, h.disease_id): h.weight
            for h in reranker.rerank(_ranking(scores), evidence).hypotheses
        }
        annotated = {key for key in before if _mentions(tiny_kb, key[1], term)}
        for with_term in annotated:
            for without_term in before.keys() - annotated:
                ratio_before = before[with_term] / before[without_term]
                ratio_after = after[with_term] / after[without_term]
                assert ratio_after <= ratio_before * (1 + 1e-12), (term, with_term, without_term)


def _mentions(kb: KnowledgeBase, disease: str, term: str) -> bool:
    return any(
        not a.negated
        and (a.frequency is None or a.frequency > 0)
        and (a.hpo_id == term or term in kb.ancestors(a.hpo_id))
        for a in kb.annotations(disease)
    )


def test_evidence_given_by_alternative_id_counts_as_the_current_term(
    tiny_kb: KnowledgeBase,
) -> None:
    reranker = _reranker(tiny_kb)
    by_alt_id = Evidence(present=frozenset(), absent=frozenset({SEIZURE_ALT_ID}))
    by_id = Evidence(present=frozenset(), absent=frozenset({SEIZURE}))

    assert _scores(reranker.rerank(_ranking(EQUAL), by_alt_id)) == pytest.approx(
        _scores(reranker.rerank(_ranking(EQUAL), by_id))
    )


def test_genes_are_sorted_by_posterior_and_keep_model_rank(tiny_kb: KnowledgeBase) -> None:
    scores = {GENE_A: 0.5, GENE_B: 0.3, GENE_C: 0.2}
    evidence = Evidence(present=frozenset(), absent=frozenset({SEIZURE, BLINDNESS}))

    posterior = _reranker(tiny_kb).rerank(_ranking(scores), evidence)

    assert [g.score for g in posterior.genes] == sorted(
        (g.score for g in posterior.genes), reverse=True
    )
    assert posterior.genes[0].gene_symbol == GENE_B
    assert {g.gene_symbol: (g.model_score, g.model_rank) for g in posterior.genes} == {
        GENE_A: (0.5, 1),
        GENE_B: (0.3, 2),
        GENE_C: (0.2, 3),
    }
    assert sum(h.weight for h in posterior.hypotheses) == pytest.approx(1.0)


def test_only_the_top_n_model_genes_are_considered(tiny_kb: KnowledgeBase) -> None:
    scores = {GENE_A: 0.4, GENE_B: 0.3, GENE_C: 0.2, GENE_E: 0.1}

    posterior = _reranker(tiny_kb, top_n=2).rerank(_ranking(scores), Evidence(frozenset()))

    assert {g.gene_symbol for g in posterior.genes} == {GENE_A, GENE_B}


def test_empty_ranking_gives_an_empty_posterior(tiny_kb: KnowledgeBase) -> None:
    assert _reranker(tiny_kb).rerank(_ranking({}), Evidence(frozenset())) == Posterior(genes=())


@pytest.mark.parametrize("kwargs", [{"top_n": 0}, {"temperature": 0.0}])
def test_invalid_rerank_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RerankConfig(**kwargs)  # type: ignore[arg-type]
