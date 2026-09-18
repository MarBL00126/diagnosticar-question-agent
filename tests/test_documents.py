from genphenai_repreguntas.documents import corpus_signature, record_to_document, records_to_documents
from genphenai_repreguntas.orphadata import DiseaseRecord, HpoTerm


def make_record(orpha_code: str = "58") -> DiseaseRecord:
    return DiseaseRecord(
        disease="Alexander disease",
        orpha_code=orpha_code,
        hpo_terms=(
            HpoTerm(
                hpo_id="HP:0000256",
                term="Macrocephaly",
                frequency="Frequent (79-30%)",
                diagnostic_criterion=True,
                rank=3,
            ),
        ),
        total_hpo_terms=1,
        n_diag_criteria=1,
    )


def test_record_to_document_keeps_traceable_metadata() -> None:
    document = record_to_document(make_record(), corpus_scope="test", min_hpo_terms=1)

    assert "Alexander disease" in document.page_content
    assert "HP:0000256" in document.page_content
    assert document.metadata["orpha_code"] == "ORPHA:58"
    assert document.metadata["source_type"] == "orpha_hpo_curated"


def test_corpus_signature_changes_when_corpus_changes() -> None:
    first = records_to_documents([make_record("58")], corpus_scope="test", min_hpo_terms=1)
    second = records_to_documents([make_record("61")], corpus_scope="test", min_hpo_terms=1)

    assert corpus_signature(first) == corpus_signature(first)
    assert corpus_signature(first) != corpus_signature(second)
