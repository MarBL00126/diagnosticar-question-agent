"""Read-only, in-memory view of the knowledge base.

``KnowledgeBase.open`` reads the whole SQLite file once (about a second for
the full HPO) and closes it; afterwards every lookup is a dictionary access,
so the object is safe to share between threads and requests.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from diagnosticar_preguntas.kb.ontology import PHENOTYPIC_ABNORMALITY, resolve_id
from diagnosticar_preguntas.kb.schema import KB_SCHEMA_VERSION


class KnowledgeBaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Term:
    hpo_id: str
    label_en: str
    label_es: str | None
    obsolete: bool
    replaced_by: str | None
    depth: int | None
    requires_testing: bool


@dataclass(frozen=True, slots=True)
class Annotation:
    """A phenotype of a disease. ``frequency`` is ``None`` when the source does not state it."""

    hpo_id: str
    negated: bool
    frequency: float | None
    onset: str | None


_EMPTY: frozenset[str] = frozenset()


class KnowledgeBase:
    """Ontology, disease annotations and gene-disease links of one HPO release."""

    def __init__(
        self,
        *,
        meta: Mapping[str, str],
        terms: Mapping[str, Term],
        alt_ids: Mapping[str, str],
        parents: Mapping[str, frozenset[str]],
        ancestors: Mapping[str, frozenset[str]],
        disease_names: Mapping[str, str | None],
        annotations: Mapping[str, tuple[Annotation, ...]],
        gene_diseases: Mapping[str, tuple[str, ...]],
    ) -> None:
        self._meta = MappingProxyType(dict(meta))
        self._terms = terms
        self._alt_ids = alt_ids
        self._parents = parents
        self._ancestors = ancestors
        descendants: dict[str, set[str]] = defaultdict(set)
        for term, term_ancestors in ancestors.items():
            for ancestor in term_ancestors:
                descendants[ancestor].add(term)
        self._descendants = {term: frozenset(ds) for term, ds in descendants.items()}
        self._disease_names = disease_names
        self._annotations = annotations
        self._gene_diseases = gene_diseases
        disease_genes: dict[str, list[str]] = defaultdict(list)
        for gene, diseases in gene_diseases.items():
            for disease in diseases:
                disease_genes[disease].append(gene)
        self._disease_genes = {d: tuple(sorted(gs)) for d, gs in disease_genes.items()}

    @classmethod
    def open(cls, path: Path | str) -> KnowledgeBase:
        path = Path(path)
        if not path.is_file():
            raise KnowledgeBaseError(f"knowledge base not found: {path}")
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return cls._load(connection)
        finally:
            connection.close()

    @classmethod
    def _load(cls, connection: sqlite3.Connection) -> KnowledgeBase:
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        schema = meta.get("kb_schema_version")
        if schema != str(KB_SCHEMA_VERSION):
            raise KnowledgeBaseError(
                f"knowledge base schema {schema}, this code reads {KB_SCHEMA_VERSION}; rebuild it"
            )
        terms = {
            row[0]: Term(row[0], row[1], row[2], bool(row[3]), row[4], row[5], bool(row[6]))
            for row in connection.execute(
                "SELECT hpo_id, label_en, label_es, obsolete, replaced_by, depth, requires_testing"
                " FROM term"
            )
        }
        alt_ids = dict(connection.execute("SELECT alt_id, hpo_id FROM alt_id"))
        parents = _group(connection.execute("SELECT hpo_id, parent_id FROM parent"))
        ancestors = _group(connection.execute("SELECT hpo_id, ancestor_id FROM ancestor"))
        disease_names = dict(connection.execute("SELECT disease_id, name FROM disease"))
        annotations: dict[str, list[Annotation]] = defaultdict(list)
        for disease_id, hpo_id, negated, frequency, onset in connection.execute(
            "SELECT disease_id, hpo_id, negated, frequency, onset FROM annotation"
        ):
            annotations[disease_id].append(Annotation(hpo_id, bool(negated), frequency, onset))
        gene_diseases: dict[str, list[str]] = defaultdict(list)
        for gene, disease in connection.execute(
            "SELECT gene_symbol, disease_id FROM gene_disease ORDER BY gene_symbol, disease_id"
        ):
            gene_diseases[gene].append(disease)
        return cls(
            meta=meta,
            terms=terms,
            alt_ids=alt_ids,
            parents={t: frozenset(ps) for t, ps in parents.items()},
            ancestors={t: frozenset(a) for t, a in ancestors.items()},
            disease_names=disease_names,
            annotations={d: tuple(a) for d, a in annotations.items()},
            gene_diseases={g: tuple(ds) for g, ds in gene_diseases.items()},
        )

    @property
    def meta(self) -> Mapping[str, str]:
        return self._meta

    @property
    def hpo_version(self) -> str:
        return self._meta["hpo_version"]

    @property
    def version(self) -> str:
        """Identifier for API responses: schema version plus HPO release."""
        return f"kb{self._meta['kb_schema_version']}+hpo.{self.hpo_version}"

    def label_es(self, hpo_id: str) -> str | None:
        term = self._terms.get(hpo_id)
        return term.label_es if term else None

    def label_en(self, hpo_id: str) -> str | None:
        term = self._terms.get(hpo_id)
        return term.label_en if term else None

    def term(self, hpo_id: str) -> Term | None:
        return self._terms.get(hpo_id)

    def term_ids(self) -> Iterator[str]:
        return iter(self._terms)

    def resolve(self, hpo_id: str) -> str | None:
        """Current id for ``hpo_id``, following alternative ids and obsolete replacements."""
        return resolve_id(hpo_id, self._terms, self._alt_ids)

    def resolve_all(self, hpo_ids: Iterable[str]) -> frozenset[str]:
        """Current ids of the resolvable terms in ``hpo_ids``; the rest are dropped."""
        return frozenset(r for hpo_id in hpo_ids if (r := self.resolve(hpo_id)) is not None)

    def parents(self, hpo_id: str) -> frozenset[str]:
        return self._parents.get(hpo_id, _EMPTY)

    def ancestors(self, hpo_id: str) -> frozenset[str]:
        """Proper ancestors (the term itself excluded)."""
        return self._ancestors.get(hpo_id, _EMPTY)

    def descendants(self, hpo_id: str) -> frozenset[str]:
        """Proper descendants (the term itself excluded)."""
        return self._descendants.get(hpo_id, _EMPTY)

    def is_phenotypic_abnormality(self, hpo_id: str) -> bool:
        """True for terms strictly below ``Phenotypic abnormality``, the only askable branch."""
        return PHENOTYPIC_ABNORMALITY in self.ancestors(hpo_id)

    def depth(self, hpo_id: str) -> int | None:
        term = self._terms.get(hpo_id)
        return term.depth if term else None

    def requires_testing(self, hpo_id: str) -> bool:
        term = self._terms.get(hpo_id)
        return term.requires_testing if term else False

    def disease_name(self, disease_id: str) -> str | None:
        return self._disease_names.get(disease_id)

    def annotations(self, disease_id: str) -> tuple[Annotation, ...]:
        return self._annotations.get(disease_id, ())

    def diseases_for_gene(self, gene_symbol: str) -> tuple[str, ...]:
        return self._gene_diseases.get(gene_symbol, ())

    def annotated_diseases_for_gene(self, gene_symbol: str) -> tuple[str, ...]:
        """Diseases of the gene that have at least one phenotype annotation."""
        return tuple(d for d in self.diseases_for_gene(gene_symbol) if d in self._annotations)

    def genes_for_disease(self, disease_id: str) -> tuple[str, ...]:
        return self._disease_genes.get(disease_id, ())

    def genes(self) -> Iterator[str]:
        return iter(self._gene_diseases)


def _group(rows: Iterable[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for key, value in rows:
        grouped[key].append(value)
    return grouped
