"""Knowledge-base views, real-patient cases and answerers of the de-circularised evaluations."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from eval.answerer import DEFAULT
from eval.cases import Case, positive_annotations
from eval.harness import AgentConfig, build_pipeline
from eval.kb_views import AnnotationOverlay, HpoaReferences, hold_out
from eval.phenopackets import (
    ASSET,
    RELEASE,
    ChecksumError,
    Phenopacket,
    RecordedPatient,
    locate_or_download,
    parse,
    select,
    to_case,
)
from eval.policies import run_case
from eval.variants import WeightedReranker, Weighting
from tests.fixtures.tiny_kb import (
    ABNORMAL_SPEECH,
    BLINDNESS,
    CNS_MORPHOLOGY,
    CORPUS_CALLOSUM_AGENESIS,
    DISEASE_A,
    DISEASE_B,
    DISEASE_C1,
    DISEASE_C2,
    FOCAL_SEIZURE,
    GENE_A,
    GENE_B,
    GENE_C,
    HYPOTONIA,
    LACTIC_ACIDOSIS,
    MICROCEPHALY,
    NERVOUS_PHYSIOLOGY,
    NERVOUS_SYSTEM,
    SEIZURE,
    VISUAL_IMPAIRMENT,
    build_tiny_kb,
)

from diagnosticar_preguntas.domain import AnswerValue, Evidence, ModelRanking
from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase
from diagnosticar_preguntas.ranking.model_client import NoKnownHpoIdsError


def test_an_overlay_replaces_or_removes_annotations_and_leaves_the_base_alone(
    tiny_kb: KnowledgeBase,
) -> None:
    only_blindness = (Annotation(BLINDNESS, False, 0.5, None),)
    view = AnnotationOverlay(tiny_kb, {DISEASE_A: only_blindness, DISEASE_C2: ()})

    assert view.annotations(DISEASE_A) == only_blindness
    assert view.annotations(DISEASE_B) == tiny_kb.annotations(DISEASE_B)
    assert view.annotated_diseases_for_gene(GENE_C) == (DISEASE_C1,)
    assert tiny_kb.annotated_diseases_for_gene(GENE_C) == (DISEASE_C1, DISEASE_C2)
    assert len(tiny_kb.annotations(DISEASE_A)) > 1
    assert view.ancestors(FOCAL_SEIZURE) == tiny_kb.ancestors(FOCAL_SEIZURE)


def test_hold_out_splits_the_positive_annotations_in_two(tiny_kb: KnowledgeBase) -> None:
    held = hold_out(tiny_kb, DISEASE_A, seed=1)
    positives = set(positive_annotations(tiny_kb, DISEASE_A))

    assert set(held.hidden) | (set(held.known) & positives) == positives
    assert set(held.hidden).isdisjoint(held.known)
    assert len(held.hidden) == len(positives) - len(positives) // 2
    assert hold_out(tiny_kb, DISEASE_A, seed=1) == held


def test_hold_out_keeps_the_negated_rows_known(tiny_kb: KnowledgeBase) -> None:
    held = hold_out(tiny_kb, DISEASE_B, seed=1)

    assert all(not a.negated for a in held.hidden)
    assert any(a.negated for a in held.known)


def _hpoa(path: Path, rows: Sequence[tuple[str, str, str, str, str]]) -> Path:
    header = (
        "database_id\tdisease_name\tqualifier\thpo_id\treference\tevidence\tonset\tfrequency"
        "\tsex\tmodifier\taspect\tbiocuration"
    )
    lines = [
        f"{disease}\tname\t{qualifier}\t{term}\t{reference}\tPCS\t\t{frequency}\t\t\tP\tx"
        for disease, qualifier, term, reference, frequency in rows
    ]
    path.write_text("#version: 2026-02-16\n" + "\n".join([header, *lines]) + "\n")
    return path


def test_the_reference_rebuild_matches_the_knowledge_base_build(tmp_path: Path) -> None:
    summary = build_tiny_kb(tmp_path)
    kb = KnowledgeBase.open(summary.path)
    references = HpoaReferences.read(kb, tmp_path / "sources" / "phenotype.hpoa")

    for disease in (DISEASE_A, DISEASE_B, DISEASE_C1, DISEASE_C2, "OMIM:100005"):
        assert set(references.annotations_without(disease, ())) == set(kb.annotations(disease))
        assert references.annotations_without(disease, {"PMID:1"}) == ()


def test_leaving_out_a_reference_drops_its_rows_before_merging(
    tiny_kb: KnowledgeBase, tmp_path: Path
) -> None:
    path = _hpoa(
        tmp_path / "phenotype.hpoa",
        [
            (DISEASE_C2, "", HYPOTONIA, "PMID:1", "1/4"),
            (DISEASE_C2, "", HYPOTONIA, "PMID:2;OMIM:1", "3/4"),
            (DISEASE_C2, "NOT", SEIZURE, "PMID:2", ""),
            (DISEASE_C2, "", BLINDNESS, "PMID:2", ""),
        ],
    )
    references = HpoaReferences.read(tiny_kb, path)

    merged = {
        (a.hpo_id, a.negated): a.frequency for a in references.annotations_without(DISEASE_C2, ())
    }
    kept = references.annotations_without(DISEASE_C2, {"PMID:2"})

    assert merged == {(HYPOTONIA, False): 0.5, (SEIZURE, True): None, (BLINDNESS, False): None}
    assert kept == (Annotation(HYPOTONIA, False, 0.25, None),)
    assert references.references(DISEASE_C2) == {"PMID:1", "PMID:2", "OMIM:1"}


def test_a_record_answers_from_what_the_article_says(tiny_kb: KnowledgeBase) -> None:
    patient = RecordedPatient(tiny_kb, observed=[FOCAL_SEIZURE], excluded=[VISUAL_IMPAIRMENT])

    assert patient.answer(FOCAL_SEIZURE) is AnswerValue.YES
    assert patient.answer(SEIZURE) is AnswerValue.YES
    assert patient.answer(NERVOUS_PHYSIOLOGY) is AnswerValue.YES
    assert patient.answer(VISUAL_IMPAIRMENT) is AnswerValue.NO
    assert patient.answer(BLINDNESS) is AnswerValue.NO
    assert patient.answer(HYPOTONIA) is AnswerValue.UNKNOWN


def test_the_sensitivity_bound_reads_silence_as_no(tiny_kb: KnowledgeBase) -> None:
    patient = RecordedPatient(tiny_kb, [FOCAL_SEIZURE], [], unrecorded=AnswerValue.NO)

    assert patient.answer(HYPOTONIA) is AnswerValue.NO
    assert patient.answer(SEIZURE) is AnswerValue.YES


def _packet(genes: Sequence[str], observed: Sequence[str], excluded: Sequence[str] = ()) -> dict:
    return {
        "id": "PMID_1_P1",
        "phenotypicFeatures": [{"type": {"id": t}} for t in observed]
        + [{"type": {"id": t}, "excluded": True} for t in excluded],
        "interpretations": [
            {
                "diagnosis": {
                    "disease": {"id": DISEASE_A},
                    "genomicInterpretations": [
                        {
                            "interpretationStatus": "CAUSATIVE",
                            "variantInterpretation": {
                                "variationDescriptor": {"geneContext": {"symbol": gene}}
                            },
                        }
                        for gene in genes
                    ]
                    + [
                        {
                            "interpretationStatus": "CANDIDATE",
                            "variantInterpretation": {
                                "variationDescriptor": {"geneContext": {"symbol": "OTHER"}}
                            },
                        }
                    ],
                }
            }
        ],
        "metaData": {"externalReferences": [{"id": "PMID:1"}, {"id": "DOI:x"}]},
    }


SIX_UNRELATED = (
    HYPOTONIA,
    ABNORMAL_SPEECH,
    MICROCEPHALY,
    CORPUS_CALLOSUM_AGENESIS,
    LACTIC_ACIDOSIS,
    BLINDNESS,
)


def test_a_phenopacket_keeps_its_causative_genes_features_and_articles() -> None:
    packet = parse(_packet([GENE_A, GENE_A], [SEIZURE, HYPOTONIA], [BLINDNESS]))

    assert packet.genes == {GENE_A}
    assert packet.disease_id == DISEASE_A
    assert packet.observed == (SEIZURE, HYPOTONIA)
    assert packet.excluded == (BLINDNESS,)
    assert packet.pmids == {"PMID:1"}


def test_the_reported_findings_come_from_the_record(tiny_kb: KnowledgeBase) -> None:
    packet = parse(_packet([GENE_A], [*SIX_UNRELATED, SEIZURE], [FOCAL_SEIZURE, VISUAL_IMPAIRMENT]))

    real = to_case(tiny_kb, packet, GENE_A)

    assert 3 <= len(real.case.present) <= 6
    assert set(real.case.present) <= set(real.observed)
    assert all(a not in tiny_kb.ancestors(b) for a in real.case.present for b in real.case.present)
    assert set(real.case.absent) <= set(real.excluded)
    assert len(real.case.absent) <= 1
    assert to_case(tiny_kb, packet, GENE_A) == real


class _Model:
    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        return ModelRanking(
            scores={GENE_A: 0.5, GENE_B: 0.3, GENE_C: 0.2}, query_hpo_ids=tuple(hpo_ids)
        )


class _Unknowing:
    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        raise NoKnownHpoIdsError(hpo_ids)


def test_selection_counts_why_each_phenopacket_was_left_out(tiny_kb: KnowledgeBase) -> None:
    nested = (NERVOUS_SYSTEM, NERVOUS_PHYSIOLOGY, SEIZURE, FOCAL_SEIZURE, CNS_MORPHOLOGY)
    packets: list[Phenopacket] = [
        parse(_packet([], SIX_UNRELATED)),
        parse(_packet([GENE_A, GENE_B], SIX_UNRELATED)),
        parse(_packet(["NOT_IN_MODEL"], SIX_UNRELATED)),
        parse(_packet([GENE_A], SIX_UNRELATED[:5])),
        parse(_packet([GENE_A], (*nested, MICROCEPHALY))),
        parse(_packet([GENE_B], SIX_UNRELATED)),
    ]

    selection = select(tiny_kb, packets, frozenset({GENE_A, GENE_B}), _Model())

    assert selection.total == 6
    assert dict(selection.excluded) == {
        "no_causative_gene": 1,
        "several_causative_genes": 1,
        "gene_not_in_model_vocabulary": 1,
        "fewer_observed_features": 1,
        "fewer_unrelated_features": 1,
    }
    assert list(selection.by_gene) == [GENE_B]


def test_a_case_the_model_cannot_read_is_left_out(tiny_kb: KnowledgeBase) -> None:
    packet = parse(_packet([GENE_A], SIX_UNRELATED))

    selection = select(tiny_kb, [packet], frozenset({GENE_A}), _Unknowing())

    assert dict(selection.excluded) == {"model_knows_no_reported_term": 1}


def test_a_release_with_another_hash_is_refused(tmp_path: Path) -> None:
    fake = tmp_path / RELEASE / ASSET
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"not the release")

    with pytest.raises(ChecksumError):
        locate_or_download(tmp_path)


class _Refuses:
    """A patient that says "no" to everything and remembers what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def answer(self, hpo_id: str) -> AnswerValue:
        self.asked.append(hpo_id)
        return AnswerValue.NO


