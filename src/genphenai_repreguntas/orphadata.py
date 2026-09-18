from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FREQUENCY_RANK = {
    "Obligate (100%)": 5,
    "Very frequent (99-80%)": 4,
    "Frequent (79-30%)": 3,
    "Occasional (29-5%)": 2,
    "Very rare (<4-1%)": 1,
    "Excluded (0%)": 0,
}


@dataclass(frozen=True)
class HpoTerm:
    hpo_id: str
    term: str
    frequency: str
    diagnostic_criterion: bool = False
    rank: int = -1


@dataclass(frozen=True)
class DiseaseRecord:
    disease: str
    orpha_code: str
    hpo_terms: tuple[HpoTerm, ...]
    total_hpo_terms: int
    n_diag_criteria: int = 0


def child_text(element: ET.Element | None, tag_name: str) -> str:
    if element is None:
        return ""
    child = element.find(tag_name)
    return child.text.strip() if child is not None and child.text else ""


def nested_name(element: ET.Element, path: str) -> str:
    found = element.find(path)
    if found is None:
        return ""
    return child_text(found, "Name")


def has_diagnostic_criterion(assoc: ET.Element) -> bool:
    diag = assoc.find("DiagnosticCriteria")
    return (
        diag is not None
        and (bool(diag.attrib) or bool(list(diag)) or bool((diag.text or "").strip()))
    )


def normalize_orpha_code(value: str | int) -> str:
    text = str(value).strip()
    return text.split(":", 1)[1] if text.upper().startswith("ORPHA:") else text


def parse_orphadata_records(xml_path: Path) -> Iterable[DiseaseRecord]:
    for _event, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag != "HPODisorderSetStatus":
            continue

        disorder = elem.find("Disorder")
        if disorder is None:
            elem.clear()
            continue

        disease = child_text(disorder, "Name")
        orpha_code = child_text(disorder, "OrphaCode")
        terms: list[HpoTerm] = []

        for assoc in elem.findall(".//HPODisorderAssociation"):
            hpo = assoc.find("HPO")
            hpo_id = child_text(hpo, "HPOId")
            term = child_text(hpo, "HPOTerm")
            if not hpo_id or not term:
                continue
            frequency = nested_name(assoc, "HPOFrequency") or "Unknown frequency"
            diagnostic = has_diagnostic_criterion(assoc)
            terms.append(
                HpoTerm(
                    hpo_id=hpo_id,
                    term=term,
                    frequency=frequency,
                    diagnostic_criterion=diagnostic,
                    rank=FREQUENCY_RANK.get(frequency, -1),
                )
            )

        if disease and orpha_code:
            yield DiseaseRecord(
                disease=disease,
                orpha_code=orpha_code,
                hpo_terms=tuple(terms),
                total_hpo_terms=len(terms),
                n_diag_criteria=sum(1 for term in terms if term.diagnostic_criterion),
            )
        elem.clear()


def load_orphadata_records(
    xml_path: Path,
    *,
    min_hpo_terms: int = 8,
    min_diag_criteria: int = 0,
    max_diseases: int | None = None,
    max_hpo_per_disease: int = 20,
) -> list[DiseaseRecord]:
    records = [
        record
        for record in parse_orphadata_records(xml_path)
        if record.total_hpo_terms >= min_hpo_terms
        and record.n_diag_criteria >= min_diag_criteria
    ]
    records.sort(key=lambda item: item.total_hpo_terms, reverse=True)
    if max_diseases is not None:
        records = records[:max_diseases]
    return [
        DiseaseRecord(
            disease=record.disease,
            orpha_code=record.orpha_code,
            hpo_terms=tuple(
                sorted(
                    record.hpo_terms,
                    key=lambda term: (term.diagnostic_criterion, term.rank),
                    reverse=True,
                )[:max_hpo_per_disease]
            ),
            total_hpo_terms=record.total_hpo_terms,
            n_diag_criteria=record.n_diag_criteria,
        )
        for record in records
    ]


def filter_records_by_candidates(
    records: Iterable[DiseaseRecord],
    candidates: Iterable[str | int],
) -> list[DiseaseRecord]:
    wanted = {normalize_orpha_code(candidate).lower() for candidate in candidates if str(candidate).strip()}
    if not wanted:
        return list(records)
    return [
        record
        for record in records
        if normalize_orpha_code(record.orpha_code).lower() in wanted
        or record.disease.lower() in wanted
    ]
