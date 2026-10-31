import asyncio
import hashlib
from pathlib import Path

import pytest

from apps.exports import routes
from models import ExportBatch, ExportDestination


def make_delivery(name="ERP"):
    payload = '{"records": [1]}'
    destination = ExportDestination(id="d1", name=name, kind="file", format="json", config={})
    batch = ExportBatch(id="batch-123", dataset="resolutions", destination_id="d1", idempotency_key="key",
                        created_by="it", payload=payload, payload_hash=hashlib.sha256(payload.encode()).hexdigest())
    return destination, batch


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
    destination.kind, destination.config = "webhook", {"url": "https://receiver.example", "secret": "secret"}
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
    assert headers["X-Nimbus-Signature"] == routes.sign("secret", body)
    assert headers["X-Nimbus-Envelope-Signature"] == routes.sign_envelope("secret", batch.dataset, batch.idempotency_key, body)


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
    assert list((tmp_path / "ERP").glob(".nimbus-*")) == []
