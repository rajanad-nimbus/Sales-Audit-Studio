"""Case evidence listing stays bounded while retaining stable page ordering."""

import asyncio
from datetime import timedelta

from apps.cases.evidence import case_evidence
from fastapi import Response
from models import Base, Case, EvidenceSnapshot, utcnow
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_case_evidence_is_paginated_with_total_headers():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                now = utcnow()
                db.add(Case(
                    id="case-1", case_number="CASE-1", case_type="Test", business_date="2026-10-01",
                    store_id="S1", status="Open",
                ))
                db.add_all([
                    EvidenceSnapshot(
                        id=f"evidence-{index}", case_id="case-1", exception_id="exception-1",
                        evidence_type="Source", evidence_status="Complete", source_system="POS",
                        evidence_data={"source_record_ids": [f"source-{index}"]},
                        retrieved_at=now + timedelta(seconds=index),
                    )
                    for index in range(3)
                ])
                await db.commit()

                first_response = Response()
                first_page = await case_evidence(
                    "case-1", db, {"role": "finance", "store_scoped": False},
                    first_response, skip=0, limit=2,
                )
                second_response = Response()
                second_page = await case_evidence(
                    "case-1", db, {"role": "finance", "store_scoped": False},
                    second_response, skip=2, limit=2,
                )

                assert [row["id"] for row in first_page] == ["evidence-2", "evidence-1"]
                assert [row["id"] for row in second_page] == ["evidence-0"]
                assert first_response.headers["X-Total-Count"] == second_response.headers["X-Total-Count"] == "3"
                assert first_response.headers["X-Has-More"] == "true"
                assert second_response.headers["X-Has-More"] == "false"
        finally:
            await engine.dispose()

    asyncio.run(run())
