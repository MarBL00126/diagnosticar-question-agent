"""How the prototype (commit c2e3548) split its Orphadata documents for retrieval.

Needs ``langchain-text-splitters`` (which brings ``langchain-core``), the
prototype's own splitter, and nothing from version 2, so it runs in a separate
environment:

    python3 -m venv /tmp/fragmentos
    /tmp/fragmentos/bin/pip install langchain-text-splitters==1.1.2
    /tmp/fragmentos/bin/python -m eval.prototipo.chunks --orphadata-dir DIR

Builds the documents with the prototype's ``records_to_documents``, splits them
with the parameters of its ``indexing.py`` and counts the fragments, the
fragments whose text does not name their disease, and how the retrieved
context reaches the prompt. Writes ``eval/prototipo/fragmentos.json``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from eval.prototipo.prototype import load
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

HERE = Path(__file__).resolve().parent

# ``indexing.py`` of the prototype, ``build_or_load_vector_store``.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
# ``config.py`` of the prototype, ``Settings.retriever_k``.
RETRIEVER_K = 8


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--orphadata-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "fragmentos.json")
    args = parser.parse_args(argv)

    with load() as proto:
        defaults = proto.module("config").Settings()
        records = proto.module("orphadata").load_orphadata_records(
            args.orphadata_dir / "en_product4.xml",
            min_hpo_terms=defaults.min_hpo_terms,
            min_diag_criteria=defaults.min_diag_criteria,
            max_diseases=defaults.max_diseases,
            max_hpo_per_disease=defaults.max_hpo_per_disease,
        )
        documents = proto.module("documents").records_to_documents(
            records, corpus_scope="all_eligible_orphadata", min_hpo_terms=defaults.min_hpo_terms
        )
        template = proto.module("prompts").CONFIDENCE_TEMPLATE
        commit = proto.commit

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP, add_start_index=True
    )
    chunks = splitter.split_documents(documents)
    per_document = Counter(chunk.metadata["orpha_code"] for chunk in chunks)

    def names_disease(chunk: Any) -> bool:
        return chunk.metadata["disease"] in chunk.page_content

    def has_terms(chunk: Any) -> bool:
        return "(HP:" in chunk.page_content

    term_chunks = Counter(c.metadata["orpha_code"] for c in chunks if has_terms(c))
    prompt = (
        ChatPromptTemplate.from_template(template)
        .format_messages(
            context=chunks[:RETRIEVER_K],
            symptoms_history="",
            top_candidates="",
            asked_questions="",
            clinical_findings="",
        )[0]
        .content
    )
    lengths = sorted(len(d.page_content) for d in documents)
    results = {
        "commit": commit,
        "langchain_text_splitters": version("langchain-text-splitters"),
        "langchain_core": version("langchain-core"),
        "documentos": len(documents),
        "caracteres_por_documento": {"mediana": lengths[len(lengths) // 2], "max": lengths[-1]},
        "fragmentos": len(chunks),
        "documentos_partidos": sum(1 for n in per_document.values() if n > 1),
        "fragmentos_por_documento": dict(sorted(Counter(per_document.values()).items())),
        "fragmentos_sin_el_nombre_en_el_texto": sum(1 for c in chunks if not names_disease(c)),
        "fragmentos_con_fenotipos_sin_el_nombre": sum(
            1 for c in chunks if has_terms(c) and not names_disease(c)
        ),
        "primeros_fragmentos_sin_fenotipos": sum(
            1 for c in chunks if c.metadata["start_index"] == 0 and not has_terms(c)
        ),
        "enfermedades_con_la_lista_de_fenotipos_partida": sum(
            1 for n in term_chunks.values() if n > 1
        ),
        "contexto_llega_como_repr_de_python": "[Document(" in prompt,
    }
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
