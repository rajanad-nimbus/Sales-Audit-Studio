"""Bound request-body reads before buffering untrusted payloads in application memory."""

import json

from fastapi import HTTPException, Request

MAX_API_REQUEST_BODY_BYTES = 26 * 1024 * 1024


def _content_length(values: list[bytes | str]) -> int | None:
    """Parse a single non-negative Content-Length value, rejecting ambiguous framing."""
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("Multiple Content-Length headers are not allowed")
    try:
        value = values[0].decode("ascii") if isinstance(values[0], bytes) else values[0]
        value.encode("ascii")
    except UnicodeError as exc:
        raise ValueError("Content-Length must be a non-negative integer") from exc
    if not value.isdecimal():
        raise ValueError("Content-Length must be a non-negative integer")
    return int(value)


class RequestBodyLimitMiddleware:
    """Enforce a streaming size limit before application handlers parse request bodies."""

    def __init__(self, app, max_bytes: int = MAX_API_REQUEST_BODY_BYTES):
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        try:
            declared_length = _content_length(
                [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
            )
        except ValueError as exc:
            await self._reject(send, status=400, detail=str(exc))
            return
        if declared_length is not None and declared_length > self.max_bytes:
            await self._reject(send)
            return

        received_bytes = 0
        oversized = False
        response_started = False

        async def limited_receive():
            nonlocal received_bytes, oversized
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    oversized = True
                    raise _RequestBodyTooLarge
            return message

        async def guarded_send(message):
            nonlocal response_started
            if oversized:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
        except Exception:
            if not oversized:
                raise
        if oversized and not response_started:
            await self._reject(send)

    async def _reject(
        self,
        send,
        *,
        status: int = 413,
        detail: str | None = None,
    ) -> None:
        body = json.dumps({"detail": detail or f"Request body exceeds the {self.max_bytes:,}-byte limit"}).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]})
        await send({"type": "http.response.body", "body": body})


class _RequestBodyTooLarge(Exception):
    """Internal control flow used to stop application parsing at the configured cap."""


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Read a request body up to ``max_bytes`` and return 413 if it exceeds the limit."""
    try:
        content_length = _content_length(request.headers.getlist("content-length"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if content_length is not None and content_length > max_bytes:
        raise HTTPException(status_code=413, detail=f"Request body exceeds the {max_bytes:,}-byte limit")

    chunks = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > max_bytes:
            raise HTTPException(status_code=413, detail=f"Request body exceeds the {max_bytes:,}-byte limit")
        chunks.append(chunk)
    return b"".join(chunks)
