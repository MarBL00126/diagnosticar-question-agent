"""Import Mariano Brizuela's prototype (commit c2e3548) straight from git history.

The prototype's ``src/`` tree is extracted from the commit into a temporary
directory and imported from there, so the measurements run the code exactly as
it was committed. Its LLM and retrieval dependencies (LangChain, Chroma and
OpenAI) are replaced by inert placeholders when they are not installed: only
deterministic functions are exercised, the same ones its own tests call.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import types
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE_COMMIT = "c2e3548"

# Third-party modules the prototype imports at module level, with the names it takes from each.
_THIRD_PARTY: dict[str, dict[str, tuple[str, ...]]] = {
    "langchain_core": {
        "langchain_core.prompts": ("ChatPromptTemplate",),
        "langchain_core.documents": ("Document",),
    },
    "langchain_openai": {"langchain_openai": ("ChatOpenAI", "OpenAIEmbeddings")},
    "langchain_chroma": {"langchain_chroma": ("Chroma",)},
    "langchain_text_splitters": {
        "langchain_text_splitters": ("RecursiveCharacterTextSplitter",),
    },
}


def git(*args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True
    ).stdout


def git_text(*args: str) -> str:
    return git(*args).decode("utf-8").strip()


class _Placeholder:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError(
            f"{type(self).__name__} is a placeholder: the prototype's LLM and retrieval "
            "stack is not installed, and these measurements never need it"
        )


def _install_placeholders() -> list[str]:
    """Register placeholder modules for the missing third-party packages; returns their names."""
    installed: list[str] = []
    for package, modules in _THIRD_PARTY.items():
        if package in sys.modules or importlib.util.find_spec(package) is not None:
            continue
        sys.modules[package] = types.ModuleType(package)
        installed.append(package)
        for name, attributes in modules.items():
            module = sys.modules.get(name) or types.ModuleType(name)
            for attribute in attributes:
                setattr(module, attribute, type(attribute, (_Placeholder,), {}))
            sys.modules[name] = module
            if name != package:
                setattr(sys.modules[package], name.rsplit(".", 1)[1], module)
                installed.append(name)
    return installed


@dataclass(frozen=True, slots=True)
class Prototype:
    commit: str
    package: str
    placeholders: tuple[str, ...]
    module: Callable[[str], types.ModuleType]


@contextmanager
def load(commit: str = PROTOTYPE_COMMIT) -> Iterator[Prototype]:
    """The prototype's package, importable by module name while the context is open."""
    with tempfile.TemporaryDirectory(prefix="prototipo-") as tmp:
        archive = git("archive", "--format=tar", commit, "src")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        src = Path(tmp) / "src"
        package = next(path.parent.name for path in src.glob("*/session.py"))
        placeholders = tuple(_install_placeholders())
        sys.path.insert(0, str(src))
        try:
            yield Prototype(
                commit=git_text("rev-parse", commit),
                package=package,
                placeholders=placeholders,
                module=lambda name: importlib.import_module(f"{package}.{name}"),
            )
        finally:
            sys.path.remove(str(src))
