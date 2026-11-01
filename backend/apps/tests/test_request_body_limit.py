import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.requests import Request as StarletteRequest

from apps.request_body import RequestBodyLimitMiddleware, read_limited_body


def test_request_body_limit_rejects_oversized_and_allows_exact_limit():
    async def run():
        app = FastAPI()
        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

        @app.post("/echo")
        async def echo(request: Request):
            return {"size": len(await request.body())}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            accepted = await client.post("/echo", content=b"12345678")
            rejected = await client.post("/echo", content=b"123456789")

        assert accepted.status_code == 200 and accepted.json() == {"size": 8}
        assert rejected.status_code == 413
        assert "8-byte limit" in rejected.json()["detail"]

    asyncio.run(run())


def test_request_body_limit_enforces_streamed_bodies_without_content_length():
    async def run():
        app = FastAPI()
        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

        @app.post("/echo")
        async def echo(request: Request):
            return {"size": len(await request.body())}

        async def chunks():
            yield b"1234"
            yield b"56789"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/echo", content=chunks())

        assert response.status_code == 413

    asyncio.run(run())


def test_request_body_limit_rejects_oversized_stream_when_handler_ignores_body():
    async def run():
        app = FastAPI()
        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

        @app.post("/ignore")
        async def ignore():
            return {"ok": True}

        async def chunks():
            yield b"1234"
            yield b"56789"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/ignore", content=chunks())

        assert response.status_code == 413

    asyncio.run(run())


def test_request_body_limit_replays_drained_body_to_handler():
    async def run():
        received = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            while True:
                message = await receive()
                received.append(message)
                if not message.get("more_body", False):
                    break
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = RequestBodyLimitMiddleware(app, max_bytes=8)

        async def receive():
            return {"type": "http.request", "body": b"1234", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await middleware({"type": "http", "method": "POST", "headers": []}, receive, send)

        assert received == [{"type": "http.request", "body": b"1234", "more_body": False}]
        assert [
            message["status"] for message in sent if message["type"] == "http.response.start"
        ] == [200]

    asyncio.run(run())


def test_request_body_limit_preserves_handler_errors_during_oversized_reads():
    async def run():
        app = FastAPI()
        app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

        @app.post("/echo")
        async def echo(request: Request):
            await request.body()
            raise RuntimeError("handler failed")

        async def chunks():
            yield b"123456789"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post("/echo", content=chunks())

        assert response.status_code == 413

    asyncio.run(run())


def test_request_body_limit_checks_unread_body_before_response_start():
    async def run():
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await receive()

        middleware = RequestBodyLimitMiddleware(app, max_bytes=8)

        async def receive():
            return {"type": "http.request", "body": b"123456789", "more_body": False}

        sent = []

        async def send(_message):
            sent.append(_message)

        await middleware({"type": "http", "method": "POST", "headers": []}, receive, send)

        assert [
            message["status"] for message in sent if message["type"] == "http.response.start"
        ] == [413]

    asyncio.run(run())


def test_request_body_limit_rejects_invalid_content_length_and_handles_non_http_scope():
    async def run():
        requests = []

        async def app(scope, receive, send):
            requests.append(scope["type"])
            if scope["type"] == "http":
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"ok"})

        middleware = RequestBodyLimitMiddleware(app, max_bytes=8)

        async def receive():
            return {"type": "http.request", "body": b"123456789", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        for headers in (
            [(b"content-length", b"invalid")],
            [(b"content-length", b"\xff")],
            [(b"content-length", b"-1")],
            [(b"content-length", b"1"), (b"content-length", b"1")],
        ):
            await middleware({"type": "http", "method": "POST", "headers": headers}, receive, send)
        await middleware({"type": "websocket"}, receive, send)

        assert requests == ["websocket"]
        assert [
            message["status"] for message in sent if message["type"] == "http.response.start"
        ] == [400] * 4

    asyncio.run(run())


def test_read_limited_body_rejects_invalid_content_length():
    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        request = StarletteRequest(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-length", b"-1")],
                "query_string": b"",
                "server": ("test", 80),
                "client": ("test", 1234),
                "scheme": "http",
            },
            receive,
        )
        with pytest.raises(HTTPException) as exc:
            await read_limited_body(request, 8)
        assert exc.value.status_code == 400

    from fastapi import HTTPException

    asyncio.run(run())
