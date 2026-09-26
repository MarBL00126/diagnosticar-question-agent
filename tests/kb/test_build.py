from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from tests.fixtures.tiny_kb import HPO_VERSION, build_tiny_kb, write_tiny_sources

from diagnosticar_preguntas.kb.build import (
    SourceError,
    build_kb,
    download_sources,
    locate_sources,
    main,
)
from diagnosticar_preguntas.kb.sources import SOURCES, Source, sha256_file


def test_same_sources_build_byte_identical_files(tmp_path: Path) -> None:
    first = build_tiny_kb(tmp_path / "first")
    second = build_tiny_kb(tmp_path / "second")

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()


def test_digest_file_matches_the_published_database(tmp_path: Path) -> None:
    summary = build_tiny_kb(tmp_path)

    digest_file = summary.path.with_name("kb.sqlite.sha256").read_text(encoding="utf-8")

    assert digest_file == f"{sha256_file(summary.path)}  kb.sqlite\n"


def test_changing_an_input_changes_the_digest(tmp_path: Path) -> None:
    original = build_tiny_kb(tmp_path / "original")
    sources = write_tiny_sources(tmp_path / "edited")
    labels = sources["spanish_labels"]
    labels.write_text(labels.read_text().replace("Ceguera", "Ceguera total"), encoding="utf-8")

    edited = build_kb(sources, tmp_path / "edited.sqlite", require_pinned=False)

    assert edited.sha256 != original.sha256


def test_meta_records_versions_and_source_digests(tmp_path: Path) -> None:
    summary = build_tiny_kb(tmp_path)
    sources = write_tiny_sources(tmp_path / "again")

    with sqlite3.connect(summary.path) as connection:
        meta = dict(connection.execute("SELECT key, value FROM meta"))

    assert meta["kb_schema_version"] == "1"
    assert meta["hpo_version"] == HPO_VERSION
    for source in SOURCES:
        assert meta[f"source.{source.name}.sha256"] == sha256_file(sources[source.name])
        assert meta[f"source.{source.name}.url"] == source.url
        assert meta[f"source.{source.name}.pinned"] == "false"


def test_build_counts_every_table(tmp_path: Path) -> None:
    summary = build_tiny_kb(tmp_path)

    assert summary.counts["gene"] == 5
    assert summary.counts["negated_annotations"] == 2
    assert summary.counts["dropped_annotations"] == 1
    assert summary.seconds >= 0


def test_unpinned_sources_are_rejected_by_default(tmp_path: Path) -> None:
    sources = write_tiny_sources(tmp_path)

    with pytest.raises(SourceError, match="pinned sha256"):
        build_kb(sources, tmp_path / "kb.sqlite")


def test_missing_requires_testing_root_fails_the_build(tmp_path: Path) -> None:
    sources = write_tiny_sources(tmp_path)

    with pytest.raises(SourceError, match="HP:0000404"):
        build_kb(
            sources,
            tmp_path / "kb.sqlite",
            require_pinned=False,
            requires_testing_roots=["HP:0000404"],
        )


def test_locate_sources_takes_the_first_directory_that_has_each_file(tmp_path: Path) -> None:
    first = write_tiny_sources(tmp_path / "first")
    first["ontology"].unlink()
    second = write_tiny_sources(tmp_path / "second")

    located = locate_sources([tmp_path / "first", tmp_path / "second"])

    assert located["ontology"] == second["ontology"]
    assert located["disease_annotations"] == first["disease_annotations"]


def test_locate_sources_names_the_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match=r"hp\.json"):
        locate_sources([tmp_path])


def _serving(files: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = files.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_verifies_the_pinned_digest(tmp_path: Path) -> None:
    payload = b"hello\n"
    (tmp_path / "probe").write_bytes(payload)
    source = Source(
        "probe", "probe.txt", "https://example.test/probe.txt", sha256_file(tmp_path / "probe")
    )

    located = download_sources(tmp_path / "out", [source], client=_serving({source.url: payload}))

    assert located["probe"].read_bytes() == payload


def test_download_with_a_wrong_digest_leaves_no_file(tmp_path: Path) -> None:
    source = Source("probe", "probe.txt", "https://example.test/probe.txt", "0" * 64)

    with pytest.raises(SourceError, match="sha256"):
        download_sources(tmp_path, [source], client=_serving({source.url: b"tampered"}))

    assert list(tmp_path.iterdir()) == []


def test_cli_builds_from_a_source_directory_and_prints_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_tiny_sources(tmp_path / "sources")
    out = tmp_path / "kb.sqlite"

    exit_code = main(
        ["--source-dir", str(tmp_path / "sources"), "--out", str(out), "--allow-unpinned"]
    )

    assert exit_code == 0
    assert out.is_file()
    assert "sha256" in capsys.readouterr().out


def test_cli_refuses_unpinned_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_tiny_sources(tmp_path / "sources")

    exit_code = main(
        ["--source-dir", str(tmp_path / "sources"), "--out", str(tmp_path / "kb.sqlite")]
    )

    assert exit_code == 1
    assert "pinned sha256" in capsys.readouterr().err
    assert not (tmp_path / "kb.sqlite").exists()
