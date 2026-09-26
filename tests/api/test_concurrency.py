from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest

from diagnosticar_preguntas.api.limiter import RoundLimiter, SaturatedError
from fixtures.http_stubs import StubRounds, client, make_settings, round_body, stub_service


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


class TestRoundLimiter:
    def test_admits_up_to_running_plus_queued_and_rejects_the_rest(self) -> None:
        limiter = RoundLimiter(concurrent=1, queued=1, wait_s=5)
        release = threading.Event()

        def hold() -> None:
            with limiter.slot():
                release.wait(5)

        with ThreadPoolExecutor(2) as pool:
            running = [pool.submit(hold), pool.submit(hold)]
            wait_until(lambda: limiter.admitted == 2)

            with pytest.raises(SaturatedError), limiter.slot():
                pass

            release.set()
            for future in running:
                future.result(timeout=5)
        assert limiter.admitted == 0

    def test_a_queued_round_gives_up_after_the_wait(self) -> None:
        limiter = RoundLimiter(concurrent=1, queued=1, wait_s=0.05)

        with limiter.slot():
            started = time.monotonic()
            with pytest.raises(SaturatedError), limiter.slot():
                pass

        assert time.monotonic() - started < 2
        assert limiter.admitted == 0

    def test_a_failing_round_frees_its_slot(self) -> None:
        limiter = RoundLimiter(concurrent=1, queued=0, wait_s=0.05)

        with pytest.raises(RuntimeError), limiter.slot():
            raise RuntimeError("boom")

        with limiter.slot():
            assert limiter.admitted == 1

    @pytest.mark.parametrize(("concurrent", "queued"), [(0, 1), (1, -1)])
    def test_rejects_impossible_limits(self, concurrent: int, queued: int) -> None:
        with pytest.raises(ValueError):
            RoundLimiter(concurrent=concurrent, queued=queued, wait_s=1)


def test_a_saturated_service_answers_503_with_retry_after() -> None:
    rounds = StubRounds(hold=True)
    settings = make_settings(max_concurrent_rounds=1, max_queued_rounds=0)
    api = client(settings, stub_service(rounds))

    with ThreadPoolExecutor(1) as pool:
        in_flight = pool.submit(api.post, "/v1/rounds", json=round_body())
        assert rounds.entered.acquire(timeout=5)

        rejected = api.post("/v1/rounds", json=round_body())

        rounds.release.set()
        assert in_flight.result(timeout=10).status_code == 200

    assert rejected.status_code == 503
    assert rejected.json() == {"error": "busy"}
    assert int(rejected.headers["Retry-After"]) > 0
    assert len(rounds.requests) == 1
