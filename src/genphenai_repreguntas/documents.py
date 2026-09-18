from __future__ import annotations

from hashlib import md5
from typing import Iterable

from langchain_core.documents import Document

from genphenai_repreguntas.orphadata import DiseaseRecord


def record_to_document(record: DiseaseRecord, *, corpus_scope: str, min_hpo_terms: int) -> Document:
    terms_block = "\n".join(
        f"- {term.term} ({term.hpo_id}, {term.frequency})"
        + (" [CRITERIO DIAGNOSTICO]" if term.diagnostic_criterion else "")
        for term in record.hpo_terms
    )
    question_hints = "\n".join(
        f"- Preguntar por {term.term} para confirmar o descartar {record.disease}."
        for term in record.hpo_terms[:8]
    )
    content = f"""Enfermedad candidata: {record.disease}
ORPHA code: ORPHA:{record.orpha_code}
Fuente: Orphadata en_product4.xml
Criterios diagnosticos HPO: {record.n_diag_criteria}

Fenotipos HPO relevantes:
{terms_block}

Uso para mecanismo de repreguntas:
{question_hints}

Regla de trazabilidad:
Cada repregunta debe estar asociada a un fenotipo HPO documentado y explicar que enfermedad ayuda a confirmar o descartar.
"""
    return Document(
        page_content=content.strip(),
        metadata={
            "source_file": "en_product4.xml",
            "source_type": "orpha_hpo_curated",
            "disease": record.disease,
            "orpha_code": f"ORPHA:{record.orpha_code}",
            "total_hpo_terms": record.total_hpo_terms,
            "indexed_hpo_terms": len(record.hpo_terms),
            "min_hpo_filter": min_hpo_terms,
            "corpus_scope": corpus_scope,
        },
    )


def records_to_documents(
    records: Iterable[DiseaseRecord],
    *,
    corpus_scope: str,
    min_hpo_terms: int,
) -> list[Document]:
    return [
        record_to_document(record, corpus_scope=corpus_scope, min_hpo_terms=min_hpo_terms)
        for record in records
    ]


def corpus_signature(documents: Iterable[Document]) -> str:
    text = "\n".join(
        f"{doc.metadata.get('orpha_code')}::{doc.metadata.get('disease')}::{doc.metadata.get('total_hpo_terms')}::{doc.metadata.get('indexed_hpo_terms')}"
        for doc in documents
    )
    return md5(text.encode("utf-8")).hexdigest()[:12]


def stable_document_id(document: Document, index: int) -> str:
    stable = f"{document.metadata.get('orpha_code', 'unknown')}::{document.metadata.get('disease', 'unknown')}::{index}"
    return md5(stable.encode("utf-8")).hexdigest()
