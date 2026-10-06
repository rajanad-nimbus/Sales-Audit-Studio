"""Shared investigation entry point: scoped evidence retrieval, optional agent, deterministic findings."""
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import agent_settings
import investigator
import llm
import nimbus
import ontology
from models import AuditTotal, Case, CanonicalTransaction, EvidenceRequest, Recommendation, SourceRecord, StoreDay


def evidence_for_requirement(requirement: str, rows: list[tuple[CanonicalTransaction, SourceRecord]],
                             totals=(), exception_type: str | None = None) -> list[dict]:
    """Return only real, scoped records that satisfy an ontology requirement, using its declared source.

    A requirement with no declared source (or one missing from the table) is never satisfied automatically.
    """
    spec = ontology.EVIDENCE_SOURCES.get(requirement)
    if not spec:
        return []
    if spec.get("totals"):
        return [{"source_record_id": t.id, "transaction_id": t.id, "source_system": "Store-day totals"} for t in totals]
    if spec.get("ontology"):
        if not (exception_type and ontology.get(exception_type)):
            return []
        ref = f"ontology:{exception_type}:{ontology.ONTOLOGY_VERSION}"
        return [{"source_record_id": ref, "transaction_id": ref, "source_system": "Ontology"}]
    selected = []
    for txn, record in rows:
        if spec.get("source") and record.source_system != spec["source"]:
            continue
        if spec.get("type") and txn.transaction_type != spec["type"]:
            continue
        selected.append({"source_record_id": record.id, "transaction_id": txn.id,
                         "source_system": record.source_system})
    return selected


async def case_source_rows(db: AsyncSession, case: Case) -> list[tuple[CanonicalTransaction, SourceRecord]]:
    result = await db.execute(
        select(CanonicalTransaction, SourceRecord).join(
            SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id
        ).where(CanonicalTransaction.business_date == case.business_date,
                CanonicalTransaction.store_id == case.store_id)
    )
    return list(result.all())


async def case_store_day_totals(db: AsyncSession, case: Case) -> list[AuditTotal]:
    day = (await db.execute(select(StoreDay).where(
        StoreDay.store_id == case.store_id, StoreDay.business_date == case.business_date))).scalar_one_or_none()
    if not day:
        return []
    return list((await db.execute(select(AuditTotal).where(
        AuditTotal.store_day_id == day.id, AuditTotal.audit_version == day.audit_version))).scalars().all())


async def provided_evidence(db: AsyncSession, case: Case) -> dict[str, list[dict]]:
    """Evidence a person has supplied for this case, keyed by requirement. It stands in only where no ingested
    record satisfies the requirement, and is labelled with who provided it."""
    rows = (await db.execute(select(EvidenceRequest).where(
        EvidenceRequest.case_id == case.id, EvidenceRequest.status == "Fulfilled"))).scalars().all()
    out: dict[str, list[dict]] = {}
    for r in rows:
        ref = f"provided:{r.id}"
        out.setdefault(r.requirement.strip().lower(), []).append(
            {"source_record_id": ref, "transaction_id": ref, "source_system": f"Provided by {r.fulfilled_by or 'a reviewer'}"})
    return out


async def run_investigation(db: AsyncSession, case: Case, exceptions, use_agent: bool = True) -> object:
    from ontology_integration import integration
    await integration.refresh()
    context = integration.context()
    od = ontology.get(exceptions[0].exception_type)
    narrative = None
    if od:
        cfg = await agent_settings.load(db)
        if use_agent and cfg["investigator_enabled"]:
            run = await investigator.run(db, case, exceptions, od["label"], od["known_causes"],
                                         model=cfg["model"], enabled_tools=cfg["tools"], ontology_context=context)
            if run:
                nimbus.audit(db, "AGENT_RUN", case.id, "Case", case.id,
                             "Investigation agent run" + (" failed" if not run["result"] else ""),
                             actor="investigation-agent", after={**run["trace"], "result": run["result"],
                                    "ontology_context": {k: context[k] for k in ("source", "ontology")}})
                if run["result"]:
                    narrative = investigator.render(run["result"])
        if not narrative:
            narrative = await llm.narrate(od["label"], str(case.total_exception_amount), od["evidence"], od["known_causes"])
            if narrative:
                narrative = f"[LLM-drafted; figures and authority are deterministic] {narrative}"
    rows = await case_source_rows(db, case)
    required = od["evidence"] if od else ontology.DEFAULT_EVIDENCE
    totals = await case_store_day_totals(db, case)
    etype = exceptions[0].exception_type if exceptions else None
    evidence = {requirement: evidence_for_requirement(requirement, rows, totals, etype) for requirement in required}
    provided = await provided_evidence(db, case)
    for requirement in required:
        if not evidence[requirement]:
            evidence[requirement] = provided.get(requirement.strip().lower(), [])
    # A new investigation replaces any recommendation still waiting for a decision.
    for old in (await db.execute(select(Recommendation).where(
            Recommendation.case_id == case.id, Recommendation.status == "Proposed"))).scalars().all():
        old.status = "Superseded"
    return nimbus.investigate(db, case, exceptions, evidence, narrative)


AGENT_BATCH_LIMIT = int(os.getenv("NIMBUS_AGENT_BATCH_LIMIT", "25"))


async def auto_investigate(db: AsyncSession, business_date: str, limit: int = 200) -> int:
    """Investigate newly raised cases for a batch date. Per-case failures are isolated and never fail the batch.

    The LLM agent handles the highest-exposure cases first, up to NIMBUS_AGENT_BATCH_LIMIT per batch, so an unattended
    run has bounded time and cost. The rest get the deterministic investigation and can be run by the agent on demand."""
    from models import Exception as DBException
    cases = (await db.execute(select(Case).where(
        Case.business_date == business_date, Case.status == "Open", Case.investigation_status == "Pending",
    ).order_by(Case.total_exposure.desc()).limit(limit))).scalars().all()
    done = 0
    for i, case in enumerate(cases):
        try:
            excs = (await db.execute(select(DBException).where(DBException.case_id == case.id))).scalars().all()
            if excs:
                await run_investigation(db, case, excs, use_agent=i < AGENT_BATCH_LIMIT)
                await db.commit()
                done += 1
        except Exception:
            await db.rollback()
    return done
