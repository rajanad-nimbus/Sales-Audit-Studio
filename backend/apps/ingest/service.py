"""Archive source deliveries, validate records, and create canonical transactions."""
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import InvalidOperation

from sqlalchemy import and_, or_, select, text, tuple_

from apps.ingest import shopify_ingest
from apps.ingest.common import COMPOSITE_SCOPE_BATCH_SIZE, LINEAGE, REQUIRED, SIGN, VALID_TYPES
from apps.ingest.manifests import validate_business_date
from apps.totals.currency import validate_currency
from apps.store_days.freshness import invalidate_totals, lock_store_day
from apps.transactions import detail as transaction_detail
from models import (CanonicalTransaction, SourceRecord, TransactionDiscount, TransactionLine,
                    TransactionTax, TransactionTender, utcnow)

STRING_FIELD_LIMITS = {
    "source_record_id": 255,
    "store_id": 50,
    "register_id": 50,
    "cashier_id": 100,
    "channel_id": 50,
    "tender_type": 50,
    "payment_reference": 255,
    "settlement_reference": 255,
}


async def lock_source_record_identities(db, source: str, record_ids: set[str]) -> None:
    """Serialize duplicate checks for each source identity, even across business dates."""
    if db.get_bind().dialect.name != "postgresql":
        return
    ordered_ids = sorted(record_ids)
    for offset in range(0, len(ordered_ids), 500):
        identity_keys = [f"source-record:{source}:{record_id}" for record_id in ordered_ids[offset:offset + 500]]
        await db.execute(text(
            "SELECT pg_advisory_xact_lock(hashtextextended(identity_key, 0)) "
            "FROM unnest(CAST(:identity_keys AS text[])) AS lock_keys(identity_key) "
            "ORDER BY identity_key"
        ), {"identity_keys": identity_keys})


