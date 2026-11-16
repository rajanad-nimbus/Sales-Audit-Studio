"""Keep retailer-defined customer attributes on canonical transactions."""

import json
import math
import re

import sqlalchemy as sa
from alembic import context, op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None

CARD_FIELDS = {
    "cardnumber",
    "creditcardnumber",
    "creditcardno",
    "ccnum",
    "ccnumber",
    "pan",
    "primaryaccountnumber",
}
REDACTED_FIELDS = CARD_FIELDS | {
    "cardexpiration",
    "cardexpirationdate",
    "cardexpiry",
    "cardexpdate",
    "cvv",
    "cvv2",
    "cvc",
    "cvc2",
    "securitycode",
    "cardsecuritycode",
    "cardverificationvalue",
    "cardverificationnumber",
    "cardverificationcode",
    "cvn",
    "track1",
    "track2",
    "track1data",
    "track2data",
    "track1equivalentdata",
    "track2equivalentdata",
    "trackdata",
    "fulltrackdata",
    "magstripedata",
    "swipedata",
}


def upgrade():
    op.add_column(
        "canonical_transactions", sa.Column("customer_attributes", sa.JSON(), nullable=True)
    )
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    transactions = sa.table(
        "canonical_transactions",
        sa.column("id", sa.String(36)),
        sa.column("source_record_id", sa.String(36)),
        sa.column("customer_attributes", sa.JSON()),
    )
    sources = sa.table(
        "source_records",
        sa.column("id", sa.String(36)),
        sa.column("payload", sa.LargeBinary()),
    )
    update = (
        transactions.update()
        .where(transactions.c.id == sa.bindparam("_id"))
        .values(customer_attributes=sa.bindparam("_attributes", type_=sa.JSON()))
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(transactions.c.id, sources.c.payload)
            .select_from(
                transactions.join(sources, sources.c.id == transactions.c.source_record_id)
            )
            .where(transactions.c.id > last_id)
            .order_by(transactions.c.id)
            .limit(500)
        ).fetchall()
        if not rows:
            break
        updates = []
        for row in rows:
            last_id = row.id
            try:
                record = json.loads(bytes(row.payload))
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            details = record.get("details") if isinstance(record.get("details"), dict) else {}
            raw = record.get("customer_attributes") or details.get("customer_attributes")
            attributes = normalize(raw)
            if attributes:
                updates.append({"_id": row.id, "_attributes": attributes})
        if updates:
            bind.execute(update, updates)


def downgrade():
    op.drop_column("canonical_transactions", "customer_attributes")


def normalize(value):
    if not isinstance(value, dict) or len(value) > 50:
        return None
    attributes = {}
    for name, raw_value in value.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 64
        ):
            return None
        if isinstance(raw_value, bool):
            text = str(raw_value).lower()
        elif isinstance(raw_value, float) and math.isfinite(raw_value):
            text = str(raw_value)
        elif isinstance(raw_value, (str, int)):
            text = str(raw_value)
        else:
            return None
        if len(text) > 255:
            return None
        field = re.sub(r"[^a-z0-9]", "", name.lower())
        if field in CARD_FIELDS:
            digits = re.sub(r"\D", "", text)
            text = f"••••{digits[-4:]}" if len(digits) >= 4 else "[REDACTED]"
        elif field in REDACTED_FIELDS:
            text = "[REDACTED]"
        else:
            text = mask_pan(text)
        if text:
            attributes[name] = text
    return attributes or None


def mask_pan(value):
    if not re.fullmatch(r"[\d -]+", value.strip()):
        return value
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19:
        return value
    checksum = 0
    for offset, char in enumerate(reversed(digits)):
        digit = int(char)
        if offset % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return f"••••{digits[-4:]}" if checksum % 10 == 0 else value
