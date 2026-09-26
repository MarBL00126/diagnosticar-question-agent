"""Parsing of the HPO ontology (``hp.json``, OBO Graphs) and its closure."""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROOT = "HP:0000001"
PHENOTYPIC_ABNORMALITY = "HP:0000118"

_REPLACED_BY = "http://purl.obolibrary.org/obo/IAO_0100001"
_ALTERNATIVE_ID = "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId"
_RELEASE = re.compile(r"/releases/(\d{4}-\d{2}-\d{2})/")
_HPO_IRI = re.compile(r"^http://purl\.obolibrary\.org/obo/HP_(\d{7})$")


class OntologyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OntologyTerm:
    hpo_id: str
    label: str
    obsolete: bool
    replaced_by: str | None
    alt_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Ontology:
    version: str
    terms: Mapping[str, OntologyTerm]
    parents: Mapping[str, frozenset[str]]


class TermStatus(Protocol):
    @property
    def obsolete(self) -> bool: ...

    @property
    def replaced_by(self) -> str | None: ...


def resolve_id(
    hpo_id: str, terms: Mapping[str, TermStatus], alt_ids: Mapping[str, str]
) -> str | None:
    """Current id for ``hpo_id``: follows alternative ids and ``replaced_by`` of obsolete terms.

    Returns ``None`` for unknown ids and for obsolete terms without a replacement.
    """
    current: str | None = alt_ids.get(hpo_id, hpo_id)
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        term = terms.get(current)
        if term is None:
            return None
        if not term.obsolete:
            return current
        current = term.replaced_by
    return None


def _hpo_id(iri: str) -> str | None:
    match = _HPO_IRI.match(iri)
    return f"HP:{match[1]}" if match else None


def parse_obographs(path: Path) -> Ontology:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    graph: dict[str, Any] = document["graphs"][0]

    version_iri = str(graph.get("meta", {}).get("version", ""))
    release = _RELEASE.search(version_iri)
    if release is None:
        raise OntologyError(f"cannot read the HPO release from {version_iri!r}")

    terms: dict[str, OntologyTerm] = {}
    for node in graph["nodes"]:
        hpo_id = _hpo_id(node["id"])
        if hpo_id is None or node.get("type") != "CLASS":
            continue
        meta = node.get("meta", {})
        properties = meta.get("basicPropertyValues", [])
        replaced_by = next((p["val"] for p in properties if p["pred"] == _REPLACED_BY), None)
        alt_ids = sorted(p["val"] for p in properties if p["pred"] == _ALTERNATIVE_ID)
        terms[hpo_id] = OntologyTerm(
            hpo_id=hpo_id,
            label=node.get("lbl", ""),
            obsolete=bool(meta.get("deprecated", False)),
            replaced_by=replaced_by,
            alt_ids=tuple(alt_ids),
        )

    parents: dict[str, set[str]] = {hpo_id: set() for hpo_id in terms}
    for edge in graph["edges"]:
        if edge["pred"] != "is_a":
            continue
        child, parent = _hpo_id(edge["sub"]), _hpo_id(edge["obj"])
        if child in terms and parent in terms:
            parents[child].add(parent)

    return Ontology(
        version=release[1],
        terms=terms,
        parents={hpo_id: frozenset(ps) for hpo_id, ps in parents.items()},
    )


def ancestor_distances(parents: Mapping[str, Iterable[str]]) -> dict[str, dict[str, int]]:
    """Shortest ``is_a`` distance from every term to each of its proper ancestors."""
    closure: dict[str, dict[str, int]] = {}
    for start in parents:
        distances: dict[str, int] = {}
        frontier = deque([(start, 0)])
        while frontier:
            term, distance = frontier.popleft()
            for parent in parents[term]:
                if parent == start:
                    raise OntologyError(f"is_a cycle through {start}")
                if parent not in distances:
                    distances[parent] = distance + 1
                    frontier.append((parent, distance + 1))
        closure[start] = distances
    return closure


def depths(parents: Mapping[str, Iterable[str]], root: str = ROOT) -> dict[str, int]:
    """Shortest distance from ``root`` down to each term reachable from it."""
    children: dict[str, list[str]] = {}
    for child, ps in parents.items():
        for parent in ps:
            children.setdefault(parent, []).append(child)
    result = {root: 0}
    frontier = deque([root])
    while frontier:
        term = frontier.popleft()
        for child in children.get(term, ()):
            if child not in result:
                result[child] = result[term] + 1
                frontier.append(child)
    return result
