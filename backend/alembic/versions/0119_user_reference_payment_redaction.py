"""Redact PAN-shaped values inside retailer-defined transaction references."""

import json
import re

import sqlalchemy as sa
from alembic import context, op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None

BATCH_SIZE = 500
PAN_VALUE_FIELDS = {
    "originalreference",
    "reference",
    "reference1",
    "reference2",
    "reference3",
    "reference4",
    "paymentreference",
    "paymentreferencenumber",
    "tenderreference",
    "settlementreference",
    "sourcerecordid",
    "authorization",
    "authorizationcode",
}


def normalized_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def mask_pan(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return value
    text = str(value)
    if not re.fullmatch(r"[\d -]+", text.strip()):
        return value
    digits = re.sub(r"\D", "", text)
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


def redact_user_reference_list(value):
    if not isinstance(value, list):
        return redact(value)
    safe = []
    for row in value:
        if isinstance(row, dict) and "field" in row and "value" in row:
            safe.append({key: mask_pan(item) if key == "value" else redact(item) for key, item in row.items()})
        else:
            safe.append(redact(row))
    return safe


def redact(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            name = normalized_name(key)
            if name in PAN_VALUE_FIELDS:
                safe[key] = mask_pan(item)
            elif name in {"userreferences", "retailerreferences"}:
                safe[key] = redact_user_reference_list(item)
            else:
                safe[key] = redact(item)
        return safe
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def decode_payload(value):
    if value is None:
        return None
    try:
        return json.loads(bytes(value))
    except (TypeError, ValueError):
        return value


def backfill(bind, table_name, column_name, column_type, transform):
    table = sa.table(
        table_name,
        sa.column("id", sa.String(36)),
        sa.column(column_name, column_type),
    )
    update = (
        table.update()
        .where(table.c.id == sa.bindparam("_id"))
        .values({column_name: sa.bindparam("_value")})
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(table.c.id, table.c[column_name])
            .where(table.c.id > last_id)
            .order_by(table.c.id)
            .limit(BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        updates = []
        for row in rows:
            old = row._mapping[column_name]
            new = transform(old)
            if new != old:
                updates.append({"_id": row.id, "_value": new})
        if updates:
            bind.execute(update, updates)
        last_id = rows[-1].id


def upgrade():
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for table_name, column_name in (
        ("canonical_transactions", "user_references"),
        ("transaction_tenders", "source_attributes"),
        ("transaction_lines", "source_attributes"),
        ("register_events", "details"),
        ("transaction_adjustments", "proposed_values"),
    ):
        backfill(bind, table_name, column_name, sa.JSON(), redact)

    def redact_payload(value):
        decoded = decode_payload(value)
        safe = redact(decoded)
        return json.dumps(safe, sort_keys=True, default=str).encode() if safe != decoded else value

    backfill(bind, "source_records", "payload", sa.LargeBinary(), redact_payload)


def downgrade():
    # Restoring PAN-shaped data is intentionally irreversible.
    pass
