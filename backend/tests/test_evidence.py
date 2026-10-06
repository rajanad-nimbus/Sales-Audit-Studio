"""Evidence retrieval uses each requirement's declared source, never a guess from its wording."""
from types import SimpleNamespace as NS

import ontology
from investigation_service import evidence_for_requirement


def rows():
    mk = lambda src, typ, i: (NS(id=f"t-{i}", transaction_type=typ), NS(id=f"r-{i}", source_system=src))
    return [mk("POS", "Sale", 1), mk("Processor", "Sale", 2), mk("Processor", "Refund", 3), mk("Bank", "Deposit", 4),
            mk("ERP", "Posting", 5), mk("Shopify", "Order", 6)]


def sources(req, **kw):
    return sorted({e["source_system"] for e in evidence_for_requirement(req, rows(), **kw)})


def test_every_ontology_requirement_has_an_explicit_source_entry():
    needed = set(ontology.DEFAULT_EVIDENCE)
    for od in ontology.EXCEPTION_TYPES.values():
        needed |= set(od["evidence"])
    assert needed <= set(ontology.EVIDENCE_SOURCES), sorted(needed - set(ontology.EVIDENCE_SOURCES))


def test_wording_no_longer_misroutes_requirements():
    assert sources("Bank posting") == ["Bank"]            # "posting" used to match "pos" -> POS
    assert sources("Bank deposit detail") == ["Bank"]     # "deposit" used to match "pos" -> POS
    assert sources("GL posting reference") == ["ERP"]
    assert sources("Shopify refund record") == ["Shopify"]  # used to go to Processor because of "refund"
    assert sources("Refund details") == ["Shopify"]


def test_type_filter_and_any_are_explicit():
    assert [e["transaction_id"] for e in evidence_for_requirement("Refund confirmation", rows())] == ["t-3"]
    assert len(evidence_for_requirement("Source transaction lineage", rows())) == 6


def test_requirements_without_a_source_are_never_satisfied_by_unrelated_records():
    for req in ("Customer reference", "Connector log", "Delivery manifest", "Fee schedule", "Period status",
                "Deposit schedule", "Not a known requirement"):
        assert evidence_for_requirement(req, rows()) == []


def test_totals_and_ontology_evidence():
    totals = [NS(id="tot-1"), NS(id="tot-2")]
    assert [e["source_record_id"] for e in evidence_for_requirement("Calculated total", rows(), totals)] == ["tot-1", "tot-2"]
    assert evidence_for_requirement("Calculated total", rows()) == []   # no store-day totals -> unavailable
    got = evidence_for_requirement("Rule definition", rows(), exception_type="CONFIGURED_RULE_VARIANCE")
    assert got and got[0]["source_system"] == "Ontology"
    assert evidence_for_requirement("Rule definition", rows(), exception_type="NOT_A_TYPE") == []


def test_provided_evidence_closes_a_gap_and_supersedes_the_old_recommendation():
    import asyncio
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import investigation_service as svc
    from models import (Base, CanonicalTransaction, Case, EvidenceRequest, EvidenceSnapshot, Exception as Exc,
                        Recommendation, SourceRecord)

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        async with Session() as db:
            case = Case(id="c1", case_number="CASE-1", case_type="Reconciliation", business_date="2026-10-01", store_id="S1")
            sr = SourceRecord(id=str(uuid.uuid4()), source_system="POS", source_version="1", source_record_id="k",
                              delivery_id="d", payload=b"{}", payload_hash="h", received_at=now, source_event_time=now)
            db.add_all([case, sr])
            await db.flush()
            db.add(CanonicalTransaction(id=str(uuid.uuid4()), business_date="2026-10-01", transaction_date=now,
                                        event_timestamp=now, processing_timestamp=now, settlement_date="2026-10-02",
                                        transaction_type="Sale", source_lineage="POS", signed_amount=Decimal("10"),
                                        store_id="S1", channel_id="c", source_record_id=sr.id))
            exc = Exc(id="e1", case_id="c1", exception_type="ORPHAN_PAYMENT", exception_family="x", source_system="POS",
                      detection_origin="d", detection_rule_id="r", exception_amount=Decimal("10"),
                      estimated_exposure=Decimal("10"), severity="High", confidence="High", close_impact="None")
            db.add(exc)
            await db.commit()

            await svc.run_investigation(db, case, [exc], use_agent=False)
            await db.commit()
            # POS journal search is satisfied by POS data; Processor record and Customer reference are not.
            assert case.evidence_completeness == 33 and case.investigation_status == "Awaiting Evidence"

            for requirement in ("Processor record", "customer reference"):  # matching ignores case
                db.add(EvidenceRequest(id=str(uuid.uuid4()), case_id="c1", requirement=requirement, requested_from="Self-supplied",
                                       requested_by="alice", status="Fulfilled", response="checked", fulfilled_by="alice"))
            await db.commit()

            await svc.run_investigation(db, case, [exc], use_agent=False)
            await db.commit()
            assert case.evidence_completeness == 100 and case.investigation_status == "Ready for Decision"
            recs = (await db.execute(select(Recommendation).where(Recommendation.case_id == "c1"))).scalars().all()
            assert sorted(r.status for r in recs) == ["Proposed", "Superseded"]
            snaps = (await db.execute(select(EvidenceSnapshot).where(EvidenceSnapshot.case_id == "c1"))).scalars().all()
            assert any(s.source_system == "Provided by alice" for s in snaps)

            # An open (unfulfilled) request never counts.
            db.add(EvidenceRequest(id=str(uuid.uuid4()), case_id="c1", requirement="Fee schedule", requested_from="Bank", requested_by="a"))
            await db.commit()
            assert "fee schedule" not in await svc.provided_evidence(db, case)
    asyncio.run(go())


def test_closed_case_rejects_new_evidence_and_collaboration_exposes_override_details():
    import asyncio
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    import pytest
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import main
    from models import Base, Case, EvidenceRequest
    from schemas import EvidenceProvide

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as db:
            db.add(Case(id="c1", case_number="CASE-1", case_type="x", business_date="2026-10-01", store_id="S1",
                        status="Closed", investigation_status="Evidence Overridden"))
            db.add(EvidenceRequest(id="r1", case_id="c1", requirement="Fee schedule", requested_from="Bank", requested_by="a",
                                   status="Overridden", override_reason="Bank cannot supply it", override_financial_impact=Decimal("12.50"),
                                   override_requested_by="alice", override_approved_by="bob", rejection_reason=None))
            db.add(EvidenceRequest(id="r2", case_id="c1", requirement="Period status", requested_from="ERP", requested_by="a",
                                   status="Rejected", rejection_reason="ERP is offline"))
            await db.commit()
            with pytest.raises(HTTPException) as e:
                await main.provide_case_evidence("c1", EvidenceProvide(requirement="Fee schedule", note="n"), db, {"user": "alice"})
            assert e.value.status_code == 409
            out = await main.case_collaboration("c1", db)
            by = {r["id"]: r for r in out["evidence_requests"]}
            assert by["r1"]["override_reason"] == "Bank cannot supply it" and by["r1"]["override_financial_impact"] == "12.50"
            assert by["r1"]["override_requested_by"] == "alice" and by["r1"]["override_approved_by"] == "bob"
            assert by["r2"]["rejection_reason"] == "ERP is offline"
    asyncio.run(go())
