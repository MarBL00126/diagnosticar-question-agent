from pathlib import Path

from genphenai_repreguntas.orphadata import (
    filter_records_by_candidates,
    load_orphadata_records,
    normalize_orpha_code,
)


def write_sample_xml(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<JDBOR>
  <HPODisorderSetStatus>
    <Disorder>
      <OrphaCode>58</OrphaCode>
      <Name>Alexander disease</Name>
    </Disorder>
    <HPODisorderAssociationList>
      <HPODisorderAssociation>
        <HPO><HPOId>HP:0000256</HPOId><HPOTerm>Macrocephaly</HPOTerm></HPO>
        <HPOFrequency><Name>Frequent (79-30%)</Name></HPOFrequency>
        <DiagnosticCriteria><Name>Diagnostic criterion</Name></DiagnosticCriteria>
      </HPODisorderAssociation>
      <HPODisorderAssociation>
        <HPO><HPOId>HP:0001250</HPOId><HPOTerm>Seizure</HPOTerm></HPO>
        <HPOFrequency><Name>Very frequent (99-80%)</Name></HPOFrequency>
      </HPODisorderAssociation>
    </HPODisorderAssociationList>
  </HPODisorderSetStatus>
  <HPODisorderSetStatus>
    <Disorder>
      <OrphaCode>61</OrphaCode>
      <Name>Alpha-mannosidosis</Name>
    </Disorder>
    <HPODisorderAssociationList>
      <HPODisorderAssociation>
        <HPO><HPOId>HP:0000365</HPOId><HPOTerm>Hearing impairment</HPOTerm></HPO>
        <HPOFrequency><Name>Frequent (79-30%)</Name></HPOFrequency>
      </HPODisorderAssociation>
    </HPODisorderAssociationList>
  </HPODisorderSetStatus>
</JDBOR>
""",
        encoding="utf-8",
    )


def test_load_orphadata_records_filters_and_sorts_terms(tmp_path: Path) -> None:
    xml_path = tmp_path / "sample.xml"
    write_sample_xml(xml_path)

    records = load_orphadata_records(
        xml_path,
        min_hpo_terms=2,
        min_diag_criteria=1,
        max_hpo_per_disease=1,
    )

    assert len(records) == 1
    assert records[0].disease == "Alexander disease"
    assert records[0].orpha_code == "58"
    assert records[0].total_hpo_terms == 2
    assert records[0].n_diag_criteria == 1
    assert records[0].hpo_terms[0].diagnostic_criterion is True


def test_filter_records_by_candidates_accepts_orpha_prefix(tmp_path: Path) -> None:
    xml_path = tmp_path / "sample.xml"
    write_sample_xml(xml_path)
    records = load_orphadata_records(xml_path, min_hpo_terms=1)

    filtered = filter_records_by_candidates(records, ["ORPHA:61"])

    assert [record.orpha_code for record in filtered] == ["61"]
    assert normalize_orpha_code("ORPHA:58") == "58"
