"""Mask historic card data in canonical transaction and source records."""

import json
import re

import sqlalchemy as sa
from alembic import context, op

revision = "0114"
down_revision = "0113"
branch_labels = None
depends_on = None

BATCH_SIZE = 500
CARD_NUMBER_FIELDS = {
    "cardnumber",
    "creditcardnumber",
    "creditcardno",
    "ccnum",
    "ccnumber",
    "pan",
    "primaryaccountnumber",
}
REDACTED_FIELDS = CARD_NUMBER_FIELDS | {
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
PAN_VALUE_FIELDS = {
    "originalreference",
    "reference",
    "paymentreference",
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


def redact(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            name = normalized_name(key)
            if name in REDACTED_FIELDS:
                if item is None:
                    safe[key] = None
                elif name in CARD_NUMBER_FIELDS:
                    digits = re.sub(r"\D", "", str(item))
                    safe[key] = f"••••{digits[-4:]}" if len(digits) >= 4 else "[REDACTED]"
                else:
                    safe[key] = "[REDACTED]"
            elif name in PAN_VALUE_FIELDS:
                safe[key] = mask_pan(item)
            else:
                safe[key] = redact(item)
        return safe
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def backfill_columns(bind, table_name, columns):
    table = sa.table(
        table_name,
        sa.column("id", sa.String(36)),
        *(sa.column(name, column_type) for name, column_type, _ in columns),
    )
    select_columns = [table.c.id, *(table.c[name] for name, _, _ in columns)]
    update = (
        table.update()
        .where(table.c.id == sa.bindparam("_id"))
        .values({name: sa.bindparam(f"_{name}") for name, _, _ in columns})
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(*select_columns)
            .where(table.c.id > last_id)
            .order_by(table.c.id)
            .limit(BATCH_SIZE)
        ).fetchall()
        if not rows:
            return
        updates = []
        for row in rows:
            changed = {"_id": row.id}
            modified = False
            for name, _, transform in columns:
                old = row._mapping[name]
                new = transform(old)
                changed[f"_{name}"] = new
                if new != old:
                    modified = True
            if modified:
                updates.append(changed)
        if updates:
            bind.execute(update, updates)
        last_id = rows[-1].id


def decode_payload(value):
    if value is None:
        return None
    try:
        return json.loads(bytes(value))
    except (TypeError, ValueError):
        return value


def encode_payload(value):
    if value is None or isinstance(value, bytes):
        return value
    return json.dumps(value, sort_keys=True, default=str).encode()


def redact_payload(value):
    decoded = decode_payload(value)
    safe = redact(decoded)
    return encode_payload(safe) if safe != decoded else value


def upgrade():
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    backfill_columns(
        bind,
        "canonical_transactions",
        [
            ("payment_reference", sa.String(255), mask_pan),
            ("settlement_reference", sa.String(255), mask_pan),
            ("original_reference", sa.String(255), mask_pan),
            ("user_references", sa.JSON(), redact),
        ],
    )
    backfill_columns(
        bind,
        "transaction_tenders",
        [
            ("reference", sa.String(255), mask_pan),
            ("authorization", sa.String(100), mask_pan),
            ("source_attributes", sa.JSON(), redact),
        ],
    )
    backfill_columns(
        bind,
        "transaction_lines",
        [("source_attributes", sa.JSON(), redact)],
    )
    backfill_columns(
        bind,
        "register_events",
        [("details", sa.JSON(), redact)],
    )
    backfill_columns(
        bind,
        "transaction_adjustments",
        [("proposed_values", sa.JSON(), redact)],
    )
    backfill_columns(
        bind,
        "source_records",
        [("payload", sa.LargeBinary(), redact_payload)],
    )


def downgrade():
    # Redaction is intentionally irreversible; restoring card data would be unsafe.
    pass
