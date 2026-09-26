"""Pinned inputs of the knowledge base.

Every file is identified by a URL that never changes content (a release asset
or a commit-pinned raw file) and by its sha256, so a build is reproducible
from the manifest alone. The HPO files are the v2026-02-16 release, the same
one the diagnostic.ar model was trained on. The Spanish labels are the
official translations of the HPO Internationalisation Effort (the file the
platform shows to families), pinned to the hpo-translations commit published
the same day as that release.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

HPO_VERSION = "2026-02-16"
SPANISH_LABELS_COMMIT = "69f7f96fba5ae4c1b999028991510088f3480487"

_HPO_RELEASE = (
    "https://github.com/obophenotype/human-phenotype-ontology/releases/download/v" + HPO_VERSION
)
_TRANSLATIONS = (
    "https://raw.githubusercontent.com/obophenotype/hpo-translations/" + SPANISH_LABELS_COMMIT
)


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    filename: str
    url: str
    sha256: str


ONTOLOGY = Source(
    name="ontology",
    filename="hp.json",
    url=f"{_HPO_RELEASE}/hp.json",
    sha256="cb3b756ac11d1308372136bf4f7f8db2ad1c51f64a795e09400e5ffb2a273243",
)
DISEASE_ANNOTATIONS = Source(
    name="disease_annotations",
    filename="phenotype.hpoa",
    url=f"{_HPO_RELEASE}/phenotype.hpoa",
    sha256="5d7aedee5f6218e9952fceba5f883df2cc4a0c1938e55d8d2616c15c517964fb",
)
GENES_TO_DISEASE = Source(
    name="genes_to_disease",
    filename="genes_to_disease.txt",
    url=f"{_HPO_RELEASE}/genes_to_disease.txt",
    sha256="364429870dd8326c5d293eeec31c3f1c89351c8d63a9d66c294b171966fa8b60",
)
GENES_TO_PHENOTYPE = Source(
    name="genes_to_phenotype",
    filename="genes_to_phenotype.txt",
    url=f"{_HPO_RELEASE}/genes_to_phenotype.txt",
    sha256="25d3e5a40203cbb4cc027747c70fcb5431bcfb26283479608a97f3d810285c7d",
)
SPANISH_LABELS = Source(
    name="spanish_labels",
    filename="hp-es.babelon.tsv",
    url=f"{_TRANSLATIONS}/babelon/hp-es.babelon.tsv",
    sha256="00972e33775a0340ad7b45620f0ff1d2794766c45beb937a165ee86e6f16ed9a",
)

SOURCES: tuple[Source, ...] = (
    ONTOLOGY,
    DISEASE_ANNOTATIONS,
    GENES_TO_DISEASE,
    GENES_TO_PHENOTYPE,
    SPANISH_LABELS,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
