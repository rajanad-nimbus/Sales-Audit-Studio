"""Deterministic end-to-end Nimbus Sales Audit demonstration data."""
import asyncio, uuid
from decimal import Decimal
from sqlalchemy import func, select
from database import async_session
import ingest, investigation_service
from models import (AuditTotalDefinition, CanonicalTransaction, CashControl, CashDeposit, Case,
    ConfiguredAuditRule, FoundationReference, GLCrossReference, ReconciliationPolicy, StoreDay)
from fastapi import HTTPException
from routes_store_days import open_store_day_record, retotal_store_day

DAY = "2026-10-06"
USER = {"user": "demo.finance", "role": "finance"}

async def main():
  async with async_session() as db:
    # Retail master data and policies make each screen immediately usable.
    for category, code, name in [
      ("Store", "STORE-007", "Kathmandu Central"), ("Store", "STORE-014", "Lalitpur Mall"),
      ("Store", "STORE-022", "Bhaktapur Square"), ("Store", "STORE-031", "Pokhara Lakeside"),
      ("Tender", "Card", "Payment card"), ("Tender", "Cash", "Physical cash"),
      ("BankAccount", "OPERATING-001", "Operating settlement account"),
      ("GLAccount", "110100", "Cash clearing"), ("GLAccount", "400100", "Sales revenue"),
    ]:
      db.add(FoundationReference(id=str(uuid.uuid4()), category=category, code=code, name=name, active=True, attributes={}, updated_by="demo.seed"))
    db.add(ReconciliationPolicy(id=str(uuid.uuid4()), name="Demo standard tolerance", amount_tolerance=Decimal("0.00"), settlement_day_tolerance=1, active=True, updated_by="demo.seed"))
    db.add(AuditTotalDefinition(id=str(uuid.uuid4()), name="Card sales", aggregation="Sum", source_system="POS", transaction_types=["Sale"], tender_type="Card", level="Store", active=True, updated_by="demo.seed"))
    db.add(ConfiguredAuditRule(id=str(uuid.uuid4()), name="Large daily card sales", total_name="Configured: Card sales", threshold=Decimal("1000"), severity="High", owner="demo.finance", enabled=True, updated_by="demo.seed"))
    for transaction_type, tender, debit, credit in [("Sale", "Card", "110100", "400100"), ("Return", "Card", "400100", "110100"), ("Order", None, "110100", "400100"), ("Refund", None, "400100", "110100")]:
      db.add(GLCrossReference(id=str(uuid.uuid4()), transaction_type=transaction_type, tender_type=tender, debit_account=debit, credit_account=credit, active=True, updated_by="demo.seed"))
    await db.commit()

    feeds = ingest.simulate_feed(DAY)
    for source, records in feeds.items():
      await ingest.ingest(db, source, f"DEMO-{DAY}-{source}", records)
    await db.commit()
    reconciliation = await ingest.reconcile(db, DAY)
    await db.commit()
    investigated = await investigation_service.auto_investigate(db, DAY)

    # Store-day versions expose POS control, processor-bank and POS-GL controls.
    stores = (await db.execute(select(CanonicalTransaction.store_id).where(CanonicalTransaction.business_date == DAY).distinct())).scalars().all()
    for store in stores:
      try:
        await open_store_day_record(db, store, DAY, USER["user"] or "demo.seed")
      except HTTPException as e:
        if e.status_code != 409:  # already open on a re-seed is fine
          raise
      await retotal_store_day(store, DAY, db, USER)

    # Cash-office examples: one balanced drawer/deposit and one over-short drawer/deposit.
    examples = [("STORE-007", "CASH-DEMO-007", Decimal("420.00"), Decimal("420.00"), "Balanced"),
                ("STORE-014", "CASH-DEMO-014", Decimal("510.00"), Decimal("500.00"), "Over/Short")]
    for store, ref, expected, deposited, status in examples:
      db.add(CashControl(id=str(uuid.uuid4()), store_id=store, business_date=DAY, register_id="R1", cashier_id="CASHIER-01", tender_type="Cash", declared_cash=expected, paid_out=Decimal("0"), safe_drop=Decimal("0"), expected_amount=expected, variance_amount=Decimal("0"), status="Balanced", declared_by="demo.cashier"))
      db.add(CashDeposit(id=str(uuid.uuid4()), store_id=store, business_date=DAY, deposit_reference=ref, bank_account="OPERATING-001", deposited_amount=deposited, expected_amount=expected, variance_amount=expected-deposited, status=status, recorded_by="demo.cashier"))
    await db.commit()
    print({"day": DAY, "feeds": {k: len(v) for k,v in feeds.items()}, "reconciliation": reconciliation, "agent_investigations": investigated, "store_days": len(stores), "cases": await db.scalar(select(func.count()).select_from(Case))})

if __name__ == "__main__": asyncio.run(main())
