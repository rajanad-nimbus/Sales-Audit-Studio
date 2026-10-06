"""Score the Investigation agent against labeled scenarios. Needs ANTHROPIC_API_KEY.

Run from backend/:  python -m evals.run_investigator_evals
Metrics: cause accuracy, false-support rate (claiming a cause the label says is unsupported), citation rejection.
"""
import asyncio
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import investigator
import ontology
from models import Base, Case, CanonicalTransaction, Exception as Exc, SourceRecord


async def run_scenario(sc):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with Session() as db:
        case = Case(id="c", case_number="EVAL", case_type="Reconciliation", business_date="2026-10-01", store_id="S1")
        exc = Exc(id="e", case_id="c", exception_type=sc["exception_type"], exception_family="x", source_system="POS",
                  detection_origin="eval", detection_rule_id="r", exception_amount=Decimal(sc["amount"]),
                  estimated_exposure=Decimal(sc["amount"]), severity="High", confidence="High", close_impact="None")
        db.add_all([case, exc])
        for r in sc["records"]:
            sr = SourceRecord(id=str(uuid.uuid4()), source_system=r["source"], source_version="1",
                              source_record_id=str(uuid.uuid4()), delivery_id="d",
                              payload=json.dumps(r["payload"]).encode(), payload_hash="h", received_at=now,
                              source_event_time=now)
            db.add(sr)
            await db.flush()
            db.add(CanonicalTransaction(
                id=str(uuid.uuid4()), business_date="2026-10-01", transaction_date=now, event_timestamp=now,
                processing_timestamp=now, settlement_date="2026-10-02", transaction_type=r["type"],
                source_lineage=r["source"], signed_amount=Decimal(r["amount"]), store_id="S1", channel_id="c",
                payment_reference=r["ref"], source_record_id=sr.id))
        await db.commit()
        od = ontology.get(sc["exception_type"])
        return await investigator.run(db, case, [exc], od["label"], od["known_causes"])


async def main():
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY required")
    scenarios = json.loads((pathlib.Path(__file__).parent / "scenarios.json").read_text())
    hit = false_support = rejected = expected_total = 0
    for sc in scenarios:
        out = await run_scenario(sc)
        res = (out or {}).get("result")
        supported = {h["cause"] for h in (res or {}).get("hypotheses", []) if h["verdict"] == "supported"}
        ok = set(sc["expected_supported"]) <= supported
        bad = supported & set(sc["expected_not_supported"])
        hit += ok
        false_support += bool(bad)
        rejected += len((res or {}).get("rejected_citations", []))
        expected_total += 1
        print(f"{'PASS' if ok and not bad else 'FAIL'} {sc['id']}: supported={sorted(supported)} bad={sorted(bad)}"
              f" tokens={(out or {}).get('trace', {}).get('input_tokens')}")
    print(f"\ncause accuracy {hit}/{expected_total}  false-support {false_support}/{expected_total}  rejected citations {rejected}")


if __name__ == "__main__":
    asyncio.run(main())
