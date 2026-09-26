"""Cap on rounds in flight, with a short bounded queue in front of it.

Rounds run in the server's worker threads, so the limiter is thread-based. A
request that finds the queue full, or waits longer than ``wait_s`` for a
slot, is turned away at once: the caller (the platform backend) gives up
after about 90 s, and work it has abandoned would still spend LLM quota.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class SaturatedError(RuntimeError):
    """No slot is free and the queue is full, or the wait for a slot timed out."""


class RoundLimiter:
    def __init__(self, *, concurrent: int, queued: int, wait_s: float) -> None:
        if concurrent < 1:
            raise ValueError("concurrent must be at least 1")
        if queued < 0:
            raise ValueError("queued cannot be negative")
        self._slots = threading.BoundedSemaphore(concurrent)
        self._capacity = concurrent + queued
        self._wait_s = wait_s
        self._admitted = 0
        self._lock = threading.Lock()

    @property
    def admitted(self) -> int:
        """Rounds running or waiting for a slot."""
        with self._lock:
            return self._admitted

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold a slot for the duration of the block. Raises ``SaturatedError``."""
        with self._lock:
            if self._admitted >= self._capacity:
                raise SaturatedError("round queue is full")
            self._admitted += 1
        try:
            if not self._slots.acquire(timeout=self._wait_s):
                raise SaturatedError(f"no round slot within {self._wait_s:.0f}s")
            try:
                yield
            finally:
                self._slots.release()
        finally:
            with self._lock:
                self._admitted -= 1