class _RecordingModel(_Model):
    def __init__(self) -> None:
        self.queries: list[tuple[str, ...]] = []

    def rank(self, hpo_ids: Sequence[str], top_k: int) -> ModelRanking:
        self.queries.append(tuple(hpo_ids))
        return super().rank(hpo_ids, top_k)


def test_the_agent_hears_only_the_reported_findings_and_the_patients_answers(
    tiny_kb: KnowledgeBase,
) -> None:
    case = Case("t-000", "test", "OMIM", DISEASE_C1, None, GENE_C, (HYPOTONIA,), ())
    pipeline = build_pipeline(tiny_kb, AgentConfig())
    patient = _Refuses()
    model = _RecordingModel()

    trace = run_case("agent", case, pipeline, model, DEFAULT, patient=patient)

    answered = [a for r in trace.rounds for a in r.answers]
    assert patient.asked and answered == ["no"] * len(patient.asked)
    assert [q for r in trace.rounds for q in r.asked] == patient.asked
    assert set(model.queries) == {(HYPOTONIA,)}


def _ratio(
    kb: KnowledgeBase, weighting: Weighting, evidence: Evidence, reported: set[str]
) -> float:
    """Score of GENE_A over GENE_B when the model gives them the same probability."""
    reranker = WeightedReranker(kb, weighting, frozenset(reported))
    ranking = ModelRanking(scores={GENE_A: 0.5, GENE_B: 0.5}, query_hpo_ids=tuple(reported))
    scores = {g.gene_symbol: g.score for g in reranker.rerank(ranking, evidence).genes}
    return scores[GENE_A] / scores[GENE_B]


