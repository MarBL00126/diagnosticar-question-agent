"""Compatibility exports for older scripts.

New code should import from ``genphenai_repreguntas.config``.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from genphenai_repreguntas.config import Settings, load_dotenv, load_settings  # noqa: E402,F401

settings = load_settings()

BASE_DIR = settings.project_root
CHROMA_DIR = settings.chroma_dir
COLLECTION_NAME = settings.collection_prefix
ORPHADATA_XML = settings.orphadata_xml
OPENAI_MODEL = settings.openai_model
EMBEDDINGS_MODEL = settings.embeddings_model
UMBRAL_CERTEZA = settings.certainty_threshold
MAX_ITERACIONES = settings.max_iterations
RETRIEVER_K = settings.retriever_k
MIN_HPO_TERMS = settings.min_hpo_terms
MIN_DIAG_CRIT = settings.min_diag_criteria
CORPUS_TOP_N = settings.max_diseases
MAX_HPO_PER_DIS = settings.max_hpo_per_disease
API_HOST = settings.api_host
API_PORT = settings.api_port
