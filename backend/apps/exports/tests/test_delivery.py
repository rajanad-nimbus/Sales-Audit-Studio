import asyncio
import hashlib
from pathlib import Path

import pytest

from apps.exports import routes
from apps.exports.delivery import render
from models import ExportBatch, ExportDestination

WEBHOOK_SECRET = "s" * 32


def make_delivery(name="ERP"):
    payload = '{"records": [1]}'
    destination = ExportDestination(id="d1", name=name, kind="file", format="json", config={})
    batch = ExportBatch(id="batch-123", dataset="resolutions", destination_id="d1", idempotency_key="key",
                        created_by="it", payload=payload, payload_hash=hashlib.sha256(payload.encode()).hexdigest())
    return destination, batch


def test_csv_exports_neutralize_spreadsheet_formulas():
    destination, _ = make_delivery()
    destination.format = "csv"

    payload = render(
        destination,
        "transactions",
        [{"reference": "=HYPERLINK(\"https://example.test\")", "amount": 10}],
        None,
        "key",
    )

    assert payload.decode() == 'reference,amount\r\n"\'=HYPERLINK(""https://example.test"")",10\r\n'


def test_file_delivery_is_stable_across_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)
    destination, batch = make_delivery()

    async def run():
        await routes.deliver(destination, batch)
        assert batch.status == "Sent"
        path = Path(batch.response_ref)
        assert path.read_text() == batch.payload
        assert path.with_name(path.name + ".sha256").read_text() == f"{batch.payload_hash}  {path.name}\n"
        await routes.deliver(destination, batch)
        assert Path(batch.response_ref) == path
        assert len(list(path.parent.glob("*.json"))) == 1

    asyncio.run(run())


def test_webhook_sends_body_and_metadata_signatures(monkeypatch):
    destination, batch = make_delivery()
    destination.kind, destination.config = "webhook", {"url": "https://receiver.example", "secret": WEBHOOK_SECRET}
    captured = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, _url, *, content, headers):
            captured.update(body=content, headers=headers)
            return type("Response", (), {"status_code": 202})()

    monkeypatch.setattr(routes.httpx, "AsyncClient", Client)
    asyncio.run(routes.deliver(destination, batch))
    assert batch.status == "Sent"
    headers, body = captured["headers"], captured["body"]
    assert headers["X-Nimbus-Signature"] == routes.sign(WEBHOOK_SECRET, body)
    assert headers["X-Nimbus-Envelope-Signature"] == routes.sign_envelope(WEBHOOK_SECRET, batch.dataset, batch.idempotency_key, body)


def test_webhook_failure_does_not_persist_receiver_response_body(monkeypatch):
    destination, batch = make_delivery()
    destination.kind, destination.config = "webhook", {"url": "https://receiver.example", "secret": WEBHOOK_SECRET}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            return type("Response", (), {"status_code": 500, "text": "secret payload echoed by receiver"})()

    monkeypatch.setattr(routes.httpx, "AsyncClient", Client)
    asyncio.run(routes.deliver(destination, batch))

    assert batch.status == "Failed"
    assert batch.error == "HTTP 500"
    assert "secret payload" not in batch.error


def test_webhook_transport_error_does_not_persist_url_credentials(monkeypatch):
    destination, batch = make_delivery()
    destination.kind = "webhook"
    destination.config = {
        "url": "https://receiver.example/path?token=top-secret",
        "secret": WEBHOOK_SECRET,
    }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, **_kwargs):
            raise routes.httpx.ConnectError(f"connection failed for {url}")

    monkeypatch.setattr(routes.httpx, "AsyncClient", Client)
    asyncio.run(routes.deliver(destination, batch))

    assert batch.status == "Failed"
    assert batch.error == "Webhook request failed"
    assert "top-secret" not in batch.error
    assert "receiver.example" not in batch.error


