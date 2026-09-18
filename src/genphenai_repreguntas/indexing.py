from __future__ import annotations

import hashlib

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from genphenai_repreguntas.config import Settings, load_settings
from genphenai_repreguntas.documents import corpus_signature, records_to_documents, stable_document_id
from genphenai_repreguntas.orphadata import load_orphadata_records


def batched(items: list, batch_size: int):
    for start in range(0, len(items), batch_size):
        yield start, items[start:start + batch_size]


def build_or_load_vector_store(settings: Settings | None = None, *, force: bool = False) -> Chroma:
    settings = settings or load_settings()
    records = load_orphadata_records(
        settings.orphadata_xml,
        min_hpo_terms=settings.min_hpo_terms,
        min_diag_criteria=settings.min_diag_criteria,
        max_diseases=settings.max_diseases,
        max_hpo_per_disease=settings.max_hpo_per_disease,
    )
    scope = "all_eligible_orphadata" if settings.max_diseases is None else f"top_{settings.max_diseases}"
    documents = records_to_documents(records, corpus_scope=scope, min_hpo_terms=settings.min_hpo_terms)
    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150, add_start_index=True)
    splits = splitter.split_documents(documents)
    signature = corpus_signature(documents)
    collection_name = f"{settings.collection_prefix}_{signature}"

    vector_store = Chroma(
        collection_name=collection_name,
        embedding_function=OpenAIEmbeddings(model=settings.embeddings_model),
        persist_directory=str(settings.chroma_dir),
    )
    if force:
        vector_store.reset_collection()
    if vector_store._collection.count() == 0:
        ids = [stable_document_id(document, index) for index, document in enumerate(splits)]
        batch_size = max(1, settings.index_batch_size)
        for start, batch in batched(splits, batch_size):
            batch_ids = ids[start:start + len(batch)]
            vector_store.add_documents(batch, ids=batch_ids)
            print(f"Indexados {min(start + len(batch), len(splits))}/{len(splits)} fragmentos")
    return vector_store


def collection_name_for_current_corpus(settings: Settings | None = None) -> str:
    settings = settings or load_settings()
    records = load_orphadata_records(
        settings.orphadata_xml,
        min_hpo_terms=settings.min_hpo_terms,
        min_diag_criteria=settings.min_diag_criteria,
        max_diseases=settings.max_diseases,
        max_hpo_per_disease=settings.max_hpo_per_disease,
    )
    scope = "all_eligible_orphadata" if settings.max_diseases is None else f"top_{settings.max_diseases}"
    documents = records_to_documents(records, corpus_scope=scope, min_hpo_terms=settings.min_hpo_terms)
    return f"{settings.collection_prefix}_{corpus_signature(documents)}"
