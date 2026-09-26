from __future__ import annotations

import pytest

from diagnosticar_preguntas.agent.round import WARNING_QUESTIONS
from diagnosticar_preguntas.domain import ModelUnavailableError
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.model_client import NoKnownHpoIdsError
from diagnosticar_preguntas.wiring import Service
from fixtures.fake_model import FakeModelClient
from fixtures.http_stubs import (
    ABSENT,
    NOTE,
    PRESENT,
    TOKEN,
    StubProbe,
    StubRounds,
    client,
    make_settings,
    real_rounds,
    round_body,
    stub_service,
)
from fixtures.tiny_kb import GENE_A, GENE_B, GENE_C, HYPOTONIA, MICROCEPHALY, SEIZURE

CLINICAL_STRINGS = (*PRESENT, *ABSENT, NOTE)


def pipeline(kb: KnowledgeBase, known_hpo_ids: tuple[str, ...] | None = None) -> Service:
    model = FakeModelClient({GENE_A: 0.4, GENE_B: 0.35, GENE_C: 0.25}, known_hpo_ids=known_hpo_ids)
    return Service(
        rounds=real_rounds(kb, model),
        knowledge_base_version=kb.version,
        model_service=StubProbe(),
    )


def with_note() -> dict[str, object]:
    return round_body(round=2, answers=[{"hpo_id": "HP:0001738", "answer": "si", "note": NOTE}])


class TestAuthentication:
    def test_without_a_configured_token_calls_are_accepted(self) -> None:
        response = client().post("/v1/rounds", json=round_body())

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "headers", [{}, {"X-Internal-Token": "wrong"}, {"X-Internal-Token": ""}]
    )
    def test_a_missing_or_wrong_token_is_rejected(self, headers: dict[str, str]) -> None:
        rounds = StubRounds()
        api = client(make_settings(service_token=TOKEN), stub_service(rounds))

        response = api.post("/v1/rounds", json=round_body(), headers=headers)

        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
        assert rounds.requests == []

    def test_the_right_token_is_accepted(self) -> None:
        api = client(make_settings(service_token=TOKEN))

        response = api.post("/v1/rounds", json=round_body(), headers={"X-Internal-Token": TOKEN})

        assert response.status_code == 200

    def test_authentication_is_checked_before_the_body(self) -> None:
        api = client(make_settings(service_token=TOKEN))

        response = api.post("/v1/rounds", json={"mode": "paciente"})

        assert response.status_code == 401

    def test_health_needs_no_token(self) -> None:
        api = client(make_settings(service_token=TOKEN))

        assert api.get("/healthz").status_code == 200

    def test_warns_at_startup_when_no_token_is_set(self, caplog: pytest.LogCaptureFixture) -> None:
        client(make_settings())

        assert "PREGUNTAS_SERVICE_TOKEN is not set" in caplog.text

    def test_does_not_warn_when_a_token_is_set(self, caplog: pytest.LogCaptureFixture) -> None:
        client(make_settings(service_token=TOKEN))

        assert "PREGUNTAS_SERVICE_TOKEN" not in caplog.text
        assert TOKEN not in caplog.text


class TestValidation:
    def test_a_body_that_breaks_the_contract_is_422_with_locations(self) -> None:
        response = client().post("/v1/rounds", json=round_body(present_hpo_ids=[]))

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "invalid-request"
        assert body["detail"][0]["loc"] == ["body", "present_hpo_ids"]

    def test_rejected_values_are_never_echoed(self) -> None:
        secret_note = "SECRETO " + "x" * 300
        body = round_body(
            round=2,
            present_hpo_ids=["HP:12345"],
            answers=[{"hpo_id": "HP:0001738", "answer": "tal vez", "note": secret_note}],
        )

        response = client().post("/v1/rounds", json=body)

        assert response.status_code == 422
        assert len(response.json()["detail"]) == 3
        for value in ("SECRETO", "HP:12345", "tal vez"):
            assert value not in response.text

    def test_malformed_json_is_422(self) -> None:
        response = client().post(
            "/v1/rounds", content=b'{"mode": ', headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid-request"


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("error", "status", "code"),
        [
            (NoKnownHpoIdsError(PRESENT), 422, "no-known-hpo-ids"),
            (ModelUnavailableError("model answered 500"), 503, "model-unavailable"),
            (RuntimeError(f"unexpected, with {NOTE}"), 500, "internal-error"),
        ],
    )
    def test_maps_failures_to_an_opaque_error_code(
        self, error: Exception, status: int, code: str
    ) -> None:
        api = client(service=stub_service(StubRounds(error=error)))

        response = api.post("/v1/rounds", json=round_body())

        assert response.status_code == status
        assert response.json() == {"error": code}
        for value in CLINICAL_STRINGS:
            assert value not in response.text

    def test_model_unavailable_tells_the_caller_when_to_retry(self) -> None:
        api = client(service=stub_service(StubRounds(error=ModelUnavailableError("down"))))

        response = api.post("/v1/rounds", json=round_body())

        assert int(response.headers["Retry-After"]) > 0


