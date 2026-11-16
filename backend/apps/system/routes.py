"""System endpoints used by platform health checks and deployment readiness."""

import uuid
from typing import Literal

from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from integrations.ontology.nimbus import audit
from integrations.ontology.ontology_integration import integration as ontology_integration
from model_base import utcnow
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ingest.models import SourceRecord
from apps.security.authentication import AUTH_ENABLED, require_role
from apps.store_days.freshness import lock_configuration
from apps.system.models import Connector, SystemOption
from apps.system.packages import router as system_options_package_router
from apps.system.options import (
    AUTO_AUDIT_AFTER_IMPORT_KEY,
    BACKDATED_TRANSACTION_DAYS_KEY,
    CARD_NUMBER_MASK_KEY,
    CARD_SECURITY_LEVEL_KEY,
    CARD_SECURITY_LEVELS,
    DEFAULT_CARD_NUMBER_MASK,
    DUPLICATE_MISSING_CHECKS_KEY,
    EXPORT_UNIT_KEY,
    STORE_DAY_ORDER_KEY,
    automatic_audit_after_import_enabled,
    credit_card_number_mask,
    credit_card_security_level,
    duplicate_missing_transaction_checks_enabled,
    max_backdated_transaction_days,
    store_days_worked_in_order,
    valid_card_number_mask,
)

router = APIRouter()
router.include_router(system_options_package_router)


class ExportUnitOfWorkIn(BaseModel):
    unit_of_work: Literal["Transaction", "Store Day"]


class StoreDayOrderIn(BaseModel):
    enabled: bool


class BackdatedTransactionLimitIn(BaseModel):
    max_days: int | None = Field(default=None, ge=0, le=3650)


class DuplicateMissingChecksIn(BaseModel):
    enabled: bool


class AutomaticAuditAfterImportIn(BaseModel):
    enabled: bool


class CardSecurityLevelIn(BaseModel):
    security_level: Literal["Restricted Access", "Block"]


class CardNumberMaskIn(BaseModel):
    mask_character: str = Field(min_length=1, max_length=1)

    @field_validator("mask_character")
    @classmethod
    def validate_mask_character(cls, value: str) -> str:
        if not valid_card_number_mask(value):
            raise ValueError("Mask character must be one printable, non-digit character")
        return value


