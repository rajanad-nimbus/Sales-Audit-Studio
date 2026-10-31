import asyncio

import httpx
from fastapi import FastAPI, Request

from apps.request_body import RequestBodyLimitMiddleware


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
