"""Build the read-only SQLite knowledge base from the pinned HPO sources.

The output is byte-for-byte reproducible for the same inputs and SQLite
library: rows are inserted in sorted order, nothing time-dependent is stored,
the page size is fixed and the file is vacuumed before it is published. The
file's sha256 is written next to it as ``<out>.sha256``.

    diagnosticar-preguntas-kb --source-dir data/sources --out data/kb.sqlite
    diagnosticar-preguntas-kb --download --out data/kb.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from diagnosticar_preguntas.kb.annotations import (
    DiseaseAnnotation,
    GeneDiseaseLink,
    parse_disease_annotations,
    parse_gene_disease_links,
    parse_spanish_labels,
)
from diagnosticar_preguntas.kb.frequency import FREQUENCY_CATEGORIES
from diagnosticar_preguntas.kb.ontology import (
    Ontology,
    ancestor_distances,
    depths,
    parse_obographs,
    resolve_id,
)
from diagnosticar_preguntas.kb.requires_testing import OBSERVABLE_ROOTS, REQUIRES_TESTING_ROOTS
from diagnosticar_preguntas.kb.schema import INDEXES, KB_SCHEMA_VERSION, PAGE_SIZE, TABLES
from diagnosticar_preguntas.kb.sources import (
    DISEASE_ANNOTATIONS,
    GENES_TO_DISEASE,
    GENES_TO_PHENOTYPE,
    ONTOLOGY,
    SOURCES,
    SPANISH_LABELS,
    Source,
    sha256_file,
)

DEFAULT_OUT = Path("data/kb.sqlite")
DEFAULT_DOWNLOAD_DIR = Path("data/sources")


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BuildSummary:
    path: Path
    sha256: str
    seconds: float
    counts: Mapping[str, int]


def locate_sources(
    directories: Sequence[Path], sources: Iterable[Source] = SOURCES
) -> dict[str, Path]:
    """Find each source by file name in the first directory that has it."""
    located: dict[str, Path] = {}
    for source in sources:
        candidates = [d / source.filename for d in directories if (d / source.filename).is_file()]
        if not candidates:
            searched = ", ".join(str(d) for d in directories)
            raise SourceError(f"{source.filename} not found in {searched}")
        located[source.name] = candidates[0]
    return located


def download_sources(
    directory: Path,
    sources: Iterable[Source] = SOURCES,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Path]:
    """Fetch every source into ``directory``, skipping files already present and verified.

    ``client`` (closed afterwards) lets tests serve the files without network.
    """
    directory.mkdir(parents=True, exist_ok=True)
    located: dict[str, Path] = {}
    with client or httpx.Client(follow_redirects=True, timeout=120.0) as client:
        for source in sources:
            target = directory / source.filename
            if not (target.is_file() and sha256_file(target) == source.sha256):
                partial = target.with_name(target.name + ".part")
                with client.stream("GET", source.url) as response:
                    response.raise_for_status()
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                if (actual := sha256_file(partial)) != source.sha256:
                    partial.unlink()
                    raise SourceError(f"{source.url}: sha256 {actual}, expected {source.sha256}")
                partial.replace(target)
            located[source.name] = target
    return located


def _alt_ids(ontology: Ontology) -> dict[str, str]:
    return {
        alt: term.hpo_id
        for term in ontology.terms.values()
        for alt in term.alt_ids
        if alt not in ontology.terms
    }


def _requires_testing(
    closure: Mapping[str, Mapping[str, int]], roots: Iterable[str], observable: Iterable[str]
) -> frozenset[str]:
    study, seen = frozenset(roots), frozenset(observable)
    return frozenset(
        term
        for term, ancestors in closure.items()
        if (term in study or not study.isdisjoint(ancestors))
        and not (term in seen or not seen.isdisjoint(ancestors))
    )


def _genes(links: Iterable[GeneDiseaseLink]) -> list[tuple[object, ...]]:
    genes: dict[str, str] = {}
    for link in links:
        known = genes.get(link.gene_symbol)
        if known is None or int(link.ncbi_gene_id) < int(known):
            genes[link.gene_symbol] = link.ncbi_gene_id
    return sorted(genes.items())


def build_kb(
    sources: Mapping[str, Path],
    out: Path,
    *,
    require_pinned: bool = True,
    requires_testing_roots: Iterable[str] = REQUIRES_TESTING_ROOTS,
    observable_roots: Iterable[str] = OBSERVABLE_ROOTS,
    manifest: Iterable[Source] = SOURCES,
) -> BuildSummary:
    """Parse the sources and write the knowledge base to ``out``.

    With ``require_pinned`` every input must match the sha256 in the manifest.
    Test fixtures build from small synthetic files and turn it off; the
    ``meta`` table then records the inputs as unpinned.
    """
    started = time.perf_counter()
    manifest_by_name = {source.name: source for source in manifest}
    digests = {name: sha256_file(path) for name, path in sorted(sources.items())}
    pinned = {name: digests[name] == manifest_by_name[name].sha256 for name in digests}
    if require_pinned and not all(pinned.values()):
        mismatched = sorted(name for name, ok in pinned.items() if not ok)
        raise SourceError(f"sources do not match the pinned sha256: {', '.join(mismatched)}")

    ontology = parse_obographs(sources[ONTOLOGY.name])
    alt_ids = _alt_ids(ontology)

    def resolve(hpo_id: str) -> str | None:
        return resolve_id(hpo_id, ontology.terms, alt_ids)

    closure = ancestor_distances(ontology.parents)
    depth = depths(ontology.parents)
    roots = sorted(set(requires_testing_roots))
    observable = sorted(set(observable_roots))
    missing_roots = [root for root in (*roots, *observable) if resolve(root) != root]
    if missing_roots:
        raise SourceError(f"requires-testing roots not in the ontology: {missing_roots}")
    testing = _requires_testing(closure, roots, observable)
    spanish = parse_spanish_labels(sources[SPANISH_LABELS.name])

    parsed = parse_disease_annotations(sources[DISEASE_ANNOTATIONS.name], resolve)
    if parsed.version != ontology.version:
        raise SourceError(
            f"annotations are for HPO {parsed.version}, ontology is {ontology.version}"
        )
    links = parse_gene_disease_links(
        sources[GENES_TO_DISEASE.name], sources[GENES_TO_PHENOTYPE.name]
    )
    disease_ids = {a.disease_id for a in parsed.annotations} | {link.disease_id for link in links}

    meta = {
        "kb_schema_version": str(KB_SCHEMA_VERSION),
        "hpo_version": ontology.version,
        "requires_testing_roots": ",".join(roots),
        "observable_roots": ",".join(observable),
        "frequency_categories": json.dumps(FREQUENCY_CATEGORIES, sort_keys=True),
        "dropped_annotations": str(parsed.dropped),
    }
    for name, digest in digests.items():
        meta[f"source.{name}.sha256"] = digest
        meta[f"source.{name}.url"] = manifest_by_name[name].url
        meta[f"source.{name}.pinned"] = "true" if pinned[name] else "false"

    rows: dict[str, list[tuple[object, ...]]] = {
        "meta": sorted(meta.items()),
        "term": [
            (
                term.hpo_id,
                term.label,
                spanish.get(term.hpo_id),
                int(term.obsolete),
                term.replaced_by,
                depth.get(term.hpo_id),
                int(term.hpo_id in testing),
            )
            for term in sorted(ontology.terms.values(), key=lambda t: t.hpo_id)
        ],
        "alt_id": sorted(alt_ids.items()),
        "parent": sorted((t, p) for t, ps in ontology.parents.items() for p in ps),
        "ancestor": sorted(
            (t, a, d) for t, ancestors in closure.items() for a, d in ancestors.items()
        ),
        "disease": [(d, parsed.disease_names.get(d)) for d in sorted(disease_ids)],
        "annotation": [_annotation_row(a) for a in parsed.annotations],
        "gene": _genes(links),
        "gene_disease": [(g.gene_symbol, g.disease_id, g.association_type) for g in links],
    }
    _write(out, rows)

    digest = sha256_file(out)
    out.with_name(out.name + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    counts = {table: len(table_rows) for table, table_rows in rows.items() if table != "meta"}
    counts["negated_annotations"] = sum(1 for a in parsed.annotations if a.negated)
    counts["spanish_labels"] = sum(1 for row in rows["term"] if row[2] is not None)
    counts["dropped_annotations"] = parsed.dropped
    return BuildSummary(out, digest, time.perf_counter() - started, counts)


def _annotation_row(annotation: DiseaseAnnotation) -> tuple[object, ...]:
    return (
        annotation.disease_id,
        annotation.hpo_id,
        int(annotation.negated),
        annotation.frequency,
        annotation.onset,
    )


def _write(out: Path, rows: Mapping[str, list[tuple[object, ...]]]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    partial.unlink(missing_ok=True)
    connection = sqlite3.connect(partial, isolation_level=None)
    try:
        connection.execute(f"PRAGMA page_size = {PAGE_SIZE}")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute(f"PRAGMA user_version = {KB_SCHEMA_VERSION}")
        connection.execute("BEGIN")
        for statement in TABLES.split(";"):
            if statement.strip():
                connection.execute(statement)
        for table, table_rows in rows.items():
            if table_rows:
                placeholders = ", ".join("?" * len(table_rows[0]))
                connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", table_rows)
        for statement in INDEXES.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute("COMMIT")
        connection.execute("VACUUM")
    finally:
        connection.close()
    os.replace(partial, out)


def _print_summary(summary: BuildSummary) -> None:
    print(f"Wrote {summary.path} in {summary.seconds:.1f} s")
    print(f"  sha256 {summary.sha256}")
    print(f"  size   {summary.path.stat().st_size / 1e6:.1f} MB")
    for name, count in summary.counts.items():
        print(f"  {name:<22}{count:>9,}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="diagnosticar-preguntas-kb",
        description="Build the question agent's knowledge base from the pinned HPO release.",
    )
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument(
        "--source-dir",
        type=Path,
        action="append",
        help="directory holding the source files (repeatable; the first match wins)",
    )
    origin.add_argument("--download", action="store_true", help="fetch the pinned sources by URL")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="build even if a source file does not match its pinned sha256",
    )
    args = parser.parse_args(argv)

    try:
        if args.download:
            sources = download_sources(args.download_dir)
        else:
            sources = locate_sources(args.source_dir)
        summary = build_kb(sources, args.out, require_pinned=not args.allow_unpinned)
    except (SourceError, httpx.HTTPError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
