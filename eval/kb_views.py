"""Knowledge-base views that hide from the agent what a de-circularised evaluation must not share.

The agent reads disease annotations only through ``KnowledgeBase.annotations``
and ``annotated_diseases_for_gene``. ``AnnotationOverlay`` replaces the
annotations of a few diseases in those two methods and shares everything else
(ontology, labels, gene links) with the base, so a view per case is free.

Two ways of choosing what to hide:

* ``hold_out`` splits a disease's positive annotations at random into a half
  the agent keeps and a half only the simulated patient draws from.
* ``HpoaReferences`` rebuilds a disease's annotations from ``phenotype.hpoa``
  without the rows that cite given publications, with the same reading and
  merge rule as the knowledge-base build (``kb.annotations``): aspect P only,
  ids resolved through the ontology, ``NOT`` or ``Excluded`` means negated,
  and rows repeated with different references merged by averaging the stated
  frequencies.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from eval.cases import positive_annotations
from eval.harness import REPO_ROOT, stable_seed

from diagnosticar_preguntas.kb.annotations import NOT_QUALIFIER, PHENOTYPE_ASPECT
from diagnosticar_preguntas.kb.frequency import EXCLUDED, parse_frequency
from diagnosticar_preguntas.kb.store import Annotation, KnowledgeBase

HPOA_PATH = REPO_ROOT / "data" / "sources" / "phenotype.hpoa"


class AnnotationOverlay(KnowledgeBase):
    """``base`` with the annotations of the diseases in ``overrides`` replaced.

    An empty tuple removes a disease's phenotype annotations: its genes then
    no longer list it among their annotated diseases.
    """

    def __init__(self, base: KnowledgeBase, overrides: Mapping[str, Sequence[Annotation]]) -> None:
        # Share the loaded tables instead of building them again.
        vars(self).update(vars(base))
        self._overrides = {disease: tuple(rows) for disease, rows in overrides.items()}

    def annotations(self, disease_id: str) -> tuple[Annotation, ...]:
        if disease_id in self._overrides:
            return self._overrides[disease_id]
        return super().annotations(disease_id)

    def annotated_diseases_for_gene(self, gene_symbol: str) -> tuple[str, ...]:
        return tuple(d for d in self.diseases_for_gene(gene_symbol) if self.annotations(d))


@dataclass(frozen=True, slots=True)
class HeldOut:
    """A disease's annotations split between the agent (``known``) and the patient (``hidden``).

    ``hidden`` holds only positive phenotypic annotations; ``known`` keeps the
    other positive ones plus every negated or zero-frequency row.
    """

    known: tuple[Annotation, ...]
    hidden: tuple[Annotation, ...]


def hold_out(kb: KnowledgeBase, disease_id: str, seed: object) -> HeldOut:
    """Random half of the positive annotations for the patient (the larger half when odd)."""
    positives = sorted(positive_annotations(kb, disease_id), key=lambda a: a.hpo_id)
    random.Random(stable_seed("hold-out", seed, disease_id)).shuffle(positives)
    hidden = tuple(positives[len(positives) // 2 :])
    known = tuple(a for a in kb.annotations(disease_id) if a not in hidden)
    return HeldOut(known=known, hidden=hidden)


@dataclass(frozen=True, slots=True)
class _Row:
    hpo_id: str
    negated: bool
    frequency: float | None
    onset: str | None
    references: frozenset[str]


class HpoaReferences:
    """The annotation rows of ``phenotype.hpoa`` with their references, by disease."""

    def __init__(self, rows: Mapping[str, Sequence[_Row]]) -> None:
        self._rows = rows

    @classmethod
    def read(
        cls, kb: KnowledgeBase, path: Path = HPOA_PATH, diseases: Collection[str] | None = None
    ) -> HpoaReferences:
        """Rows of ``diseases`` (every disease when ``None``), resolved with ``kb``'s ontology."""
        rows: dict[str, list[_Row]] = defaultdict(list)
        for row in _hpoa_rows(path):
            disease_id = row["database_id"]
            if row["aspect"] != PHENOTYPE_ASPECT or (
                diseases is not None and disease_id not in diseases
            ):
                continue
            hpo_id = kb.resolve(row["hpo_id"])
            if hpo_id is None:
                continue
            raw_frequency = row["frequency"].strip()
            negated = row["qualifier"] == NOT_QUALIFIER or raw_frequency == EXCLUDED
            rows[disease_id].append(
                _Row(
                    hpo_id=hpo_id,
                    negated=negated,
                    frequency=None if negated else parse_frequency(raw_frequency),
                    onset=row["onset"].strip() or None,
                    references=frozenset(
                        ref.strip() for ref in row["reference"].split(";") if ref.strip()
                    ),
                )
            )
        return cls(rows)

    def references(self, disease_id: str) -> frozenset[str]:
        return frozenset(ref for row in self._rows.get(disease_id, ()) for ref in row.references)

    def annotations_without(
        self, disease_id: str, references: Collection[str]
    ) -> tuple[Annotation, ...]:
        """The disease's merged annotations, leaving out every row citing any of ``references``."""
        grouped: dict[tuple[str, bool], tuple[list[float], list[str]]] = {}
        for row in self._rows.get(disease_id, ()):
            if not row.references.isdisjoint(references):
                continue
            frequencies, onsets = grouped.setdefault((row.hpo_id, row.negated), ([], []))
            if row.frequency is not None:
                frequencies.append(row.frequency)
            if row.onset:
                onsets.append(row.onset)
        return tuple(
            Annotation(
                hpo_id=hpo_id,
                negated=negated,
                frequency=fmean(frequencies) if frequencies else None,
                onset=min(onsets) if onsets else None,
            )
            for (hpo_id, negated), (frequencies, onsets) in sorted(grouped.items())
        )


def _hpoa_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        lines = (line for line in handle if not line.startswith("#"))
        yield from csv.DictReader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)
