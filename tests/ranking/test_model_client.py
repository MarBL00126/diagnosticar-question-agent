from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from diagnosticar_preguntas.domain import ModelRequestError, ModelUnavailableError
from diagnosticar_preguntas.ranking.model_client import HttpModelClient, NoKnownHpoIdsError
from fixtures.fake_model import FakeModelClient

RESPONSE = {
    "results": [
        {"gene_symbol": "CFTR", "score": 0.41, "gene_id": "CFTR", "rank": 1},
        {"gene_symbol": "DNAH5", "score": 0.2},
        {"gene_symbol": "SCNN1A", "score": 0.09},
    ],
    "specialties": {"top_specialty": "Pulmonology"},
    "confidence": 0.78,
    "unknown_hpo_ids": ["HP:0000002"],
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpModelClient:
    return HttpModelClient("http://model.test", transport=httpx.MockTransport(handler))


def test_rank_posts_the_terms_and_parses_the_ranking() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=RESPONSE)

    with _client(handler) as client:
        ranking = client.rank(["HP:0002205", "HP:0000002"], top_k=50)

    assert seen[0].method == "POST"
    assert seen[0].url == "http://model.test/rank-genes"
    assert json.loads(seen[0].content) == {"hpo_ids": ["HP:0002205", "HP:0000002"], "top_k": 50}
    assert list(ranking.scores) == ["CFTR", "DNAH5", "SCNN1A"]
    assert ranking.scores["CFTR"] == pytest.approx(0.41)
    assert ranking.specialties == {"top_specialty": "Pulmonology"}
    assert ranking.confidence == pytest.approx(0.78)
    assert ranking.unknown_hpo_ids == ("HP:0000002",)
    assert ranking.query_hpo_ids == ("HP:0002205", "HP:0000002")


def test_optional_fields_may_be_null() -> None:
    body = {"results": [], "specialties": None, "confidence": None, "unknown_hpo_ids": []}

    ranking = _client(lambda _: httpx.Response(200, json=body)).rank(["HP:0002205"], 5)

    assert ranking.scores == {}
    assert ranking.specialties is None
    assert ranking.confidence is None


def test_no_known_hpo_ids_raises_a_dedicated_error() -> None:
    body = {"detail": {"error": "no-known-hpo-ids", "unknown_hpo_ids": ["HP:0000002"]}}
    client = _client(lambda _: httpx.Response(422, json=body))

    with pytest.raises(NoKnownHpoIdsError) as caught:
        client.rank(["HP:0000002"], 50)

    assert caught.value.hpo_ids == ("HP:0000002",)


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_other_client_errors_are_a_rejected_request_not_an_outage(status: int) -> None:
    body = {"detail": [{"msg": "bad", "input": ["HP:0002205"]}]}
    client = _client(lambda _: httpx.Response(status, json=body))

    with pytest.raises(ModelRequestError, match=str(status)) as caught:
        client.rank(["HP:0002205"], 50)

    assert "HP:0002205" not in str(caught.value)
    assert not isinstance(caught.value, ModelUnavailableError)


def test_server_error_raises_model_unavailable_without_the_body() -> None:
    client = _client(lambda _: httpx.Response(503, text="warming up with HP:0002205"))

    with pytest.raises(ModelUnavailableError, match="503") as caught:
        client.rank(["HP:0002205"], 50)

    assert "warming" not in str(caught.value)
    assert "HP:0002205" not in str(caught.value)


def test_no_known_hpo_ids_does_not_quote_the_terms_in_its_message() -> None:
    body = {"detail": {"error": "no-known-hpo-ids"}}

    with pytest.raises(NoKnownHpoIdsError) as caught:
        _client(lambda _: httpx.Response(422, json=body)).rank(["HP:0000002"], 50)

    assert "HP:0000002" not in str(caught.value)


def test_timeout_raises_model_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelUnavailableError, match="timed out"):
        _client(handler).rank(["HP:0002205"], 50)


def test_connection_error_raises_model_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ModelUnavailableError, match="unreachable"):
        _client(handler).rank(["HP:0002205"], 50)


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        {"results": [{"gene_symbol": "CFTR"}]},
        {"results": [{"gene_symbol": 7, "score": 0.1}]},
        {"genes": []},
    ],
)
def test_malformed_response_raises_model_unavailable(body: object) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(200, text=body)
        return httpx.Response(200, json=body)

    with pytest.raises(ModelUnavailableError, match="unusable") as caught:
        _client(handler).rank(["HP:0002205"], 50)

    assert caught.value.__cause__ is None


def test_fake_model_records_calls_and_reports_unknown_terms() -> None:
    fake = FakeModelClient({"A": 0.7, "B": 0.2, "C": 0.1}, known_hpo_ids={"HP:0000001"})

    ranking = fake.rank(["HP:0000001", "HP:0000002"], top_k=2)

    assert fake.calls == [(("HP:0000001", "HP:0000002"), 2)]
    assert dict(ranking.scores) == {"A": 0.7, "B": 0.2}
    assert ranking.unknown_hpo_ids == ("HP:0000002",)
    assert ranking.query_hpo_ids == ("HP:0000001", "HP:0000002")


def test_fake_model_fails_like_the_real_service() -> None:
    with pytest.raises(NoKnownHpoIdsError):
        FakeModelClient({}, known_hpo_ids=()).rank(["HP:0000002"], 5)
    with pytest.raises(ModelUnavailableError):
        FakeModelClient({}, unavailable=True).rank(["HP:0000002"], 5)


@pytest.mark.parametrize(("status", "healthy"), [(200, True), (503, False)])
def test_health_probe_reads_the_status_of_healthz(status: int, healthy: bool) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"status": "ok"})

    assert _client(handler).is_healthy(timeout=1.0) is healthy
    assert (seen[0].method, seen[0].url.path) == ("GET", "/healthz")


def test_health_probe_is_false_when_the_model_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert _client(handler).is_healthy(timeout=1.0) is False
