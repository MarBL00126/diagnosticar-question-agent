"""Parsing of the HPO annotation files and the Spanish label table."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from diagnosticar_preguntas.kb.frequency import EXCLUDED, parse_frequency

PHENOTYPE_ASPECT = "P"
NOT_QUALIFIER = "NOT"
_VERSION = re.compile(r"^#version:\s*(\S+)")
_NO_SYMBOL = frozenset({"", "-"})


class AnnotationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiseaseAnnotation:
    """One disease-phenotype link. ``frequency`` is ``None`` when the source does not state it."""

    disease_id: str
    hpo_id: str
    negated: bool
    frequency: float | None
    onset: str | None


@dataclass(frozen=True, slots=True)
class DiseaseAnnotations:
    """Parsed annotations. ``dropped`` counts rows whose term the ontology cannot resolve."""

    version: str
    disease_names: dict[str, str]
    annotations: tuple[DiseaseAnnotation, ...]
    dropped: int


@dataclass(frozen=True, slots=True)
class GeneDiseaseLink:
    gene_symbol: str
    ncbi_gene_id: str
    disease_id: str
    association_type: str | None


def _data_rows(path: Path, header_first_field: str) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        reader = csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)
        if reader.fieldnames is None or reader.fieldnames[0] != header_first_field:
            raise AnnotationError(f"{path.name}: unexpected header {reader.fieldnames}")
        yield from reader


def _hpoa_version(path: Path) -> str:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            if match := _VERSION.match(line):
                return match[1]
    raise AnnotationError(f"{path.name}: no #version header")


def parse_disease_annotations(
    path: Path, resolve: Callable[[str], str | None] = lambda hpo_id: hpo_id
) -> DiseaseAnnotations:
    """Read the phenotypic-abnormality rows (aspect P) of ``phenotype.hpoa``.

    ``resolve`` maps each term to its current id (alternative and obsolete
    ids) or to ``None`` to drop the row. A ``NOT`` qualifier or the frequency
    term ``Excluded`` makes the row a negated annotation. Rows repeated with
    different references are merged: the stated frequencies are averaged and
    the earliest-sorting onset kept.
    """
    names: dict[str, str] = {}
    grouped: dict[tuple[str, str, bool], tuple[list[float], list[str]]] = {}
    dropped = 0
    for row in _data_rows(path, "database_id"):
        if row["aspect"] != PHENOTYPE_ASPECT:
            continue
        disease_id, hpo_id = row["database_id"], resolve(row["hpo_id"])
        if hpo_id is None:
            dropped += 1
            continue
        names.setdefault(disease_id, row["disease_name"])
        raw_frequency = row["frequency"].strip()
        negated = row["qualifier"] == NOT_QUALIFIER or raw_frequency == EXCLUDED
        frequencies, onsets = grouped.setdefault((disease_id, hpo_id, negated), ([], []))
        if not negated and (frequency := parse_frequency(raw_frequency)) is not None:
            frequencies.append(frequency)
        if onset := row["onset"].strip():
            onsets.append(onset)

    annotations = tuple(
        DiseaseAnnotation(
            disease_id=disease_id,
            hpo_id=hpo_id,
            negated=negated,
            frequency=fmean(frequencies) if frequencies else None,
            onset=min(onsets) if onsets else None,
        )
        for (disease_id, hpo_id, negated), (frequencies, onsets) in sorted(grouped.items())
    )
    return DiseaseAnnotations(_hpoa_version(path), names, annotations, dropped)


def _ncbi_id(raw: str) -> str:
    return raw.removeprefix("NCBIGene:")


def parse_gene_disease_links(
    genes_to_disease: Path, genes_to_phenotype: Path
) -> tuple[GeneDiseaseLink, ...]:
    """Union of the curated gene-disease table and the pairs implied by gene-phenotype rows.

    ``association_type`` comes from ``genes_to_disease.txt``; pairs seen only
    in ``genes_to_phenotype.txt`` keep it as ``None``. Loci without an
    approved symbol (``-``) are skipped: the model ranks genes by symbol.
    """
    links: dict[tuple[str, str], GeneDiseaseLink] = {}
    for row in _data_rows(genes_to_disease, "ncbi_gene_id"):
        if row["gene_symbol"] in _NO_SYMBOL:
            continue
        key = (row["gene_symbol"], row["disease_id"])
        links[key] = GeneDiseaseLink(
            gene_symbol=row["gene_symbol"],
            ncbi_gene_id=_ncbi_id(row["ncbi_gene_id"]),
            disease_id=row["disease_id"],
            association_type=row["association_type"] or None,
        )
    for row in _data_rows(genes_to_phenotype, "ncbi_gene_id"):
        key = (row["gene_symbol"], row["disease_id"])
        if row["gene_symbol"] not in _NO_SYMBOL and key not in links:
            links[key] = GeneDiseaseLink(
                gene_symbol=row["gene_symbol"],
                ncbi_gene_id=_ncbi_id(row["ncbi_gene_id"]),
                disease_id=row["disease_id"],
                association_type=None,
            )
    return tuple(links[key] for key in sorted(links))


def parse_spanish_labels(path: Path) -> dict[str, str]:
    """``rdfs:label`` translations of a Babelon TSV, keyed by HPO id."""
    labels: dict[str, str] = {}
    for row in _data_rows(path, "source_language"):
        value = row["translation_value"].strip()
        if row["predicate_id"] == "rdfs:label" and value:
            labels[row["subject_id"].strip()] = value
    return labels
