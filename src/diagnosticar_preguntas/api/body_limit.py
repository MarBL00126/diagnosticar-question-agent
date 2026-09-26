"""Refuse request bodies over a size cap before anything parses them.

A round request is a few kilobytes; a body far beyond that is a mistake or an
attack, and parsing it would cost memory in a process that holds the whole
knowledge base. The cap is checked against ``Content-Length`` when present and
against the bytes actually received otherwise (chunked bodies).
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
PAYLOAD_TOO_LARGE = "payload-too-large"


class BodySizeLimit:
    """ASGI middleware: 413 ``{"error": "payload-too-large"}`` for bodies over ``max_bytes``."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or ()).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            await self._reject(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self._max_bytes:
                await self._reject(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        logger.info("rejected a request body over %d bytes", self._max_bytes)
        response = JSONResponse({"error": PAYLOAD_TOO_LARGE}, status_code=413)
        await response(scope, receive, send)