def parse(source: str, rec: dict) -> dict:
    if source not in VALID_TYPES:
        raise ValueError(f"unknown source_system {source}")
    for field in ("source_record_id", "store_id"):
        value = rec.get(field)
        if (not isinstance(value, str) or not value.strip() or value != value.strip()
                or len(value) > STRING_FIELD_LIMITS[field]):
            raise ValueError(
                f"{field} must be a non-empty string without surrounding whitespace and at most "
                f"{STRING_FIELD_LIMITS[field]} characters"
            )
    for field, max_length in STRING_FIELD_LIMITS.items():
        value = rec.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > max_length):
            raise ValueError(f"{field} must be a string of at most {max_length} characters")
    validate_currency(rec.get("currency", "USD"))
    if source == "POSControl":
        need = ["source_record_id", "business_date", "store_id", "declared_count", "declared_total"]
        missing = [k for k in need if rec.get(k) in (None, "")]
        if missing:
            raise ValueError(f"missing fields: {', '.join(missing)}")
        try:
            raw_count = rec["declared_count"]
            if isinstance(raw_count, bool):
                raise ValueError("declared_count must be a non-negative integer")
            if isinstance(raw_count, str):
                if not raw_count.isdecimal():
                    raise ValueError("declared_count must be a non-negative integer")
                count = int(raw_count)
            elif isinstance(raw_count, int):
                count = raw_count
            else:
                raise ValueError("declared_count must be a non-negative integer")
            if not 0 <= count <= 2**63 - 1:
                raise ValueError("declared_count must be a non-negative 64-bit integer")
            business_date = validate_business_date(rec["business_date"])
            total = transaction_detail.cent_value(rec["declared_total"], "declared_total")
            if abs(total) > transaction_detail.MAX_MONEY:
                raise ValueError("invalid manifest values")
            return {"manifest": True, "count": count,
                    "total": total,
                    "ts": datetime.fromisoformat(f"{business_date}T00:00:00+00:00")}
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            raise ValueError("invalid manifest values")
    cashier = rec.get("cashier_id")
    if cashier is not None and not cashier.strip():
        raise ValueError("cashier_id must not be blank")
    if source == "Shopify":
        return shopify_ingest.parse(rec)
    missing = [k for k in REQUIRED if rec.get(k) in (None, "")]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if not isinstance(rec["record_type"], str) or rec["record_type"] not in VALID_TYPES[source]:
        raise ValueError(f"invalid record_type {rec['record_type']} for {source}")
    amount = transaction_detail.cent_value(rec["amount"], "amount")
    if amount <= 0:
        raise ValueError("amount must be positive")
    if amount > transaction_detail.MAX_MONEY:
        raise ValueError("amount exceeds supported precision")
    try:
        ts = datetime.fromisoformat(str(rec["event_time"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("event_time is not ISO-8601")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    try:
        detail = transaction_detail.extract(rec, SIGN[rec["record_type"]])
    except (ValueError, InvalidOperation, OverflowError) as e:
        raise ValueError(f"invalid transaction detail: {e}")
    return {"amount": amount, "ts": ts, "detail": detail}


def store_detail(db, tx: CanonicalTransaction, detail: dict | None) -> None:
    """Persist the lines, tax, discounts and tenders of a transaction and record whether they add up to its amount."""
    if not detail:
        return
    for ln in detail["lines"]:
        db.add(TransactionLine(id=str(uuid.uuid4()), transaction_id=tx.id, **ln))
    for t in detail["taxes"]:
        db.add(TransactionTax(id=str(uuid.uuid4()), transaction_id=tx.id, **t))
    for d in detail["discounts"]:
        db.add(TransactionDiscount(id=str(uuid.uuid4()), transaction_id=tx.id, **d))
    for t in detail["tenders"]:
        db.add(TransactionTender(id=str(uuid.uuid4()), transaction_id=tx.id, **t))
    tx.original_reference, tx.return_reason = detail["original_reference"], detail["return_reason"]
    tx.detail_status = transaction_detail.check(detail, tx.signed_amount)["status"]


async def link_returns(db, reference_keys: set[tuple[str, str]]) -> int:
    """Link returns affected by this delivery's sale or return references."""
    if not reference_keys:
        return 0

    linked = 0
    return_page_size = 500
    reference_batch_size = COMPOSITE_SCOPE_BATCH_SIZE
    ordered_keys = sorted(reference_keys)

    for offset in range(0, len(ordered_keys), reference_batch_size):
        batch_keys = ordered_keys[offset:offset + reference_batch_size]
        last_id = ""
        while True:
            pending_query = select(CanonicalTransaction).where(
                CanonicalTransaction.transaction_type == "Return",
                CanonicalTransaction.original_reference.is_not(None),
                CanonicalTransaction.original_transaction_id.is_(None),
                CanonicalTransaction.id > last_id,
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.original_reference).in_(batch_keys),
            ).order_by(CanonicalTransaction.id).limit(return_page_size).with_for_update()
            pending = (await db.execute(pending_query)).scalars().all()
            if not pending:
                break
            last_id = pending[-1].id

            returns_by_reference: dict[tuple[str, str], list[CanonicalTransaction]] = defaultdict(list)
            for row in pending:
                if row.original_reference:
                    returns_by_reference[(row.store_id, row.original_reference)].append(row)
            pending_keys = list(returns_by_reference)
            match_conditions = [
                and_(
                    CanonicalTransaction.store_id == store_id,
                    or_(SourceRecord.source_record_id == reference,
                        CanonicalTransaction.payment_reference == reference),
                )
                for store_id, reference in pending_keys
            ]
            candidates = await db.stream(
                select(CanonicalTransaction, SourceRecord.source_record_id)
                .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
                .where(CanonicalTransaction.transaction_type == "Sale", or_(*match_conditions))
                .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
                .execution_options(yield_per=500)
            )
            try:
                async for sale, source_record_id in candidates:
                    references = {source_record_id, sale.payment_reference} - {None, ""}
                    for reference in references:
                        for return_row in returns_by_reference.get((sale.store_id, reference), ()):
                            if return_row.original_transaction_id is None:
                                return_row.original_transaction_id = sale.id
                                linked += 1
            finally:
                await candidates.close()
    return linked


async def ingest(db, source: str, delivery_id: str, records: list[dict]) -> dict:
    stats = {"received": 0, "archived": 0, "duplicates_skipped": 0, "quarantined": 0, "canonical_created": 0}
    prepared = []
    record_ids = set()
    for record in records:
        payload = json.dumps(record, sort_keys=True, default=str).encode()
        identity = shopify_ingest.identity_payload(record) if source == "Shopify" else record
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
        supplied_record_id = record.get("source_record_id")
        record_id = (supplied_record_id if isinstance(supplied_record_id, str)
                     and supplied_record_id.strip()
                     and len(supplied_record_id) <= STRING_FIELD_LIMITS["source_record_id"]
                     else f"unknown-{digest[:12]}")
        parsed, parse_error = None, None
        try:
            parsed = parse(source, record)
            if not parsed.get("manifest"):
                settled_on = record.get("settled_on")
                try:
                    parsed["settlement_date"] = (
                        datetime.fromisoformat(str(settled_on).replace("Z", "+00:00")).date().isoformat()
                        if settled_on not in (None, "") else parsed["ts"].date().isoformat()
                    )
                except ValueError:
                    raise ValueError("settled_on is not ISO-8601") from None
        except ValueError as exc:
            parse_error = str(exc)
        prepared.append((record, payload, digest, record_id, parsed, parse_error))
        record_ids.add(record_id)

    # Lock store days in a deterministic order before writing any records in this delivery.
    keys = set()
    for record, _, _, _, parsed_record, _ in prepared:
        store = record.get("store_id")
        if parsed_record and isinstance(store, str) and store:
            keys.add((store, parsed_record["ts"].date().isoformat()))
    days = {}
    for store, business_date in sorted(keys):
        day = await lock_store_day(db, store, business_date)
        if day:
            days[(store, business_date)] = day

    # Recheck identities after acquiring store-day locks so concurrent re-deliveries
    # and cross-date replays observe rows committed by the transaction ahead of them.
    await lock_source_record_identities(db, source, record_ids)
    prior_hashes: dict[str, set[str]] = defaultdict(set)
    sorted_ids = sorted(record_ids)
    for offset in range(0, len(sorted_ids), 500):
        rows = (await db.execute(select(SourceRecord.source_record_id, SourceRecord.payload_hash).where(
            SourceRecord.source_system == source,
            SourceRecord.source_record_id.in_(sorted_ids[offset:offset + 500])))).all()
        for record_id, digest in rows:
            prior_hashes[record_id].add(digest)

    changed_days = set()
    return_reference_keys: set[tuple[str, str]] = set()
    for rec, payload, digest, srid, parsed_record, parse_error in prepared:
        stats["received"] += 1
        prior = prior_hashes[srid]
        if digest in prior:
            stats["duplicates_skipped"] += 1
            continue

        status, reason, parsed = "Archived", None, None
        if prior:
            status, reason = "Quarantined", "Conflicting resubmission: same record id, different payload"
        elif parse_error is not None:
            status, reason = "Quarantined", parse_error
        else:
            parsed = parsed_record

        sr = SourceRecord(
            id=str(uuid.uuid4()), source_system=source, source_version="1.0", source_record_id=srid,
            delivery_id=delivery_id, payload=payload, payload_hash=digest, received_at=utcnow(),
            source_event_time=parsed["ts"] if parsed else utcnow(), status=status, quarantine_reason=reason)
        db.add(sr)
        prior.add(digest)
        if status == "Quarantined":
            stats["quarantined"] += 1
            continue
        store = rec.get("store_id")
        day_key = (store, parsed["ts"].date().isoformat()) if isinstance(store, str) else None
        day = days.get(day_key)
        if day and day_key not in changed_days:
            changed_days.add(day_key)
            await invalidate_totals(db, day, "ingest")
        stats["archived"] += 1
        if parsed.get("manifest"):
            continue

        ts, bd = parsed["ts"], parsed["ts"].date().isoformat()
        tx = CanonicalTransaction(
            id=str(uuid.uuid4()), business_date=bd, transaction_date=ts, event_timestamp=ts,
            processing_timestamp=utcnow(), settlement_date=parsed["settlement_date"],
            transaction_type=rec["record_type"], source_lineage=LINEAGE[source],
            currency=rec.get("currency", "USD"), signed_amount=SIGN[rec["record_type"]] * parsed["amount"],
            store_id=rec["store_id"], register_id=rec.get("register_id"), cashier_id=rec.get("cashier_id"),
            channel_id=rec.get("channel_id") or "store",
            tender_type=rec.get("tender_type"), payment_reference=rec.get("payment_reference"),
            settlement_reference=rec.get("settlement_reference"),
            source_record_id=sr.id, reconciliation_status="Unmatched", disposition="Open")
        db.add(tx)
        store_detail(db, tx, parsed.get("detail"))
        if rec["record_type"] == "Sale":
            for reference in (rec.get("source_record_id"), rec.get("payment_reference")):
                if isinstance(reference, str) and reference:
                    return_reference_keys.add((tx.store_id, reference))
        elif rec["record_type"] == "Return" and tx.original_reference:
            return_reference_keys.add((tx.store_id, tx.original_reference))
        sr.status, sr.extracted_at = "Processed", utcnow()
        stats["canonical_created"] += 1
    await db.flush()
    stats["returns_linked"] = await link_returns(db, return_reference_keys)
    return stats
