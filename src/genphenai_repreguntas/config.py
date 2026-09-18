from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """Load a simple KEY=VALUE .env file without adding a runtime dependency."""

    dotenv_path = path or PROJECT_ROOT / ".env"
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _optional_int(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    lowered = value.strip().lower()
    if lowered in {"none", "all", "null"}:
        return None
    return int(value)


def _path_from_env(name: str, default: Path, root: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default.resolve()
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    orphadata_xml: Path = PROJECT_ROOT / "data" / "en_product4.xml"
    chroma_dir: Path = PROJECT_ROOT / "db_repreguntas_demo"
    runtime_chroma_dir: Path | None = None
    collection_prefix: str = "repreguntas_hpo_orphadata"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    embeddings_model: str = "text-embedding-3-large"
    min_hpo_terms: int = 8
    min_diag_criteria: int = 0
    max_diseases: int | None = None
    max_hpo_per_disease: int = 20
    index_batch_size: int = 512
    retriever_k: int = 8
    certainty_threshold: int = 90
    max_iterations: int = 7
    api_host: str = "127.0.0.1"
    api_port: int = 8010


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file)
    root = Path(os.getenv("REPREGUNTAS_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
    data_dir = _path_from_env("REPREGUNTAS_DATA_DIR", root / "data", root)
    runtime = os.getenv("REPREGUNTAS_RUNTIME_CHROMA_DIR", "").strip()
    return Settings(
        project_root=root,
        data_dir=data_dir,
        orphadata_xml=_path_from_env("REPREGUNTAS_ORPHADATA_XML", data_dir / "en_product4.xml", root),
        chroma_dir=_path_from_env("REPREGUNTAS_CHROMA_DIR", root / "db_repreguntas_demo", root),
        runtime_chroma_dir=_path_from_env("REPREGUNTAS_RUNTIME_CHROMA_DIR", root / "runtime_chroma", root)
        if runtime
        else None,
        collection_prefix=os.getenv("REPREGUNTAS_COLLECTION_PREFIX", "repreguntas_hpo_orphadata"),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("REPREGUNTAS_OPENAI_MODEL", "gpt-4o-mini"),
        embeddings_model=os.getenv("REPREGUNTAS_EMBEDDINGS_MODEL", "text-embedding-3-large"),
        min_hpo_terms=int(os.getenv("REPREGUNTAS_MIN_HPO_TERMS", "8")),
        min_diag_criteria=int(os.getenv("REPREGUNTAS_MIN_DIAG_CRIT", "0")),
        max_diseases=_optional_int("REPREGUNTAS_MAX_DISEASES", None),
        max_hpo_per_disease=int(os.getenv("REPREGUNTAS_MAX_HPO_PER_DISEASE", "20")),
        index_batch_size=int(os.getenv("REPREGUNTAS_INDEX_BATCH_SIZE", "512")),
        retriever_k=int(os.getenv("REPREGUNTAS_RETRIEVER_K", "8")),
        certainty_threshold=int(os.getenv("REPREGUNTAS_UMBRAL_CERTEZA", "90")),
        max_iterations=int(os.getenv("REPREGUNTAS_MAX_ITERACIONES", "7")),
        api_host=os.getenv("REPREGUNTAS_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("REPREGUNTAS_API_PORT", "8010")),
    )