class TestRealPipeline:
    """The HTTP layer over the real re-ranker and selector on the tiny knowledge base."""

    def test_round_one_asks_labelled_questions(self, tiny_kb: KnowledgeBase) -> None:
        api = client(service=pipeline(tiny_kb))

        response = api.post(
            "/v1/rounds", json=round_body(present_hpo_ids=[MICROCEPHALY], absent_hpo_ids=[])
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "questions"
        assert body["questions"]
        for question in body["questions"]:
            assert question["label"] == tiny_kb.label_es(question["hpo_id"])
            assert question["separates"] is None
        assert body["versions"]["knowledge_base"] == tiny_kb.version

    def test_a_finished_round_returns_a_summary(self, tiny_kb: KnowledgeBase) -> None:
        api = client(service=pipeline(tiny_kb))
        body = round_body(
            round=2,
            present_hpo_ids=[MICROCEPHALY],
            absent_hpo_ids=[],
            answers=[{"hpo_id": SEIZURE, "answer": "no"}, {"hpo_id": HYPOTONIA, "answer": "si"}],
            finish=True,
        )

        response = api.post("/v1/rounds", json=body)

        assert response.status_code == 200
        assert response.json()["finish_reason"] == "user_finished"
        assert response.json()["summary"]

    def test_terms_the_model_does_not_know_are_422(self, tiny_kb: KnowledgeBase) -> None:
        api = client(service=pipeline(tiny_kb, known_hpo_ids=()))

        response = api.post("/v1/rounds", json=round_body(present_hpo_ids=[MICROCEPHALY]))

        assert response.status_code == 422
        assert response.json() == {"error": "no-known-hpo-ids"}


class TestLogging:
    def test_a_round_logs_metadata_and_timing(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level("DEBUG")
        api = client(service=stub_service(StubRounds(warnings=(WARNING_QUESTIONS,))))

        api.post("/v1/rounds", json=with_note())

        line = next(r.getMessage() for r in caplog.records if "elapsed_ms=" in r.getMessage())
        for field in (
            "round=2",
            "mode=paciente",
            "finish_reason=user_finished",
            "llm_fallback=True",
        ):
            assert field in line

    @pytest.mark.parametrize(
        "error",
        [
            None,
            NoKnownHpoIdsError(PRESENT),
            ModelUnavailableError("down"),
            ValueError(f"cannot parse {NOTE} for {PRESENT}"),
        ],
    )
    def test_logs_never_carry_clinical_data(
        self, error: Exception | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        api = client(service=stub_service(StubRounds(error=error)))

        api.post("/v1/rounds", json=with_note())
        api.post("/v1/rounds", json={**with_note(), "mode": "otro"})

        assert caplog.records
        for value in (*CLINICAL_STRINGS, "HP:0001738"):
            assert value not in caplog.text

    def test_an_unexpected_error_logs_its_type_and_traceback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        api = client(service=stub_service(StubRounds(error=ValueError(NOTE))))

        api.post("/v1/rounds", json=round_body())

        assert "ValueError" in caplog.text
        assert "in run" in caplog.text
        assert NOTE not in caplog.text

    def test_the_real_pipeline_logs_no_clinical_data(
        self, tiny_kb: KnowledgeBase, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        body = round_body(
            round=2,
            present_hpo_ids=[MICROCEPHALY],
            absent_hpo_ids=[],
            answers=[{"hpo_id": SEIZURE, "answer": "si", "note": NOTE}],
        )

        response = client(service=pipeline(tiny_kb)).post("/v1/rounds", json=body)

        assert response.status_code == 200
        for value in (MICROCEPHALY, SEIZURE, NOTE, "Microcefalia", "Convulsiones"):
            assert value not in caplog.text
