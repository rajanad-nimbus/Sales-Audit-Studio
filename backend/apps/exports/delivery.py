"""Signed webhook and atomic file delivery for persisted export batches."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import tempfile
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from model_base import utcnow

from apps.csv_export import csv_table
from apps.exports.models import ExportBatch, ExportDestination

MAX_RESPONSE_REFERENCE_BYTES = 64 * 1024
MAX_RESPONSE_REFERENCE_SECONDS = 5
MAX_WEBHOOK_RESPONSE_SECONDS = 30


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_envelope(secret: str, dataset: str, idempotency_key: str, body: bytes) -> str:
    """Sign canonical delivery metadata and the payload digest (protocol v1)."""
    message = (
        f"nimbus-export-v1\n{dataset}\n{idempotency_key}\n{hashlib.sha256(body).hexdigest()}"
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
        if records:
            fieldnames = list(records[0].keys())
            return csv_table(
                fieldnames,
                ([record.get(fieldname, "") for fieldname in fieldnames] for record in records),
            ).encode()
        return b""
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
    checksum_path = folder / (name + ".sha256")
    previous_checksum = checksum_path.read_bytes() if checksum_path.exists() else None
    checksum_published = False
    temporary_paths = []
    try:
        artifacts = (
            (".sha256", f"{batch.payload_hash}  {name}\n".encode()),
            ("", body),
        )
        for suffix, contents in artifacts:
            with tempfile.NamedTemporaryFile(
                dir=folder, prefix=".nimbus-", delete=False
            ) as temporary:
                temporary_paths.append(Path(temporary.name))
                temporary.write(contents)
            os.replace(temporary_paths[-1], folder / (name + suffix))
            if suffix == ".sha256":
                checksum_published = True
        return str(folder / name)
    except BaseException:
        if checksum_published:
            if previous_checksum is None:
                checksum_path.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(
                    dir=folder, prefix=".nimbus-", delete=False
                ) as backup:
                    backup.write(previous_checksum)
                    temporary_paths.append(Path(backup.name))
                os.replace(temporary_paths[-1], checksum_path)
        raise
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


def safe_delivery_error(error: Exception) -> str:
    """Return a useful failure category without persisting URLs, paths, or remote text."""
    if isinstance(error, httpx.TimeoutException):
        return "Webhook request timed out"
    if isinstance(error, httpx.HTTPError):
        return "Webhook request failed"
    if isinstance(error, OSError):
        return "File delivery failed"
    if isinstance(error, ValueError):
        safe_messages = {
            "Invalid destination directory name",
            "Destination directory is outside the export root",
            "Export payload or hash is missing",
            "Export payload does not match its recorded hash",
            "Webhook signing secret is missing or too short",
        }
        return (
            str(error)
            if str(error) in safe_messages
            else "Export payload or destination is invalid"
        )
    return "Delivery failed"


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
        batch.error = safe_delivery_error(exc)


async def deliver_webhook(
    destination: ExportDestination,
    batch: ExportBatch,
    body: bytes,
    client: httpx.AsyncClient | None,
) -> None:
    """Send a signed webhook and map receiver responses onto batch state."""
    config = destination.config or {}
    secret = config.get("secret")
    if not isinstance(secret, str) or len(secret.strip()) < 32:
        raise ValueError("Webhook signing secret is missing or too short")
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
            status_code, response_body = await post_webhook(
                owned_client, config["url"], body, headers
            )
    else:
        status_code, response_body = await post_webhook(client, config["url"], body, headers)
    batch.response_code = status_code
    if 200 <= status_code < 300 and status_code != 202:
        batch.status, batch.acked_at = "Acknowledged", utcnow()
        batch.response_ref = response_reference(response_body)
    elif status_code == 202:
        batch.status = "Sent"
    else:
        batch.status = "Failed"
        batch.error = f"HTTP {status_code}"


async def post_webhook(
    client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
) -> tuple[int, bytes | None]:
    """Bound the time to receive a status and the optional reference's size and time."""
    async with AsyncExitStack() as stack:
        try:
            async with asyncio.timeout(MAX_WEBHOOK_RESPONSE_SECONDS):
                response = await stack.enter_async_context(
                    client.stream("POST", url, content=body, headers=headers)
                )
        except TimeoutError as exc:
            raise httpx.TimeoutException("Webhook response timed out") from exc
        status_code = response.status_code
        if not 200 <= status_code < 300 or status_code == 202:
            return status_code, None
        response_body = bytearray()
        try:
            async with asyncio.timeout(MAX_RESPONSE_REFERENCE_SECONDS):
                async for chunk in response.aiter_bytes():
                    if len(response_body) + len(chunk) > MAX_RESPONSE_REFERENCE_BYTES:
                        return status_code, None
                    response_body.extend(chunk)
        except TimeoutError:
            return status_code, None
    return status_code, bytes(response_body)


def response_reference(response_body: bytes | None) -> str | None:
    """Extract a bounded receiver reference from a bounded JSON response."""
    if response_body is None:
        return None
    try:
        response_data = json.loads(response_body)
    except ValueError:
        return None
    if not isinstance(response_data, dict):
        return None
    return str(response_data.get("reference", ""))[:255] or None


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
