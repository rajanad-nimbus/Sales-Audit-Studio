"""Read-only evidence tools for the Investigation agent.

Every tool is scoped to one case's store and business date, returns bounded output, and never writes.
Each result carries the ids the agent may cite; the investigator rejects citations not returned here.
"""
import json
from decimal import Decimal

from sqlalchemy import select

from models import AuditTotal, CanonicalTransaction, SourceRecord, StoreDay

MAX_ROWS = 25
MAX_PAYLOAD_CHARS = 1500

TOOL_SPECS = [
    {"name": "list_transactions",
     "description": "List canonical transactions for the case's store and business date. Optional filters.",
     "input_schema": {"type": "object", "properties": {
         "source_system": {"type": "string", "description": "POS, Processor, Bank, ERP or Shopify"},
         "transaction_type": {"type": "string"},
         "reconciliation_status": {"type": "string"},
         "payment_reference": {"type": "string"}}}},
    {"name": "get_source_record",
     "description": "Fetch the raw source payload (truncated) for a source_record_id returned by list_transactions.",
     "input_schema": {"type": "object", "properties": {"source_record_id": {"type": "string"}},
                      "required": ["source_record_id"]}},
    {"name": "get_store_day_totals",
     "description": "Calculated vs declared audit totals and variances for the case's store day.",
     "input_schema": {"type": "object", "properties": {}}},
]


def _money(v):
    return str(v) if isinstance(v, Decimal) else v


class EvidenceTools:
    def __init__(self, db, case, allowed: set[str] | None = None):
        self.db, self.case, self.allowed = db, case, allowed
        self.seen_ids: set[str] = set()  # ids the agent has legitimately been shown

    async def call(self, name: str, args: dict) -> dict:
        if self.allowed is not None and name not in self.allowed:
            return {"error": f"tool {name} is disabled"}
        fn = {"list_transactions": self.list_transactions, "get_source_record": self.get_source_record,
              "get_store_day_totals": self.get_store_day_totals}.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            return await fn(**(args or {}))
        except TypeError:
            return {"error": "invalid arguments"}

    async def list_transactions(self, source_system=None, transaction_type=None,
                                reconciliation_status=None, payment_reference=None):
        q = (select(CanonicalTransaction, SourceRecord.source_system)
             .join(SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id)
             .where(CanonicalTransaction.business_date == self.case.business_date,
                    CanonicalTransaction.store_id == self.case.store_id))
        if source_system:
            q = q.where(SourceRecord.source_system == source_system)
        if transaction_type:
            q = q.where(CanonicalTransaction.transaction_type == transaction_type)
        if reconciliation_status:
            q = q.where(CanonicalTransaction.reconciliation_status == reconciliation_status)
        if payment_reference:
            q = q.where(CanonicalTransaction.payment_reference == payment_reference)
        rows = (await self.db.execute(q.order_by(CanonicalTransaction.event_timestamp).limit(MAX_ROWS + 1))).all()
        out = []
        for t, system in rows[:MAX_ROWS]:
            self.seen_ids.update({t.id, t.source_record_id})
            out.append({"transaction_id": t.id, "source_record_id": t.source_record_id, "source_system": system,
                        "type": t.transaction_type, "amount": _money(t.signed_amount), "tender": t.tender_type,
                        "payment_reference": t.payment_reference, "settlement_date": t.settlement_date,
                        "event_time": t.event_timestamp.isoformat(), "status": t.reconciliation_status})
        return {"transactions": out, "truncated": len(rows) > MAX_ROWS}

    async def get_source_record(self, source_record_id: str):
        if source_record_id not in self.seen_ids:
            return {"error": "source_record_id not in scope; list_transactions first"}
        rec = (await self.db.execute(select(SourceRecord).where(SourceRecord.id == source_record_id))).scalar_one_or_none()
        if not rec:
            return {"error": "not found"}
        text = rec.payload.decode("utf-8", "replace")
        return {"source_record_id": rec.id, "source_system": rec.source_system,
                "payload": text[:MAX_PAYLOAD_CHARS], "truncated": len(text) > MAX_PAYLOAD_CHARS}

    async def get_store_day_totals(self):
        day = (await self.db.execute(select(StoreDay).where(
            StoreDay.store_id == self.case.store_id, StoreDay.business_date == self.case.business_date))).scalar_one_or_none()
        if not day:
            return {"totals": []}
        rows = (await self.db.execute(select(AuditTotal).where(
            AuditTotal.store_day_id == day.id, AuditTotal.audit_version == day.audit_version))).scalars().all()
        for r in rows:
            self.seen_ids.add(r.id)
        return {"totals": [{"total_id": r.id, "name": r.total_name, "dimension": r.dimension_key,
                            "calculated": _money(r.calculated_amount), "declared": _money(r.declared_amount),
                            "variance": _money(r.variance_amount)} for r in rows]}


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
