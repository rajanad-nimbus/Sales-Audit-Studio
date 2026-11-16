"""Defaults and readers for persisted retailer-wide system options."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.system.models import SystemOption

EXPORT_UNIT_KEY = "export_unit_of_work"
EXPORT_UNITS = ("Transaction", "Store Day")
STORE_DAY_ORDER_KEY = "store_days_worked_in_order"
BACKDATED_TRANSACTION_DAYS_KEY = "max_backdated_transaction_days"
DUPLICATE_MISSING_CHECKS_KEY = "duplicate_missing_transaction_checks"
AUTO_AUDIT_AFTER_IMPORT_KEY = "automatic_audit_after_import"
AUTO_STORE_DAY_CLOSE_KEY = "automatic_store_day_close"
CARD_SECURITY_LEVEL_KEY = "credit_card_security_level"
CARD_SECURITY_LEVELS = ("Restricted Access", "Block")
CARD_NUMBER_MASK_KEY = "credit_card_number_mask"
DEFAULT_CARD_NUMBER_MASK = "•"


def valid_card_number_mask(value: str) -> bool:
    """Accept one visible, non-whitespace character that cannot be mistaken for a digit."""
    return len(value) == 1 and value.isprintable() and not value.isspace() and not value.isdigit()


async def export_unit_of_work(db: AsyncSession) -> str:
    value = await db.scalar(select(SystemOption.value).where(SystemOption.key == EXPORT_UNIT_KEY))
    return value if value in EXPORT_UNITS else "Store Day"


async def store_days_worked_in_order(db: AsyncSession) -> bool:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == STORE_DAY_ORDER_KEY)
    )
    return value == "true"


async def max_backdated_transaction_days(db: AsyncSession) -> int | None:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == BACKDATED_TRANSACTION_DAYS_KEY)
    )
    if value is None or value == "unlimited":
        return None
    try:
        days = int(value)
    except ValueError:
        return None
    return days if 0 <= days <= 3650 else None


async def duplicate_missing_transaction_checks_enabled(db: AsyncSession) -> bool:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == DUPLICATE_MISSING_CHECKS_KEY)
    )
    return value != "false"


async def automatic_audit_after_import_enabled(db: AsyncSession) -> bool:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == AUTO_AUDIT_AFTER_IMPORT_KEY)
    )
    return value == "true"


async def automatic_store_day_close_enabled(db: AsyncSession) -> bool:
    """Whether the store-day agent signs off and closes clean store days. On unless turned off."""
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == AUTO_STORE_DAY_CLOSE_KEY)
    )
    return value != "false"


async def credit_card_security_level(db: AsyncSession) -> str:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == CARD_SECURITY_LEVEL_KEY)
    )
    return value if value in CARD_SECURITY_LEVELS else "Restricted Access"


async def credit_card_number_mask(db: AsyncSession) -> str:
    value = await db.scalar(
        select(SystemOption.value).where(SystemOption.key == CARD_NUMBER_MASK_KEY)
    )
    return value if value and valid_card_number_mask(value) else DEFAULT_CARD_NUMBER_MASK
