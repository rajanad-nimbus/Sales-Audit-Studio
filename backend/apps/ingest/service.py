"""Archive source deliveries, validate records, and create canonical transactions."""
import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from decimal import InvalidOperation

from sqlalchemy import and_, or_, select, text, tuple_

from apps.ingest.shopify import ingest as shopify_ingest
from apps.ingest.common import COMPOSITE_SCOPE_BATCH_SIZE, LINEAGE, REQUIRED, SIGN, VALID_TYPES
from apps.ingest.manifests import validate_business_date
from apps.totals.currency import validate_currency
from apps.store_days.freshness import invalidate_totals_many, lock_store_days
from apps.transactions import detail as transaction_detail
from apps.transactions.models import (
    CanonicalTransaction,
    TransactionDiscount,
    TransactionLine,
    TransactionTax,
    TransactionTender,
)
from model_base import utcnow
from apps.ingest.models import SourceRecord

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


@dataclass(slots=True)
class IngestResult:
    """Accumulated persistence effects for one source delivery."""
    stats: dict[str, int] = dataclass_field(default_factory=lambda: {
        "received": 0, "archived": 0, "duplicates_skipped": 0,
        "quarantined": 0, "canonical_created": 0,
    })
    changed_days: set[tuple[str, str]] = dataclass_field(default_factory=set)
    return_reference_keys: set[tuple[str, str]] = dataclass_field(default_factory=set)


@dataclass(slots=True)
class PreparedRecord:
    """Validated and serialized delivery record ready for transactional persistence."""
    record: dict
    payload: bytes
    digest: str
    record_id: str
    parsed: dict | None
    parse_error: str | None


def prepare_record(source: str, record: dict) -> PreparedRecord:
    """Serialize, identify, and validate one incoming record without database access."""
    payload = json.dumps(record, sort_keys=True, default=str).encode()
    identity = shopify_ingest.identity_payload(record) if source == "Shopify" else record
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    supplied_id = record.get("source_record_id")
    record_id = (supplied_id if isinstance(supplied_id, str) and supplied_id.strip()
                 and len(supplied_id) <= STRING_FIELD_LIMITS["source_record_id"]
                 else f"unknown-{digest[:12]}")
    parsed, parse_error = None, None
    try:
        parsed = parse(source, record)
        if not parsed.get("manifest"):
            parsed["settlement_date"] = parse_settlement_date(record, parsed["ts"])
    except ValueError as exc:
        parse_error = str(exc)
    return PreparedRecord(record, payload, digest, record_id, parsed, parse_error)


