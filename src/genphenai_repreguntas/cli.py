from __future__ import annotations

import argparse
import json
import uuid
from hashlib import md5

from genphenai_repreguntas.config import load_settings
from genphenai_repreguntas.orphadata import load_orphadata_records


def cmd_stats() -> None:
    settings = load_settings()
    records = load_orphadata_records(
        settings.orphadata_xml,
        min_hpo_terms=settings.min_hpo_terms,
        min_diag_criteria=settings.min_diag_criteria,
        max_diseases=settings.max_diseases,
        max_hpo_per_disease=settings.max_hpo_per_disease,
    )
    print(f"Enfermedades elegibles: {len(records)}")
    print(f"Filtro minimo HPO: {settings.min_hpo_terms}")
    print(f"Max enfermedades: {'todas' if settings.max_diseases is None else settings.max_diseases}")
    signature_text = "\n".join(
        f"ORPHA:{record.orpha_code}::{record.disease}::{record.total_hpo_terms}::{len(record.hpo_terms)}"
        for record in records
    )
    signature = md5(signature_text.encode("utf-8")).hexdigest()[:12]
    print(f"Documentos LangChain esperados: {len(records)}")
    print(f"Coleccion sugerida: {settings.collection_prefix}_{signature}")


def cmd_index(force: bool) -> None:
    from genphenai_repreguntas.indexing import build_or_load_vector_store

    vector_store = build_or_load_vector_store(load_settings(), force=force)
    print(f"Fragmentos en Chroma: {vector_store._collection.count()}")


def _parse_candidate(value: str) -> tuple[str | None, str | None]:
    if ":" in value:
        code, name = value.split(":", 1)
        return code.strip() or None, name.strip() or None
    return value.strip() or None, None


def cmd_chat(case_text: str | None, candidates: list[str]) -> None:
    from genphenai_repreguntas.engine import RepreguntasEngine
    from genphenai_repreguntas.session import DiagnosticSession

    if not case_text:
        print("Describi el caso clinico inicial:")
        case_text = input("> ").strip()
    if not case_text:
        raise SystemExit("No se ingreso caso clinico.")

    candidate_codes: list[str] = []
    candidate_names: list[str] = []
    for raw_candidate in candidates:
        code, name = _parse_candidate(raw_candidate)
        if code:
            candidate_codes.append(code)
        if name:
            candidate_names.append(name)

    engine = RepreguntasEngine(load_settings())
    session_id = str(uuid.uuid4())
    session = DiagnosticSession(
        case_text=case_text,
        candidate_orpha_codes=candidate_codes,
        candidate_names=candidate_names,
    )

    print("\nChatbot iniciado. Responde en texto libre. Escribi 'salir' para cortar.\n")
    while True:
        evaluation = engine.evaluate(session)
        response = engine.response_from(session_id, session, evaluation)
        print(json.dumps(response, ensure_ascii=False, indent=2))

        if response["status"] == "final":
            break

        print("\nTu respuesta a las repreguntas:")
        answer = input("> ").strip()
        if answer.lower() in {"salir", "exit", "quit"}:
            print("Sesion interrumpida por el usuario.")
            break
        session.add_answer(answer)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(prog="genphenai-repreguntas")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats", help="Muestra estadisticas del corpus Orphadata")
    index = sub.add_parser("index", help="Construye o reutiliza el indice Chroma")
    index.add_argument("--force", action="store_true", help="Recrear la coleccion")
    chat = sub.add_parser("chat", help="Prueba interactiva del chatbot por consola")
    chat.add_argument("--case", help="Caso clinico inicial")
    chat.add_argument(
        "--candidate",
        action="append",
        default=[],
        help="Candidata Diagnostic.ar como ORPHA o ORPHA:nombre. Se puede repetir.",
    )
    args = parser.parse_args()
    if args.command == "stats":
        cmd_stats()
    elif args.command == "index":
        cmd_index(force=args.force)
    elif args.command == "chat":
        cmd_chat(case_text=args.case, candidates=args.candidate)


if __name__ == "__main__":
    main()
