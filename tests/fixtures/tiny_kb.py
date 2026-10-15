"""A tiny synthetic HPO release, built into a knowledge base by the real build code.

The files mimic the upstream formats (OBO Graphs JSON, ``phenotype.hpoa``,
the two gene tables and a Babelon TSV) with a handful of terms, diseases and
genes chosen to exercise every rule of the build, the re-ranker and the
question selector.

Ontology (depth in brackets)::

    All [0]
    ├── Mode of inheritance ─ Autosomal dominant inheritance
    └── Phenotypic abnormality [1]
        ├── Abnormality of the nervous system [2]
        │   ├── Abnormal nervous system physiology [3]   grouping label
        │   │   ├── Seizure [4]                          alt id HP:0001275
        │   │   │   ├── Focal-onset seizure [5]
        │   │   │   └── Generalized-onset seizure [5]    no Spanish label
        │   │   ├── Hypotonia [4]
        │   │   └── Abnormal speech pattern [4]          leaf with a grouping-like label
        │   └── Morphological CNS abnormality [3]        requires-testing root
        │       ├── Microcephaly [4]                     also under skull size (observable)
        │       └── Agenesis of corpus callosum [4]
        ├── Abnormality of head or neck [2] ─ Abnormality of skull size [3] ─ Microcephaly
        ├── Abnormality of metabolism/homeostasis [2]    requires-testing root
        │   └── Lactic acidosis [3]
        └── Abnormality of the eye [2] ─ Visual impairment [3] ─ Blindness [4]
    obsolete HP:0000057, replaced by Blindness

Every other root of ``REQUIRES_TESTING_ROOTS`` / ``OBSERVABLE_ROOTS`` is
added as a bare child of Phenotypic abnormality so the default build rules
apply unchanged.

Genes and diseases::

    GENE_A  OMIM:100001  Seizure obligate, Hypotonia 1/2, Blindness very rare
    GENE_B  OMIM:100002  Microcephaly 3/4, NOT Seizure, Lactic acidosis (no frequency)
    GENE_C  OMIM:100003  Focal-onset seizure 80%, Visual impairment occasional
            ORPHA:300    Blindness frequent, Visual impairment 1/2,
                         Hypotonia 1/4 and 3/4 (two rows, merged)
    GENE_D  OMIM:100004  no phenotype annotations
    GENE_E  OMIM:100005  obsolete HP:0000057 very frequent (filed under Blindness),
                         Hypotonia Excluded, Lactic acidosis 0/5, one unknown term
                         (link only in genes_to_phenotype)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diagnosticar_preguntas.kb.build import BuildSummary, build_kb
from diagnosticar_preguntas.kb.requires_testing import OBSERVABLE_ROOTS, REQUIRES_TESTING_ROOTS
from diagnosticar_preguntas.kb.sources import SOURCES
from diagnosticar_preguntas.kb.store import KnowledgeBase

HPO_VERSION = "2026-02-16"

ALL = "HP:0000001"
MODE_OF_INHERITANCE = "HP:0000005"
AUTOSOMAL_DOMINANT = "HP:0000006"
PHENOTYPIC_ABNORMALITY = "HP:0000118"
NERVOUS_SYSTEM = "HP:0000707"
NERVOUS_PHYSIOLOGY = "HP:0012638"
SEIZURE = "HP:0001250"
SEIZURE_ALT_ID = "HP:0001275"
FOCAL_SEIZURE = "HP:0007359"
GENERALIZED_SEIZURE = "HP:0002197"
HYPOTONIA = "HP:0001252"
ABNORMAL_SPEECH = "HP:0002167"
CNS_MORPHOLOGY = "HP:0002011"
MICROCEPHALY = "HP:0000252"
CORPUS_CALLOSUM_AGENESIS = "HP:0001274"
HEAD_OR_NECK = "HP:0000152"
SKULL_SIZE = "HP:0000240"
METABOLISM = "HP:0001939"
LACTIC_ACIDOSIS = "HP:0003128"
EYE = "HP:0000478"
VISUAL_IMPAIRMENT = "HP:0000505"
BLINDNESS = "HP:0000618"
OBSOLETE_BLINDNESS = "HP:0000057"
UNKNOWN_TERM = "HP:9999999"

GENE_A, GENE_B, GENE_C, GENE_D, GENE_E = "GENE_A", "GENE_B", "GENE_C", "GENE_D", "GENE_E"
DISEASE_A = "OMIM:100001"
DISEASE_B = "OMIM:100002"
DISEASE_C1 = "OMIM:100003"
DISEASE_C2 = "ORPHA:300"
DISEASE_D = "OMIM:100004"
DISEASE_E = "OMIM:100005"

# (id, English label, parents, Spanish label or None)
_TERMS: list[tuple[str, str, tuple[str, ...], str | None]] = [
    (ALL, "All", (), "Todos"),
    (MODE_OF_INHERITANCE, "Mode of inheritance", (ALL,), None),
    (
        AUTOSOMAL_DOMINANT,
        "Autosomal dominant inheritance",
        (MODE_OF_INHERITANCE,),
        "Herencia autosómica dominante",
    ),
    (PHENOTYPIC_ABNORMALITY, "Phenotypic abnormality", (ALL,), "Anomalía fenotípica"),
    (
        NERVOUS_SYSTEM,
        "Abnormality of the nervous system",
        (PHENOTYPIC_ABNORMALITY,),
        "Anomalía del sistema nervioso",
    ),
    (
        NERVOUS_PHYSIOLOGY,
        "Abnormal nervous system physiology",
        (NERVOUS_SYSTEM,),
        "Fisiología anormal del sistema nervioso",
    ),
    (SEIZURE, "Seizure", (NERVOUS_PHYSIOLOGY,), "Convulsiones"),
    (FOCAL_SEIZURE, "Focal-onset seizure", (SEIZURE,), "Convulsiones focales"),
    (GENERALIZED_SEIZURE, "Generalized-onset seizure", (SEIZURE,), None),
    (HYPOTONIA, "Hypotonia", (NERVOUS_PHYSIOLOGY,), "Hipotonía"),
    (ABNORMAL_SPEECH, "Abnormal speech pattern", (NERVOUS_PHYSIOLOGY,), "Patrón del habla anormal"),
    (
        CNS_MORPHOLOGY,
        "Morphological central nervous system abnormality",
        (NERVOUS_SYSTEM,),
        "Anomalía morfológica del sistema nervioso central",
    ),
    (
        HEAD_OR_NECK,
        "Abnormality of head or neck",
        (PHENOTYPIC_ABNORMALITY,),
        "Anomalía de la cabeza o el cuello",
    ),
    (SKULL_SIZE, "Abnormality of skull size", (HEAD_OR_NECK,), "Anomalía del tamaño del cráneo"),
    (MICROCEPHALY, "Microcephaly", (SKULL_SIZE, CNS_MORPHOLOGY), "Microcefalia"),
    (
        CORPUS_CALLOSUM_AGENESIS,
        "Agenesis of corpus callosum",
        (CNS_MORPHOLOGY,),
        "Agenesia del cuerpo calloso",
    ),
    (
        METABOLISM,
        "Abnormality of metabolism/homeostasis",
        (PHENOTYPIC_ABNORMALITY,),
        "Anomalía del metabolismo/homeostasis",
    ),
    (LACTIC_ACIDOSIS, "Lactic acidosis", (METABOLISM,), "Acidosis láctica"),
    (EYE, "Abnormality of the eye", (PHENOTYPIC_ABNORMALITY,), "Anomalía del ojo"),
    (VISUAL_IMPAIRMENT, "Visual impairment", (EYE,), "Discapacidad visual"),
    (BLINDNESS, "Blindness", (VISUAL_IMPAIRMENT,), "Ceguera"),
]

_HPOA_HEADER = (
    "database_id\tdisease_name\tqualifier\thpo_id\treference\tevidence\tonset\tfrequency"
    "\tsex\tmodifier\taspect\tbiocuration"
)

# (disease, name, qualifier, term, onset, frequency, aspect)
_ANNOTATIONS: list[tuple[str, str, str, str, str, str, str]] = [
    (DISEASE_A, "Seizure disorder A", "", SEIZURE, "HP:0003593", "HP:0040280", "P"),
    (DISEASE_A, "Seizure disorder A", "", HYPOTONIA, "", "1/2", "P"),
    (DISEASE_A, "Seizure disorder A", "", BLINDNESS, "", "HP:0040284", "P"),
    (DISEASE_A, "Seizure disorder A", "", AUTOSOMAL_DOMINANT, "", "", "I"),
    (DISEASE_B, "Microcephaly syndrome B", "", MICROCEPHALY, "", "3/4", "P"),
    (DISEASE_B, "Microcephaly syndrome B", "NOT", SEIZURE, "", "", "P"),
    (DISEASE_B, "Microcephaly syndrome B", "", LACTIC_ACIDOSIS, "", "", "P"),
    (DISEASE_C1, "Focal epilepsy C", "", FOCAL_SEIZURE, "", "80%", "P"),
    (DISEASE_C1, "Focal epilepsy C", "", VISUAL_IMPAIRMENT, "", "HP:0040283", "P"),
    (DISEASE_C2, "Retinal disease C", "", BLINDNESS, "", "HP:0040282", "P"),
    (DISEASE_C2, "Retinal disease C", "", VISUAL_IMPAIRMENT, "", "1/2", "P"),
    (DISEASE_C2, "Retinal disease C", "", HYPOTONIA, "", "1/4", "P"),
    (DISEASE_C2, "Retinal disease C", "", HYPOTONIA, "", "3/4", "P"),
    (DISEASE_E, "Visual disorder E", "", OBSOLETE_BLINDNESS, "", "HP:0040281", "P"),
    (DISEASE_E, "Visual disorder E", "", HYPOTONIA, "", "HP:0040285", "P"),
    (DISEASE_E, "Visual disorder E", "", LACTIC_ACIDOSIS, "", "0/5", "P"),
    (DISEASE_E, "Visual disorder E", "", UNKNOWN_TERM, "", "", "P"),
]

# (ncbi id, symbol, association, disease)
_GENES_TO_DISEASE = [
    ("NCBIGene:1", GENE_A, "MENDELIAN", DISEASE_A),
    ("NCBIGene:2", GENE_B, "MENDELIAN", DISEASE_B),
    ("NCBIGene:3", GENE_C, "MENDELIAN", DISEASE_C1),
    ("NCBIGene:3", GENE_C, "UNKNOWN", DISEASE_C2),
    ("NCBIGene:4", GENE_D, "MENDELIAN", DISEASE_D),
    ("NCBIGene:9", "-", "MENDELIAN", DISEASE_D),
]
# (ncbi id, symbol, term, frequency, disease)
_GENES_TO_PHENOTYPE = [
    ("1", GENE_A, SEIZURE, "HP:0040280", DISEASE_A),
    ("5", GENE_E, BLINDNESS, "HP:0040281", DISEASE_E),
    ("9", "-", MICROCEPHALY, "-", DISEASE_D),
]


def _iri(hpo_id: str) -> str:
    return "http://purl.obolibrary.org/obo/" + hpo_id.replace(":", "_")


def _ontology_json() -> str:
    terms = list(_TERMS)
    defined = {hpo_id for hpo_id, *_ in terms}
    for root, label in sorted({**REQUIRES_TESTING_ROOTS, **OBSERVABLE_ROOTS}.items()):
        if root not in defined:
            terms.append((root, label, (PHENOTYPIC_ABNORMALITY,), label))
    nodes: list[dict[str, object]] = [
        {
            "id": _iri(hpo_id),
            "lbl": label,
            "type": "CLASS",
            "meta": {
                "basicPropertyValues": [
                    {
                        "pred": "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId",
                        "val": SEIZURE_ALT_ID,
                    }
                ]
                if hpo_id == SEIZURE
                else []
            },
        }
        for hpo_id, label, _, _ in terms
    ]
    nodes.append(
        {
            "id": _iri(OBSOLETE_BLINDNESS),
            "lbl": "obsolete Loss of sight",
            "type": "CLASS",
            "meta": {
                "deprecated": True,
                "basicPropertyValues": [
                    {"pred": "http://purl.obolibrary.org/obo/IAO_0100001", "val": BLINDNESS}
                ],
            },
        }
    )
    nodes.append({"id": "http://purl.obolibrary.org/obo/RO_0002200", "type": "PROPERTY"})
    edges = [
        {"sub": _iri(hpo_id), "pred": "is_a", "obj": _iri(parent)}
        for hpo_id, _, parents, _ in terms
        for parent in parents
    ]
    graph = {
        "id": "http://purl.obolibrary.org/obo/hp.json",
        "meta": {"version": f"http://purl.obolibrary.org/obo/hp/releases/{HPO_VERSION}/hp.json"},
        "nodes": nodes,
        "edges": edges,
    }
    return json.dumps({"graphs": [graph]}, indent=1)


def _tsv(header: str, rows: list[tuple[str, ...]]) -> str:
    return "\n".join([header, *("\t".join(row) for row in rows)]) + "\n"


def write_tiny_sources(directory: Path) -> dict[str, Path]:
    """Write the five source files into ``directory``; returns them by source name."""
    directory.mkdir(parents=True, exist_ok=True)
    hpoa_rows = [
        (disease, name, qualifier, term, "PMID:1", "PCS", onset, frequency, "", "", aspect, "x")
        for disease, name, qualifier, term, onset, frequency, aspect in _ANNOTATIONS
    ]
    contents = {
        "hp.json": _ontology_json(),
        "phenotype.hpoa": f"#description: tiny\n#version: {HPO_VERSION}\n"
        + _tsv(_HPOA_HEADER, hpoa_rows),
        "genes_to_disease.txt": _tsv(
            "ncbi_gene_id\tgene_symbol\tassociation_type\tdisease_id\tsource",
            [(*row, "tiny") for row in _GENES_TO_DISEASE],
        ),
        "genes_to_phenotype.txt": _tsv(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id",
            [
                (ncbi, gene, term, "name", freq, disease)
                for ncbi, gene, term, freq, disease in _GENES_TO_PHENOTYPE
            ],
        ),
        "hp-es.babelon.tsv": _tsv(
            "source_language\ttranslation_language\tsubject_id\tsource_value\tpredicate_id"
            "\ttranslation_value\ttranslation_status",
            [
                ("en", "es", hpo_id, label, "rdfs:label", spanish, "OFFICIAL")
                for hpo_id, label, _, spanish in _TERMS
                if spanish is not None
            ],
        ),
    }
    for filename, text in contents.items():
        (directory / filename).write_text(text, encoding="utf-8")
    return {source.name: directory / source.filename for source in SOURCES}


def build_tiny_kb(directory: Path) -> BuildSummary:
    """Write the sources under ``directory/sources`` and build ``directory/kb.sqlite``."""
    sources = write_tiny_sources(directory / "sources")
    return build_kb(sources, directory / "kb.sqlite", require_pinned=False)


@pytest.fixture(scope="session")
def tiny_kb(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeBase:
    summary = build_tiny_kb(tmp_path_factory.mktemp("tiny-kb"))
    return KnowledgeBase.open(summary.path)
