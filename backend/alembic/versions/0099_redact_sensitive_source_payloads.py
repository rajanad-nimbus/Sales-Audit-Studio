"""Redact card credentials from previously archived source payloads."""

import json
import re

import sqlalchemy as sa
from alembic import context, op

revision = "0099"
down_revision = "0098"
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


def redact_sensitive_fields(value):
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            field_name = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if field_name in REDACTED_FIELDS:
                if item is None:
                    safe[key] = None
                elif field_name in CARD_NUMBER_FIELDS:
                    digits = re.sub(r"\D", "", str(item))
                    safe[key] = f"••••{digits[-4:]}" if len(digits) >= 4 else "[REDACTED]"
                else:
                    safe[key] = "[REDACTED]"
            else:
                safe[key] = redact_sensitive_fields(item)
        return safe
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    return value


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    source_records = sa.table(
        "source_records",
        sa.column("id", sa.String(length=36)),
        sa.column("payload", sa.LargeBinary()),
    )
    update_payload = (
        source_records.update()
        .where(source_records.c.id == sa.bindparam("_record_id"))
        .values(payload=sa.bindparam("_payload"))
    )
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(source_records.c.id, source_records.c.payload)
            .where(source_records.c.id > last_id)
            .order_by(source_records.c.id)
            .limit(BATCH_SIZE)
        ).fetchall()
        if not rows:
            break
        updates = []
        for record_id, payload in rows:
            try:
                decoded = json.loads(bytes(payload))
            except (TypeError, ValueError):
                continue
            safe = redact_sensitive_fields(decoded)
            if safe != decoded:
                updates.append(
                    {
                        "_record_id": record_id,
                        "_payload": json.dumps(safe, sort_keys=True, default=str).encode(),
                    }
                )
        if updates:
            bind.execute(update_payload, updates)
        last_id = rows[-1][0]


def downgrade() -> None:
    # Redaction is intentionally irreversible; restoring credentials would be unsafe.
    pass