def test_enrich_only_leaves_the_models_order(tiny_kb: KnowledgeBase) -> None:
    evidence = Evidence(present=frozenset({HYPOTONIA, SEIZURE}), absent=frozenset({MICROCEPHALY}))

    assert _ratio(tiny_kb, Weighting(), evidence, {HYPOTONIA}) == pytest.approx(1.0)


def test_explicit_exclusions_punish_only_a_present_finding(tiny_kb: KnowledgeBase) -> None:
    exclusions = Weighting(exclusion=0.02)
    literal = Weighting(exclusion=0.02, exclusion_on_no=True)
    seizure_yes = Evidence(present=frozenset({HYPOTONIA, SEIZURE}))
    seizure_no = Evidence(present=frozenset({HYPOTONIA}), absent=frozenset({SEIZURE}))

    assert _ratio(tiny_kb, exclusions, seizure_yes, {HYPOTONIA}) == pytest.approx(50.0)
    assert _ratio(tiny_kb, exclusions, seizure_no, {HYPOTONIA}) == pytest.approx(1.0)
    assert _ratio(tiny_kb, literal, seizure_no, {HYPOTONIA}) == pytest.approx(50.0)


def test_the_asymmetric_likelihood_never_punishes_silence(tiny_kb: KnowledgeBase) -> None:
    soft = Weighting(reward=0.5, penalty=0.5, exclusion=0.02)
    hypotonia_yes = Evidence(present=frozenset({MICROCEPHALY, HYPOTONIA}))
    seizure_no = Evidence(present=frozenset({MICROCEPHALY}), absent=frozenset({SEIZURE}))

    # Disease A has hypotonia in 1/2 and seizures always; B mentions neither and excludes seizures.
    assert _ratio(tiny_kb, soft, hypotonia_yes, {MICROCEPHALY}) == pytest.approx(1.25)
    assert _ratio(tiny_kb, soft, seizure_no, {MICROCEPHALY}) == pytest.approx(0.5)
    assert _ratio(tiny_kb, soft, hypotonia_yes, {MICROCEPHALY, HYPOTONIA}) == pytest.approx(1.0)
