"""Shared investigation entry point: scoped evidence retrieval, optional agent, deterministic findings."""
import logging
import os
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import agent_settings, investigator, llm
from integrations.ontology import nimbus, ontology
from models import AuditTotal, Case, CanonicalTransaction, EvidenceRequest, Recommendation, SourceRecord, StoreDay

logger = logging.getLogger(__name__)
MAX_SOURCE_EVIDENCE_ROWS_PER_SCOPE = 100


@dataclass(frozen=True, slots=True)
class CaseSourceRows:
    """Bounded source evidence plus scopes whose matching records were sampled."""

    records: tuple[tuple[CanonicalTransaction, SourceRecord], ...]
    truncated_scopes: frozenset[tuple[str | None, str | None]] = frozenset()
    truncated_any: bool = False

    def __iter__(self):
        return iter(self.records)


def evidence_for_requirement(requirement: str, rows: list[tuple[CanonicalTransaction, SourceRecord]] | CaseSourceRows,
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
    records = rows.records if isinstance(rows, CaseSourceRows) else rows
    selected = []
    for txn, record in records:
        if spec.get("source") and record.source_system != spec["source"]:
            continue
        if spec.get("type") and txn.transaction_type != spec["type"]:
            continue
        truncated = isinstance(rows, CaseSourceRows) and (
            rows.truncated_any
            or (record.source_system, spec.get("type")) in rows.truncated_scopes
            or (record.source_system, None) in rows.truncated_scopes
        )
        selected.append({"source_record_id": record.id, "transaction_id": txn.id,
                         "source_system": record.source_system, "truncated": truncated})
    return selected


async def case_source_rows(
    db: AsyncSession, case: Case, requirements: list[str]
) -> CaseSourceRows:
    scopes: set[tuple[str | None, str | None]] = set()
    for requirement in requirements:
        spec = ontology.EVIDENCE_SOURCES.get(requirement)
        if not spec or spec.get("totals") or spec.get("ontology"):
            continue
        if spec.get("any"):
            scopes.add((None, None))
        elif spec.get("source") or spec.get("type"):
            scopes.add((spec.get("source"), spec.get("type")))
    if not scopes:
        return CaseSourceRows(())

    records_by_transaction: dict[str, tuple[CanonicalTransaction, SourceRecord]] = {}
    truncated_scopes: set[tuple[str | None, str | None]] = set()
    truncated_any = False
    for source, transaction_type in sorted(scopes, key=lambda item: (item[0] or "", item[1] or "")):
        query = select(CanonicalTransaction, SourceRecord).join(
            SourceRecord, CanonicalTransaction.source_record_id == SourceRecord.id,
        ).where(
            CanonicalTransaction.business_date == case.business_date,
            CanonicalTransaction.store_id == case.store_id,
        )
        if source is not None:
            query = query.where(SourceRecord.source_system == source)
        if transaction_type is not None:
            query = query.where(CanonicalTransaction.transaction_type == transaction_type)
        rows = (await db.execute(
            query.order_by(CanonicalTransaction.event_timestamp, CanonicalTransaction.id)
            .limit(MAX_SOURCE_EVIDENCE_ROWS_PER_SCOPE + 1)
        )).all()
        truncated = len(rows) > MAX_SOURCE_EVIDENCE_ROWS_PER_SCOPE
        if truncated:
            if source is None and transaction_type is None:
                truncated_any = True
            else:
                truncated_scopes.add((source, transaction_type))
        for transaction, record in rows[:MAX_SOURCE_EVIDENCE_ROWS_PER_SCOPE]:
            records_by_transaction[transaction.id] = (transaction, record)

    ordered_records = tuple(sorted(
        records_by_transaction.values(),
        key=lambda pair: (pair[0].event_timestamp, pair[0].id),
    ))
    return CaseSourceRows(ordered_records, frozenset(truncated_scopes), truncated_any)


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


class InvestigationEvidenceCache:
    """Reuse immutable store-day evidence within one batch or bulk operation."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._source_rows: dict[
            tuple[str, str, tuple[str, ...]], CaseSourceRows
        ] = {}
        self._store_day_totals: dict[tuple[str, str], list[AuditTotal]] = {}

    def clear(self) -> None:
        """Discard ORM instances after a transaction rollback expires them."""
        self._source_rows.clear()
        self._store_day_totals.clear()

    @staticmethod
    def _key(case: Case) -> tuple[str, str]:
        return case.store_id, case.business_date

    async def source_rows(
        self, case: Case, requirements: list[str]
    ) -> CaseSourceRows:
        key = (*self._key(case), tuple(sorted(set(requirements))))
        if key not in self._source_rows:
            self._source_rows[key] = await case_source_rows(self.db, case, requirements)
        return self._source_rows[key]

    async def store_day_totals(self, case: Case) -> list[AuditTotal]:
        key = self._key(case)
        if key not in self._store_day_totals:
            self._store_day_totals[key] = await case_store_day_totals(self.db, case)
        return self._store_day_totals[key]


async def run_investigation(
    db: AsyncSession,
    case: Case,
    exceptions,
    use_agent: bool = True,
    evidence_cache: InvestigationEvidenceCache | None = None,
) -> object:
    case = (await db.execute(select(Case).where(Case.id == case.id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one()
    if case.amount_needs_review:
        raise HTTPException(status_code=409, detail="Case amounts require finance review before investigation")
    from integrations.ontology.ontology_integration import integration
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
    evidence_cache = evidence_cache or InvestigationEvidenceCache(db)
    required = od["evidence"] if od else ontology.DEFAULT_EVIDENCE
    rows = await evidence_cache.source_rows(case, required)
    totals = await evidence_cache.store_day_totals(case)
    etype = exceptions[0].exception_type if exceptions else None
    evidence = {requirement: evidence_for_requirement(requirement, rows, totals, etype) for requirement in required}
    provided = await provided_evidence(db, case)
    for requirement in required:
        if not evidence[requirement]:
            evidence[requirement] = provided.get(requirement.strip().lower(), [])
    # Serialize investigations and all other proposal writers on the case row.
    # The partial unique index is the final guard, while this lock ensures the
    # old proposal is superseded before the replacement is inserted.
    case = (await db.execute(select(Case).where(Case.id == case.id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one()
    for old in (await db.execute(select(Recommendation).where(
            Recommendation.case_id == case.id, Recommendation.status == "Proposed").with_for_update())).scalars().all():
        old.status = "Superseded"
    return nimbus.investigate(db, case, exceptions, evidence, narrative)


AGENT_BATCH_LIMIT = int(os.getenv("NIMBUS_AGENT_BATCH_LIMIT", "25"))


async def auto_investigate(db: AsyncSession, business_date: str, limit: int = 200) -> int:
    """Investigate newly raised cases for a batch date. Per-case failures are isolated and never fail the batch.

    The LLM agent handles the highest-exposure cases first, up to NIMBUS_AGENT_BATCH_LIMIT per batch, so an unattended
    run has bounded time and cost. The rest get the deterministic investigation and can be run by the agent on demand."""
    from models import Exception as DBException
    case_ids = (await db.execute(select(Case.id).where(
        Case.business_date == business_date, Case.status == "Open", Case.investigation_status == "Pending",
        Case.amount_needs_review.is_(False),
    ).order_by(Case.total_exposure.desc()).limit(limit))).scalars().all()
    done = 0
    evidence_cache = InvestigationEvidenceCache(db)
    for i, case_id in enumerate(case_ids):
        try:
            case = await db.get(Case, case_id)
            if case is None:
                continue
            excs = (await db.execute(select(DBException).where(DBException.case_id == case_id))).scalars().all()
            if excs:
                await run_investigation(
                    db, case, excs, use_agent=i < AGENT_BATCH_LIMIT, evidence_cache=evidence_cache
                )
                await db.commit()
                done += 1
        except Exception:
            logger.exception("Automatic investigation failed for case %s", case_id)
            await db.rollback()
            evidence_cache.clear()
    return done
