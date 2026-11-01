"""Bound request-body reads before buffering untrusted payloads in application memory."""

import json
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException, Request

MAX_API_REQUEST_BODY_BYTES = 26 * 1024 * 1024


@dataclass
class _BodyLimitState:
    received_bytes: int = 0
    oversized: bool = False
    response_started: bool = False
    request_complete: bool = False
    buffered_messages: deque = field(default_factory=deque)


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
        if not self._requires_body_limit(scope):
            await self.app(scope, receive, send)
            return
        declared_length, invalid_detail = self._declared_content_length(scope)
        if invalid_detail:
            await self._reject(send, status=400, detail=invalid_detail)
            return
        if declared_length is not None and declared_length > self.max_bytes:
            await self._reject(send)
            return
        await self._run_with_body_limit(scope, receive, send)

    def _requires_body_limit(self, scope) -> bool:
        return scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}

    @staticmethod
    def _declared_content_length(scope) -> tuple[int | None, str | None]:
        try:
            value = _content_length(
                [
                    header
                    for name, header in scope.get("headers", [])
                    if name.lower() == b"content-length"
                ]
            )
        except ValueError as exc:
            return None, str(exc)
        return value, None

    async def _run_with_body_limit(self, scope, receive, send) -> None:
        state = _BodyLimitState()
        try:
            await self.app(
                scope,
                self._limited_receive(receive, state),
                self._guarded_send(send, receive, state),
            )
        except _RequestBodyTooLarge:
            if state.response_started:
                raise
        except Exception:
            if not state.oversized:
                raise
        if state.oversized and not state.response_started:
            await self._reject(send)

    def _limited_receive(self, receive, state: _BodyLimitState):
        async def limited_receive():
            if state.buffered_messages:
                return state.buffered_messages.popleft()
            message = await receive()
            self._record_request_message(message, state)
            if state.oversized:
                raise _RequestBodyTooLarge
            return message

        return limited_receive

    def _record_request_message(self, message, state: _BodyLimitState) -> None:
        if message["type"] != "http.request":
            return
        state.received_bytes += len(message.get("body", b""))
        state.request_complete = not message.get("more_body", False)
        if state.received_bytes > self.max_bytes:
            state.oversized = True

    def _guarded_send(self, send, receive, state: _BodyLimitState):
        async def guarded_send(message):
            if state.oversized:
                return
            if message["type"] == "http.response.start":
                while not state.request_complete:
                    request_message = await receive()
                    state.buffered_messages.append(request_message)
                    self._record_request_message(request_message, state)
                    if state.oversized:
                        raise _RequestBodyTooLarge
                    if request_message["type"] == "http.disconnect":
                        break
                state.response_started = True
            await send(message)

        return guarded_send

    async def _reject(
        self,
        send,
        *,
        status: int = 413,
        detail: str | None = None,
    ) -> None:
        body = json.dumps(
            {"detail": detail or f"Request body exceeds the {self.max_bytes:,}-byte limit"}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
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
        raise HTTPException(
            status_code=413, detail=f"Request body exceeds the {max_bytes:,}-byte limit"
        )

    chunks = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > max_bytes:
            raise HTTPException(
                status_code=413, detail=f"Request body exceeds the {max_bytes:,}-byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)
