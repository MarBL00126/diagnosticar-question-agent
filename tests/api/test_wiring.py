from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from diagnosticar_preguntas.agent.round import RoundService
from diagnosticar_preguntas.api import __main__ as entrypoint
from diagnosticar_preguntas.api.app import create_app
from diagnosticar_preguntas.config import get_settings
from diagnosticar_preguntas.kb.store import KnowledgeBase, KnowledgeBaseError
from diagnosticar_preguntas.ranking.model_client import HttpModelClient
from diagnosticar_preguntas.wiring import build_service
from fixtures.http_stubs import make_settings
from fixtures.tiny_kb import build_tiny_kb

# Port 9 (discard) is closed on any sane host, so the probe fails fast.
UNREACHABLE = "http://127.0.0.1:9"


@pytest.fixture(scope="module")
def kb_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_tiny_kb(tmp_path_factory.mktemp("wiring-kb")).path


def test_builds_the_real_components_from_settings(kb_path: Path) -> None:
    service = build_service(make_settings(kb_path=kb_path, model_service_url=UNREACHABLE))

    assert isinstance(service.rounds, RoundService)
    assert isinstance(service.model_service, HttpModelClient)
    assert service.knowledge_base_version == KnowledgeBase.open(kb_path).version


def test_the_app_wires_itself_when_no_service_is_given(kb_path: Path) -> None:
    settings = make_settings(kb_path=kb_path, model_service_url=UNREACHABLE)

    body = TestClient(create_app(settings)).get("/healthz").json()

    assert body["knowledge_base"] == KnowledgeBase.open(kb_path).version
    assert body["model_service"] == "unreachable"


def test_a_missing_knowledge_base_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeBaseError):
        create_app(make_settings(kb_path=tmp_path / "missing.sqlite"))


def test_the_entrypoint_serves_on_the_configured_host_and_port(
    kb_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: dict[str, Any] = {}
    monkeypatch.setenv("PREGUNTAS_KB_PATH", str(kb_path))
    monkeypatch.setenv("PREGUNTAS_LLM_PROVIDER", "template")
    monkeypatch.setenv("PREGUNTAS_HOST", "127.0.0.1")
    monkeypatch.setenv("PREGUNTAS_PORT", "18999")
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda app, **kwargs: served.update(kwargs))
    get_settings.cache_clear()

    try:
        entrypoint.main()
    finally:
        get_settings.cache_clear()

    assert (served["host"], served["port"]) == ("127.0.0.1", 18999)