@router.get("/api/system-options/backdated-transaction-limit")
async def get_backdated_transaction_limit(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, BACKDATED_TRANSACTION_DAYS_KEY)
    return {
        "max_days": await max_backdated_transaction_days(db),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/backdated-transaction-limit")
async def update_backdated_transaction_limit(
    body: BackdatedTransactionLimitIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    value = str(body.max_days) if body.max_days is not None else "unlimited"
    values = {
        "key": BACKDATED_TRANSACTION_DAYS_KEY,
        "value": value,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, BACKDATED_TRANSACTION_DAYS_KEY)
    before = previous.value if previous else "unlimited"
    await db.execute(statement)
    audit(
        db,
        "BACKDATED_TRANSACTION_LIMIT_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, BACKDATED_TRANSACTION_DAYS_KEY)),
        f"Maximum backdated transaction age changed from {before} to {value}",
        actor=actor,
        before={"max_days": before},
        after={"max_days": value},
    )
    await db.commit()
    return {"max_days": body.max_days, "updated_by": actor, "updated_at": now}


@router.get("/api/system-options/duplicate-missing-transaction-checks")
async def get_duplicate_missing_checks(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, DUPLICATE_MISSING_CHECKS_KEY)
    return {
        "enabled": await duplicate_missing_transaction_checks_enabled(db),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/duplicate-missing-transaction-checks")
async def update_duplicate_missing_checks(
    body: DuplicateMissingChecksIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    value = str(body.enabled).lower()
    values = {
        "key": DUPLICATE_MISSING_CHECKS_KEY,
        "value": value,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, DUPLICATE_MISSING_CHECKS_KEY)
    before = previous.value != "false" if previous else True
    await db.execute(statement)
    audit(
        db,
        "DUPLICATE_MISSING_CHECKS_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, DUPLICATE_MISSING_CHECKS_KEY)),
        f"Duplicate and missing transaction checks changed from {before} to {body.enabled}",
        actor=actor,
        before={"enabled": before},
        after={"enabled": body.enabled},
    )
    await db.commit()
    return {"enabled": body.enabled, "updated_by": actor, "updated_at": now}


@router.get("/api/system-options/audit-after-import")
async def get_audit_after_import(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, AUTO_AUDIT_AFTER_IMPORT_KEY)
    return {
        "enabled": await automatic_audit_after_import_enabled(db),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/audit-after-import")
async def update_audit_after_import(
    body: AutomaticAuditAfterImportIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    value = str(body.enabled).lower()
    values = {
        "key": AUTO_AUDIT_AFTER_IMPORT_KEY,
        "value": value,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, AUTO_AUDIT_AFTER_IMPORT_KEY)
    before = previous.value == "true" if previous else False
    await db.execute(statement)
    audit(
        db,
        "AUTOMATIC_AUDIT_AFTER_IMPORT_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, AUTO_AUDIT_AFTER_IMPORT_KEY)),
        f"Automatic audit after import changed from {before} to {body.enabled}",
        actor=actor,
        before={"enabled": before},
        after={"enabled": body.enabled},
    )
    await db.commit()
    return {"enabled": body.enabled, "updated_by": actor, "updated_at": now}


@router.get("/api/system-options/store-days-worked-in-order")
async def get_store_day_order(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("finance", "it", "auditor")),
):
    option = await db.get(SystemOption, STORE_DAY_ORDER_KEY)
    return {
        "enabled": await store_days_worked_in_order(db),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/store-days-worked-in-order")
async def update_store_day_order(
    body: StoreDayOrderIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    value = str(body.enabled).lower()
    values = {
        "key": STORE_DAY_ORDER_KEY,
        "value": value,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, STORE_DAY_ORDER_KEY)
    before = previous.value == "true" if previous else False
    await db.execute(statement)
    audit(
        db,
        "STORE_DAY_ORDER_POLICY_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, STORE_DAY_ORDER_KEY)),
        f"Store days worked in order changed from {before} to {body.enabled}",
        actor=actor,
        before={"enabled": before},
        after={"enabled": body.enabled},
    )
    await db.commit()
    return {"enabled": body.enabled, "updated_by": actor, "updated_at": now}


@router.get("/api/system-options/export-unit-of-work")
async def export_unit_of_work(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, EXPORT_UNIT_KEY)
    return {
        "unit_of_work": option.value if option else "Store Day",
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/export-unit-of-work")
async def update_export_unit_of_work(
    body: ExportUnitOfWorkIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    values = {
        "key": EXPORT_UNIT_KEY,
        "value": body.unit_of_work,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, EXPORT_UNIT_KEY)
    before = previous.value if previous else "Store Day"
    await db.execute(statement)
    audit(
        db,
        "EXPORT_UNIT_OF_WORK_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, EXPORT_UNIT_KEY)),
        f"Export unit of work changed from {before} to {body.unit_of_work}",
        actor=actor,
        before={"unit_of_work": before},
        after={"unit_of_work": body.unit_of_work},
    )
    await db.commit()
    return {"unit_of_work": body.unit_of_work, "updated_by": actor, "updated_at": now}


@router.get("/api/connectors")
async def list_connectors(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Connector).order_by(Connector.name))).scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "state": row.state,
            "mode": row.mode,
            "last_error": row.last_error,
            "last_checked_at": row.last_checked_at,
        }
        for row in rows
    ]


@router.post("/api/connectors/{connector_id}/{action}")
async def connector_action(
    connector_id: str,
    action: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("it")),
):
    """Reject connector controls until a real adapter can execute them."""
    if action not in {"retry", "pause", "resume"}:
        raise HTTPException(status_code=400, detail="Unknown action")
    connector = await db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    raise HTTPException(
        status_code=501,
        detail=f"{action.capitalize()} is unavailable: {connector.name} has no configured connector adapter",
    )


@router.get("/")
async def root():
    return {
        "message": "Sales Audit Studio API",
        "version": "1.0.0",
        "description": "Agentic Financial Exception Management for Retail Commerce",
    }


@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    await db.execute(select(1))
    return {
        "status": "healthy",
        "database": "healthy",
    }


@router.get("/api/production-readiness")
async def production_readiness(db: AsyncSession = Depends(get_db)):
    """Show the concrete prerequisites before a sales-audit cutover."""
    rows = (
        await db.execute(
            select(SourceRecord.source_system, func.max(SourceRecord.received_at))
            .where(SourceRecord.status.in_(("Archived", "Processed")))
            .group_by(SourceRecord.source_system)
        )
    ).all()
    received = {source: timestamp for source, timestamp in rows}
    sources = [
        {"source_system": source, "received_at": received.get(source), "ready": source in received}
        for source in ("POS", "POSControl", "Processor", "Bank", "ERP", "Shopify")
    ]
    integration = ontology_integration.status()
    checks = [
        {
            "name": "Ontology user authentication",
            "ready": AUTH_ENABLED,
            "detail": "Nimbus delegates user authentication to Ontology Studio.",
        },
        {
            "name": "Approved Sales Audit ontology",
            "ready": integration["ready"],
            "detail": "Approve and release the rules, then configure an ons_ integration key.",
        },
        {
            "name": "Required source feeds",
            "ready": all(item["ready"] for item in sources),
            "detail": "Configure scheduled read-only POS, control, processor, bank, and ERP deliveries.",
        },
    ]
    return {
        "ready": all(check["ready"] for check in checks),
        "checks": checks,
        "sources": sources,
        "next_inputs": [
            "source schemas and read-only endpoints",
            "matching keys and control totals",
            "cutoff calendar and tolerance policy",
            "approval matrix and escalation owners",
        ],
    }


@router.get("/api/system-options/card-security")
async def get_card_security(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, CARD_SECURITY_LEVEL_KEY)
    return {
        "security_level": await credit_card_security_level(db),
        "levels": list(CARD_SECURITY_LEVELS),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/card-security")
async def update_card_security(
    body: CardSecurityLevelIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    values = {
        "key": CARD_SECURITY_LEVEL_KEY,
        "value": body.security_level,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, CARD_SECURITY_LEVEL_KEY)
    before = previous.value if previous else "Restricted Access"
    await db.execute(statement)
    audit(
        db,
        "CARD_SECURITY_LEVEL_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, CARD_SECURITY_LEVEL_KEY)),
        f"Card security changed from {before} to {body.security_level}",
        actor=actor,
        before={"security_level": before},
        after={"security_level": body.security_level},
    )
    await db.commit()
    return {
        "security_level": body.security_level,
        "levels": list(CARD_SECURITY_LEVELS),
        "updated_by": actor,
        "updated_at": now,
    }


@router.get("/api/system-options/card-number-mask")
async def get_card_number_mask(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_role("analyst", "finance", "it", "auditor")),
):
    option = await db.get(SystemOption, CARD_NUMBER_MASK_KEY)
    return {
        "mask_character": await credit_card_number_mask(db),
        "updated_by": option.updated_by if option else None,
        "updated_at": option.updated_at if option else None,
    }


@router.put("/api/system-options/card-number-mask")
async def update_card_number_mask(
    body: CardNumberMaskIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("it")),
):
    await lock_configuration(db, exclusive=True)
    actor = user["user"] or "ontology.user"
    now = utcnow()
    values = {
        "key": CARD_NUMBER_MASK_KEY,
        "value": body.mask_character,
        "updated_by": actor,
        "updated_at": now,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgres_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(SystemOption).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[SystemOption.key],
            set_={key: getattr(statement.excluded, key) for key in values if key != "key"},
        )
    else:
        raise HTTPException(
            status_code=501, detail="System options are unsupported by this database"
        )
    previous = await db.get(SystemOption, CARD_NUMBER_MASK_KEY)
    before = previous.value if previous else DEFAULT_CARD_NUMBER_MASK
    await db.execute(statement)
    audit(
        db,
        "CARD_NUMBER_MASK_CHANGED",
        None,
        "SystemOption",
        str(uuid.uuid5(uuid.NAMESPACE_URL, CARD_NUMBER_MASK_KEY)),
        "Card number mask character changed",
        actor=actor,
        before={"mask_character": before},
        after={"mask_character": body.mask_character},
    )
    await db.commit()
    return {
        "mask_character": body.mask_character,
        "updated_by": actor,
        "updated_at": now,
    }