def parse_settlement_date(record: dict, event_timestamp: datetime) -> str:
    """Normalize an optional settlement date, defaulting to the event's UTC date."""
    settled_on = record.get("settled_on")
    try:
        if settled_on in (None, ""):
            return event_timestamp.date().isoformat()
        return datetime.fromisoformat(str(settled_on).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        raise ValueError("settled_on is not ISO-8601") from None


async def lock_delivery_store_days(db, prepared: list[PreparedRecord]):
    """Lock valid store days in deterministic order before delivery persistence."""
    keys = {
        (prepared_record.record.get("store_id"), prepared_record.parsed["ts"].date().isoformat())
        for prepared_record in prepared
        if prepared_record.parsed
        and isinstance(prepared_record.record.get("store_id"), str)
        and prepared_record.record.get("store_id")
    }
    return await lock_store_days(db, keys)


async def load_prior_record_hashes(
    db, source: str, prepared: list[PreparedRecord],
) -> dict[str, set[str]]:
    """Lock record identities and load existing payload hashes in bounded queries."""
    record_ids = {prepared_record.record_id for prepared_record in prepared}
    await lock_source_record_identities(db, source, record_ids)
    prior_hashes: dict[str, set[str]] = defaultdict(set)
    ordered_ids = sorted(record_ids)
    for offset in range(0, len(ordered_ids), 500):
        result = await db.execute(select(SourceRecord.source_record_id, SourceRecord.payload_hash).where(
            SourceRecord.source_system == source,
            SourceRecord.source_record_id.in_(ordered_ids[offset:offset + 500])))
        for record_id, digest in result.all():
            prior_hashes[record_id].add(digest)
    return prior_hashes


def persist_prepared_records(
    db, source, delivery_id, prepared: list[PreparedRecord], prior_hashes, days,
) -> IngestResult:
    """Apply idempotency, archive accepted records, and create canonical transactions."""
    result = IngestResult()
    for prepared_record in prepared:
        record = prepared_record.record
        record_id = prepared_record.record_id
        digest = prepared_record.digest
        result.stats["received"] += 1
        prior = prior_hashes[record_id]
        if digest in prior:
            result.stats["duplicates_skipped"] += 1
            continue
        archived = archive_source_record(db, source, delivery_id, prepared_record, prior)
        if archived is None:
            result.stats["quarantined"] += 1
            continue
        source_record, parsed = archived
        result.stats["archived"] += 1
        store_id = record.get("store_id")
        day_key = (store_id, parsed["ts"].date().isoformat()) if isinstance(store_id, str) else None
        if day_key in days:
            result.changed_days.add(day_key)
        if parsed.get("manifest"):
            continue
        transaction = create_canonical_transaction(db, source, record, parsed, source_record)
        collect_return_reference(result.return_reference_keys, record, transaction)
        source_record.status, source_record.extracted_at = "Processed", utcnow()
        result.stats["canonical_created"] += 1
    return result


def archive_source_record(db, source, delivery_id, prepared_record: PreparedRecord, prior):
    """Archive one new payload or record a quarantine reason, returning parsed valid data."""
    reason = None
    parsed = prepared_record.parsed
    if prior:
        reason, parsed = "Conflicting resubmission: same record id, different payload", None
    elif prepared_record.parse_error is not None:
        reason, parsed = prepared_record.parse_error, None
    source_record = SourceRecord(
        id=str(uuid.uuid4()), source_system=source,
        source_version="1.0", source_record_id=prepared_record.record_id,
        delivery_id=delivery_id, payload=prepared_record.payload,
        payload_hash=prepared_record.digest, received_at=utcnow(),
        source_event_time=parsed["ts"] if parsed else utcnow(),
        status="Quarantined" if reason else "Archived", quarantine_reason=reason)
    db.add(source_record)
    prior.add(prepared_record.digest)
    return None if reason else (source_record, parsed)


def create_canonical_transaction(db, source, record, parsed, source_record):
    """Create a canonical transaction and persist its validated transaction details."""
    timestamp = parsed["ts"]
    transaction = CanonicalTransaction(
        id=str(uuid.uuid4()), business_date=timestamp.date().isoformat(), transaction_date=timestamp,
        event_timestamp=timestamp, processing_timestamp=utcnow(),
        settlement_date=parsed["settlement_date"], transaction_type=record["record_type"],
        source_lineage=LINEAGE[source], currency=record.get("currency", "USD"),
        signed_amount=SIGN[record["record_type"]] * parsed["amount"], store_id=record["store_id"],
        register_id=record.get("register_id"), cashier_id=record.get("cashier_id"),
        channel_id=record.get("channel_id") or "store", tender_type=record.get("tender_type"),
        payment_reference=record.get("payment_reference"),
        settlement_reference=record.get("settlement_reference"), source_record_id=source_record.id,
        reconciliation_status="Unmatched", disposition="Open")
    db.add(transaction)
    store_detail(db, transaction, parsed.get("detail"))
    return transaction


def collect_return_reference(reference_keys, record, transaction) -> None:
    """Collect references needed to link newly ingested sales and returns."""
    if record["record_type"] == "Sale":
        for reference in (record.get("source_record_id"), record.get("payment_reference")):
            if isinstance(reference, str) and reference:
                reference_keys.add((transaction.store_id, reference))
    elif record["record_type"] == "Return" and transaction.original_reference:
        reference_keys.add((transaction.store_id, transaction.original_reference))


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
    validate_source_identifiers(rec)
    validate_currency(rec.get("currency", "USD"))
    if source == "POSControl":
        return parse_manifest(rec)
    cashier = rec.get("cashier_id")
    if cashier is not None and not cashier.strip():
        raise ValueError("cashier_id must not be blank")
    if source == "Shopify":
        return shopify_ingest.parse(rec)
    return parse_transaction(source, rec)


def validate_source_identifiers(rec: dict) -> None:
    """Validate required and optional identifier fields against database constraints."""
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


def parse_manifest(rec: dict) -> dict:
    """Validate a POS control manifest and return its canonical values."""
    need = ["source_record_id", "business_date", "store_id", "declared_count", "declared_total"]
    missing = [key for key in need if rec.get(key) in (None, "")]
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
        return {"manifest": True, "count": count, "total": total,
                "ts": datetime.fromisoformat(f"{business_date}T00:00:00+00:00")}
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise ValueError("invalid manifest values")


def parse_transaction(source: str, rec: dict) -> dict:
    """Validate a POS-family transaction and parse its amount, timestamp, and details."""
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
            pending = await load_pending_return_page(db, batch_keys, last_id, return_page_size)
            if not pending:
                break
            last_id = pending[-1].id
            linked += await link_return_page(db, pending)
    return linked


async def load_pending_return_page(db, batch_keys, last_id: str, page_size: int):
    """Load the next locked page of unlinked returns for a reference batch."""
    query = select(CanonicalTransaction).where(
        CanonicalTransaction.transaction_type == "Return",
        CanonicalTransaction.original_reference.is_not(None),
        CanonicalTransaction.original_transaction_id.is_(None),
        CanonicalTransaction.id > last_id,
        tuple_(CanonicalTransaction.store_id, CanonicalTransaction.original_reference).in_(batch_keys),
    ).order_by(CanonicalTransaction.id).limit(page_size).with_for_update()
    return (await db.execute(query)).scalars().all()


async def link_return_page(db, pending: list[CanonicalTransaction]) -> int:
    """Stream candidate sales and link each pending return to the earliest match."""
    returns_by_reference: dict[tuple[str, str], list[CanonicalTransaction]] = defaultdict(list)
    for return_transaction in pending:
        if return_transaction.original_reference:
            key = (return_transaction.store_id, return_transaction.original_reference)
            returns_by_reference[key].append(return_transaction)
    conditions = [
        and_(
            CanonicalTransaction.store_id == store_id,
            or_(SourceRecord.source_record_id == reference,
                CanonicalTransaction.payment_reference == reference),
        )
        for store_id, reference in returns_by_reference
    ]
    if not conditions:
        return 0
    candidates = await db.stream(
        select(CanonicalTransaction, SourceRecord.source_record_id)
        .outerjoin(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
        .where(CanonicalTransaction.transaction_type == "Sale", or_(*conditions))
        .order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
        .execution_options(yield_per=500)
    )
    linked = 0
    try:
        async for sale, source_record_id in candidates:
            references = {source_record_id, sale.payment_reference} - {None, ""}
            for reference in references:
                matching_returns = returns_by_reference.get((sale.store_id, reference), ())
                for return_transaction in matching_returns:
                    if return_transaction.original_transaction_id is None:
                        return_transaction.original_transaction_id = sale.id
                        linked += 1
    finally:
        await candidates.close()
    return linked


async def ingest(db, source: str, delivery_id: str, records: list[dict]) -> dict:
    prepared = [prepare_record(source, record) for record in records]
    days = await lock_delivery_store_days(db, prepared)
    prior_hashes = await load_prior_record_hashes(db, source, prepared)
    result = persist_prepared_records(db, source, delivery_id, prepared, prior_hashes, days)
    await invalidate_totals_many(db, [days[key] for key in sorted(result.changed_days)], "ingest")
    await db.flush()
    result.stats["returns_linked"] = await link_returns(db, result.return_reference_keys)
    return result.stats