@pytest.mark.parametrize("response_body", [ValueError("invalid JSON"), ["unexpected", "JSON"]])
def test_webhook_acknowledges_success_even_when_response_reference_is_unusable(monkeypatch, response_body):
    destination, batch = make_delivery()
    destination.kind, destination.config = "webhook", {"url": "https://receiver.example", "secret": WEBHOOK_SECRET}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *_args, **_kwargs):
            def parse_json():
                if isinstance(response_body, Exception):
                    raise response_body
                return response_body

            return type("Response", (), {"status_code": 200, "json": staticmethod(parse_json)})()

    monkeypatch.setattr(routes.httpx, "AsyncClient", Client)
    asyncio.run(routes.deliver(destination, batch))

    assert batch.status == "Acknowledged"
    assert batch.response_ref is None


@pytest.mark.parametrize("secret", [None, "", " " * 32, "too-short"])
def test_webhook_with_invalid_persisted_secret_fails_closed(monkeypatch, secret):
    destination, batch = make_delivery()
    destination.kind = "webhook"
    destination.config = {"url": "https://receiver.example", "secret": secret}

    async def unexpected_post(*_args, **_kwargs):
        pytest.fail("webhook must not be sent with an invalid signing secret")

    class Client:
        post = unexpected_post

    asyncio.run(routes.deliver(destination, batch, Client()))

    assert batch.status == "Failed"
    assert batch.error == "Webhook signing secret is missing or too short"


def test_envelope_signature_interoperability_vector():
    assert routes.sign_envelope("secret", "resolutions", "a" * 64, b"payload") == (
        "sha256=04e3620ad0ae650d3bf9c7d6963ff6cf625fc79a1bad6345372295eae080edbb"
    )


@pytest.mark.parametrize("name", ["", ".", ".."])
def test_file_delivery_rejects_unsafe_directory_names(tmp_path, monkeypatch, name):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path / "exports")
    destination, batch = make_delivery(name)
    asyncio.run(routes.deliver(destination, batch))
    assert batch.status == "Failed"
    assert "Invalid destination directory" in batch.error
    assert list(tmp_path.iterdir()) == []


def test_file_delivery_rejects_symlink_outside_root(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    root.mkdir()
    (root / "ERP").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(routes, "EXPORT_DIR", root)
    destination, batch = make_delivery()
    asyncio.run(routes.deliver(destination, batch))
    assert batch.status == "Failed"
    assert "outside the export root" in batch.error


@pytest.mark.parametrize("kind", ["file", "webhook"])
def test_tampered_payload_is_not_delivered(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)
    destination, batch = make_delivery()
    destination.kind = kind
    batch.payload = "tampered"
    asyncio.run(routes.deliver(destination, batch))
    assert batch.status == "Failed"
    assert "does not match" in batch.error
    assert list(tmp_path.iterdir()) == []


def test_failed_publish_does_not_expose_partial_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)
    destination, batch = make_delivery()
    replace = routes.os.replace

    def fail_payload(source, target):
        if str(target).endswith(".json"):
            raise OSError("disk unavailable")
        replace(source, target)

    monkeypatch.setattr(routes.os, "replace", fail_payload)
    asyncio.run(routes.deliver(destination, batch))
    assert batch.status == "Failed"
    assert list((tmp_path / "ERP").glob("*.json")) == []
    assert list((tmp_path / "ERP").glob("*.sha256")) == []
    assert list((tmp_path / "ERP").glob(".nimbus-*")) == []


def test_failed_retry_restores_previous_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "EXPORT_DIR", tmp_path)
    destination, batch = make_delivery()

    async def publish_original():
        await routes.deliver(destination, batch)

    asyncio.run(publish_original())
    path = Path(batch.response_ref)
    previous_payload = path.read_bytes()
    previous_checksum = path.with_name(path.name + ".sha256").read_bytes()
    batch.payload = '{"records": [2]}'
    batch.payload_hash = hashlib.sha256(batch.payload.encode()).hexdigest()
    replace = routes.os.replace

    def fail_payload(source, target):
        if str(target).endswith(".json"):
            raise OSError("disk unavailable")
        replace(source, target)

    monkeypatch.setattr(routes.os, "replace", fail_payload)
    asyncio.run(routes.deliver(destination, batch))

    assert batch.status == "Failed"
    assert path.read_bytes() == previous_payload
    assert path.with_name(path.name + ".sha256").read_bytes() == previous_checksum
    assert list(path.parent.glob(".nimbus-*")) == []
