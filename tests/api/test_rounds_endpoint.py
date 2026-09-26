from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest

from diagnosticar_preguntas.agent.round import WARNING_QUESTIONS
from diagnosticar_preguntas.domain import ModelRequestError, ModelUnavailableError
from diagnosticar_preguntas.kb.store import KnowledgeBase
from diagnosticar_preguntas.ranking.model_client import HttpModelClient, NoKnownHpoIdsError
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
    def test_without_a_configured_token_every_round_is_refused(self) -> None:
        rounds = StubRounds()
        api = client(make_settings(allow_unauthenticated=False), stub_service(rounds))

        for headers in ({}, {"X-Internal-Token": "anything"}):
            response = api.post("/v1/rounds", json=round_body(), headers=headers)

            assert response.status_code == 401
            assert response.json() == {"error": "unauthorized"}
        assert rounds.requests == []

    def test_unauthenticated_calls_need_an_explicit_opt_in(self) -> None:
        api = client(make_settings(allow_unauthenticated=True))

        assert api.post("/v1/rounds", json=round_body()).status_code == 200

    def test_a_configured_token_is_required_even_with_the_opt_in(self) -> None:
        api = client(make_settings(service_token=TOKEN, allow_unauthenticated=True))

        assert api.post("/v1/rounds", json=round_body()).status_code == 401

    def test_logs_an_error_at_startup_when_rounds_will_be_refused(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client(make_settings(allow_unauthenticated=False))

        assert "refuses every call" in caplog.text

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
        assert "accepts unauthenticated calls" in caplog.text

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

    @pytest.mark.parametrize(
        "overrides",
        [
            {"absent_hpo_ids": [PRESENT[0]]},
            {"round": 2, "answers": [{"hpo_id": PRESENT[0], "answer": "si"}]},
            {"round": 2, "answers": [{"hpo_id": ABSENT[0], "answer": "no"}]},
            {
                "round": 2,
                "answers": [
                    {
                        "hpo_id": "HP:0001738",
                        "answer": "si",
                        "reading": {"effective_answer": "no"},
                    }
                ],
            },
        ],
        ids=["present-and-absent", "answer-on-present", "answer-on-absent", "reading-flips"],
    )
    def test_inconsistent_findings_answers_and_readings_are_422(
        self, overrides: dict[str, object]
    ) -> None:
        rounds = StubRounds()

        response = client(service=stub_service(rounds)).post(
            "/v1/rounds", json=round_body(**overrides)
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid-request"
        assert rounds.requests == []
        for value in CLINICAL_STRINGS:
            assert value not in response.text

    def test_a_reading_may_downgrade_to_no_se(self) -> None:
        answers = [
            {
                "hpo_id": "HP:0001738",
                "answer": "si",
                "note": NOTE,
                "reading": {"effective_answer": "no_se", "note_summary": "Duda."},
            }
        ]

        response = client().post("/v1/rounds", json=round_body(round=2, answers=answers))

        assert response.status_code == 200

    def test_a_body_over_64_kib_is_413_before_parsing(self) -> None:
        rounds = StubRounds()
        body = json.dumps(round_body()).encode() + b" " * (64 * 1024)

        response = client(service=stub_service(rounds)).post(
            "/v1/rounds", content=body, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 413
        assert response.json() == {"error": "payload-too-large"}
        assert rounds.requests == []

    def test_a_chunked_body_over_the_cap_is_413_too(self) -> None:
        def chunks() -> Iterator[bytes]:
            for _ in range(80):
                yield b" " * 1024

        response = client().post(
            "/v1/rounds", content=chunks(), headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 413

    def test_a_body_under_the_cap_is_parsed_normally(self) -> None:
        body = json.dumps(round_body()).encode() + b" " * (60 * 1024)

        response = client().post(
            "/v1/rounds", content=body, headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 200

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
            (ModelRequestError("model rejected the request with 400"), 422, "invalid-request"),
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

    def test_a_rejected_model_request_does_not_invite_a_retry(self) -> None:
        api = client(service=stub_service(StubRounds(error=ModelRequestError("400"))))

        response = api.post("/v1/rounds", json=round_body())

        assert "Retry-After" not in response.headers

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
        assert body["mode"] == "paciente"
        for question in body["questions"]:
            assert question["label"] == tiny_kb.label_es(question["hpo_id"])
            assert question["genes_if_yes"] is None
            assert question["genes_if_no"] is None
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


class TestModelRejection:
    """A 4xx from the model service, other than no-known-hpo-ids, over the real HTTP client."""

    def pipeline(self, kb: KnowledgeBase, status: int) -> Service:
        def handler(request: httpx.Request) -> httpx.Response:
            # Services commonly echo the offending input in a validation error.
            echoed = json.loads(request.content)["hpo_ids"]
            return httpx.Response(status, json={"detail": [{"msg": "bad", "input": echoed}]})

        model = HttpModelClient("http://model.test", transport=httpx.MockTransport(handler))
        return Service(
            rounds=real_rounds(kb, model),
            knowledge_base_version=kb.version,
            model_service=StubProbe(),
        )

    def test_is_422_invalid_request_and_logs_no_clinical_data(
        self, tiny_kb: KnowledgeBase, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        body = round_body(
            round=2,
            present_hpo_ids=[MICROCEPHALY],
            absent_hpo_ids=[],
            answers=[{"hpo_id": SEIZURE, "answer": "si", "note": NOTE}],
        )

        response = client(service=self.pipeline(tiny_kb, 422)).post("/v1/rounds", json=body)

        assert response.status_code == 422
        assert response.json() == {"error": "invalid-request"}
        assert "error=invalid-request" in caplog.text
        for value in (MICROCEPHALY, SEIZURE, NOTE):
            assert value not in caplog.text
            assert value not in response.text

    def test_a_5xx_is_still_model_unavailable(self, tiny_kb: KnowledgeBase) -> None:
        body = round_body(present_hpo_ids=[MICROCEPHALY], absent_hpo_ids=[])

        response = client(service=self.pipeline(tiny_kb, 500)).post("/v1/rounds", json=body)

        assert response.status_code == 503
        assert response.json() == {"error": "model-unavailable"}


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

    def test_each_round_is_logged_once(
        self, tiny_kb: KnowledgeBase, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        body = round_body(present_hpo_ids=[MICROCEPHALY], absent_hpo_ids=[])

        client(service=pipeline(tiny_kb)).post("/v1/rounds", json=body)

        assert sum("round=" in r.getMessage() for r in caplog.records) == 1

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
