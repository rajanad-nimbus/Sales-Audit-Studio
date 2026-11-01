"""Export destination configuration and administration endpoints."""

import re
import uuid
from urllib.parse import urlsplit

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.database_errors import violates_unique_constraint
from apps.exports.models import ExportDestination
from apps.exports.schemas import DestIn
from apps.exports.settings import sink_secret, test_sink_enabled
from apps.security.authentication import require_role

router = APIRouter()
DEFAULT_DESTINATION_NAMESPACE = uuid.UUID("e3648eeb-fb20-4290-a08b-fd39404b0be4")


def _is_duplicate_destination_name(exc: IntegrityError) -> bool:
    return violates_unique_constraint(
        exc,
        constraint_names={"export_destinations_name_key"},
        sqlite_messages={"UNIQUE constraint failed: export_destinations.name"},
    )


def dest_view(d: ExportDestination, *, reveal_url: bool = False) -> dict:
    cfg = dict(d.config or {})
    if "secret" in cfg:
        cfg["secret"] = "••••••" if cfg["secret"] else ""
    if cfg.get("url") and not reveal_url:
        cfg["url"] = webhook_origin(str(cfg["url"]))
    return {
        "id": d.id,
        "name": d.name,
        "kind": d.kind,
        "config": cfg,
        "format": d.format,
        "enabled": d.enabled,
    }


def webhook_origin(url: str) -> str:
    """Expose only the webhook origin when the caller cannot manage destination credentials."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname:
            return "Webhook configured"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "Webhook configured"
    return f"{parsed.scheme}://{host}{port}"


async def ensure_defaults(db: AsyncSession, *, test_sink_enabled: bool, sink_secret: str) -> None:
    """Create default destinations once, safely under concurrent startup requests."""
    if (await db.execute(select(func.count()).select_from(ExportDestination))).scalar():
        return
    defaults = []
    if test_sink_enabled:
        defaults.append(
            {
                "id": str(uuid.uuid5(DEFAULT_DESTINATION_NAMESPACE, "Test receiver (webhook)")),
                "name": "Test receiver (webhook)",
                "kind": "webhook",
                "format": "json",
                "config": {
                    "url": "http://localhost:8000/api/exports/_sink",
                    "secret": sink_secret,
                },
            }
        )
    defaults.append(
        {
            "id": str(uuid.uuid5(DEFAULT_DESTINATION_NAMESPACE, "ERP/GL file drop")),
            "name": "ERP/GL file drop",
            "kind": "file",
            "format": "csv",
            "config": {},
        }
    )
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = (
            postgresql_insert(ExportDestination)
            .values(defaults)
            .on_conflict_do_nothing(index_elements=["name"])
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(ExportDestination)
            .values(defaults)
            .on_conflict_do_nothing(index_elements=["name"])
        )
    else:
        raise RuntimeError(
            f"Default export destinations are unsupported for database dialect {dialect}"
        )
    await db.execute(statement)
    await db.commit()


@router.get("/destinations")
async def list_destinations(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "it", "auditor")),
):
    await ensure_defaults(db, test_sink_enabled=test_sink_enabled(), sink_secret=sink_secret())
    reveal_url = user["role"] in {"it", "admin"}
    destinations = (
        await db.execute(select(ExportDestination).order_by(ExportDestination.name))
    ).scalars()
    return [dest_view(destination, reveal_url=reveal_url) for destination in destinations]


@router.post("/destinations")
async def create_destination(
    body: DestIn, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))
):
    if body.kind not in ("webhook", "file") or body.format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="kind must be webhook|file and format json|csv")
    if body.kind == "webhook" and not (body.url and re.match(r"^https?://", body.url)):
        raise HTTPException(status_code=400, detail="A webhook needs an http(s) URL")
    config = {"url": body.url, "secret": body.secret or ""} if body.kind == "webhook" else {}
    destination = ExportDestination(
        id=str(uuid.uuid4()),
        name=body.name.strip(),
        kind=body.kind,
        format=body.format,
        config=config,
    )
    db.add(destination)
    audit(
        db,
        "EXPORT_DESTINATION_CREATED",
        None,
        "ExportDestination",
        destination.id,
        f"Destination '{destination.name}' ({destination.kind}) created",
        actor=user["user"] or "demo.user",
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_destination_name(exc):
            raise HTTPException(
                status_code=409, detail="A destination with that name exists"
            ) from exc
        raise
    return dest_view(destination, reveal_url=True)


@router.post("/destinations/{dest_id}/toggle")
async def toggle_destination(
    dest_id: str, db: AsyncSession = Depends(get_db), user: dict = Depends(require_role("it"))
):
    destination = (
        await db.execute(
            select(ExportDestination).where(ExportDestination.id == dest_id).with_for_update()
        )
    ).scalar_one_or_none()
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    destination.enabled = not destination.enabled
    audit(
        db,
        "EXPORT_DESTINATION_TOGGLED",
        None,
        "ExportDestination",
        destination.id,
        f"'{destination.name}' {'enabled' if destination.enabled else 'disabled'}",
        actor=user["user"] or "demo.user",
    )
    await db.commit()
    return dest_view(destination, reveal_url=True)
