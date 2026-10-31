"""Signed webhook and atomic file delivery for persisted export batches."""

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
from pathlib import Path

import httpx

from apps.exports.models import ExportBatch, ExportDestination
from model_base import utcnow


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_envelope(secret: str, dataset: str, idempotency_key: str, body: bytes) -> str:
    """Sign canonical delivery metadata and the payload digest (protocol v1)."""
    message = (
        f"nimbus-export-v1\n{dataset}\n{idempotency_key}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    ).encode("ascii")
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def valid_signature(actual: str | None, expected: str) -> bool:
    return bool(
        actual
        and re.fullmatch(r"sha256=[0-9a-f]{64}", actual)
        and hmac.compare_digest(actual, expected)
    )


def render(
    destination: ExportDestination,
    dataset: str,
    records: list[dict],
    audit_head: str | None,
    idempotency_key: str,
    summary: dict | None = None,
) -> bytes:
    """Serialize a stable payload for a reserved export batch."""
    if destination.format == "csv":
        buffer = io.StringIO()
        if records:
            writer = csv.DictWriter(buffer, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
        return buffer.getvalue().encode()
    envelope = {
        "schema_version": "1.0",
        "dataset": dataset,
        "destination": destination.name,
        "idempotency_key": idempotency_key,
        "generated_at": utcnow().isoformat(),
        "audit_head_hash": audit_head,
        "count": len(records),
        **({"summary": summary} if summary else {}),
        "records": records,
    }
    return json.dumps(envelope, default=str, indent=1).encode()


def write_file_delivery(
    destination: ExportDestination,
    batch: ExportBatch,
    body: bytes,
    export_dir: Path,
) -> str:
    """Publish the checksum first and atomically expose the complete payload last."""
    folder_name = re.sub(r"[^A-Za-z0-9_.-]", "_", destination.name)
    if folder_name in {"", ".", ".."}:
        raise ValueError("Invalid destination directory name")
    root = export_dir.resolve()
    folder = (root / folder_name).resolve()
    if not folder.is_relative_to(root) or folder == root:
        raise ValueError("Destination directory is outside the export root")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{batch.dataset}-{batch.id}.{destination.format}"
    temporary_paths = []
    try:
        artifacts = (
            (".sha256", f"{batch.payload_hash}  {name}\n".encode()),
            ("", body),
        )
        for suffix, contents in artifacts:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".nimbus-", delete=False) as temporary:
                temporary_paths.append(Path(temporary.name))
                temporary.write(contents)
            os.replace(temporary_paths[-1], folder / (name + suffix))
        return str(folder / name)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def verified_export_body(batch: ExportBatch) -> bytes:
    """Return the persisted payload only if its digest matches the immutable batch."""
    if batch.payload is None or not batch.payload_hash:
        raise ValueError("Export payload or hash is missing")
    body = batch.payload.encode()
    digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(digest, batch.payload_hash):
        raise ValueError("Export payload does not match its recorded hash")
    return body


async def deliver(
    destination: ExportDestination,
    batch: ExportBatch,
    client: httpx.AsyncClient | None,
    export_dir: Path,
) -> None:
    """Deliver a hash-verified batch and record receiver or filesystem outcomes."""
    try:
        body = verified_export_body(batch)
        if destination.kind == "webhook":
            await deliver_webhook(destination, batch, body, client)
        else:
            await deliver_file(destination, batch, body, export_dir)
        if batch.status in ("Sent", "Acknowledged"):
            batch.sent_at, batch.error = utcnow(), None
    except Exception as exc:
        batch.status = "Failed"
        batch.error = f"{type(exc).__name__}: {str(exc)[:200]}"


async def deliver_webhook(
    destination: ExportDestination,
    batch: ExportBatch,
    body: bytes,
    client: httpx.AsyncClient | None,
) -> None:
    """Send a signed webhook and map receiver responses onto batch state."""
    config = destination.config or {}
    secret = config.get("secret", "")
    headers = {
        "Content-Type": "text/csv" if destination.format == "csv" else "application/json",
        "X-Nimbus-Idempotency-Key": batch.idempotency_key,
        "X-Nimbus-Dataset": batch.dataset,
        "X-Nimbus-Signature": sign(secret, body),
        "X-Nimbus-Envelope-Signature": sign_envelope(
            secret, batch.dataset, batch.idempotency_key, body
        ),
    }
    if client is None:
        async with httpx.AsyncClient(timeout=15) as owned_client:
            response = await owned_client.post(config["url"], content=body, headers=headers)
    else:
        response = await client.post(config["url"], content=body, headers=headers)
    batch.response_code = response.status_code
    if response.status_code in (200, 201):
        batch.status, batch.acked_at = "Acknowledged", utcnow()
        batch.response_ref = response_reference(response)
    elif response.status_code == 202:
        batch.status = "Sent"
    else:
        batch.status = "Failed"
        batch.error = f"HTTP {response.status_code}: {response.text[:200]}"


def response_reference(response: httpx.Response) -> str | None:
    """Extract a bounded receiver reference from a successful JSON response."""
    try:
        response_body = response.json()
    except ValueError:
        return None
    if not isinstance(response_body, dict):
        return None
    return str(response_body.get("reference", ""))[:255] or None


async def deliver_file(
    destination: ExportDestination,
    batch: ExportBatch,
    body: bytes,
    export_dir: Path,
) -> None:
    """Publish a verified payload and checksum through atomic file replacement."""
    batch.response_ref = await asyncio.to_thread(
        write_file_delivery, destination, batch, body, export_dir
    )
    batch.status = "Sent"
