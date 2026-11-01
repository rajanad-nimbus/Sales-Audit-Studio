import asyncio

import pytest
from apps.ingest import routes, service
from fastapi import HTTPException
from models import Base, CanonicalTransaction, ReconciliationMatch
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_manual_matches_scope_reviews_and_filter_before_limit():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                common = {
                    "record_type": "Sale",
                    "event_time": "2026-10-01T10:00:00Z",
                    "amount": "10",
                }
                await service.ingest(
                    db,
                    "POS",
                    "delivery",
                    [
                        {**common, "source_record_id": f"{store}-{number}", "store_id": store}
                        for store in ("S1", "S2")
                        for number in range(2)
                    ],
                )
                rows = (await db.execute(select(CanonicalTransaction))).scalars().all()
                by_store = {
                    store: [row.id for row in rows if row.store_id == store]
                    for store in ("S1", "S2")
                }
                db.add_all(
                    [
                        ReconciliationMatch(
                            id="visible",
                            transaction_ids=by_store["S1"],
                            store_id="S1",
                            match_type="Manual",
                            rationale="test",
                            proposed_by="user",
                        ),
                        *[
                            ReconciliationMatch(
                                id=f"hidden-{i:03}",
                                transaction_ids=by_store["S2"],
                                store_id="S2",
                                match_type="Manual",
                                rationale="test",
                                proposed_by="user",
                            )
                            for i in range(205)
                        ],
                        ReconciliationMatch(
                            id="missing",
                            transaction_ids=[by_store["S1"][0], "missing"],
                            match_type="Manual",
                            rationale="test",
                            proposed_by="user",
                        ),
                    ]
                )
                await db.commit()
                user = {"user": "finance", "store_scoped": True, "stores": ["S1"]}
                statements = []

                def capture_queries(
                    _connection, _cursor, statement, _parameters, _context, _executemany
                ):
                    statements.append(statement.lower())

                event.listen(engine.sync_engine, "before_cursor_execute", capture_queries)
                try:
                    listed = await routes.list_manual_matches(None, db, user)
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", capture_queries)
                assert [item["id"] for item in listed] == ["visible"]
                assert (
                    sum("from reconciliation_matches" in statement for statement in statements) == 1
                )
                assert not any(
                    "from canonical_transactions" in statement for statement in statements
                )
                assert (
                    await routes.list_manual_matches(None, db, {"store_scoped": True, "stores": []})
                    == []
                )
                proposed = await routes.propose_manual_match(
                    routes.ManualMatchRequest(
                        transaction_ids=by_store["S1"], rationale="new proposal"
                    ),
                    db,
                    user,
                )
                assert (await db.get(ReconciliationMatch, proposed["id"])).store_id == "S1"
                for operation, code in [
                    (
                        routes.propose_manual_match(
                            routes.ManualMatchRequest(
                                transaction_ids=by_store["S2"], rationale="test"
                            ),
                            db,
                            user,
                        ),
                        403,
                    ),
                    (routes.approve_manual_match("hidden-000", db, user), 403),
                    (routes.reject_manual_match("hidden-001", db, user), 403),
                    (routes.approve_manual_match("missing", db, user), 409),
                ]:
                    with pytest.raises(HTTPException) as exc:
                        await operation
                    assert exc.value.status_code == code
                assert (await routes.approve_manual_match("visible", db, user))[
                    "status"
                ] == "Approved"
                with pytest.raises(HTTPException) as already_reconciled:
                    await routes.propose_manual_match(
                        routes.ManualMatchRequest(transaction_ids=by_store["S1"], rationale="test"),
                        db,
                        user,
                    )
                assert already_reconciled.value.status_code == 409
                db.add(
                    ReconciliationMatch(
                        id="overlap",
                        transaction_ids=by_store["S1"],
                        store_id="S1",
                        match_type="Manual",
                        rationale="test",
                        proposed_by="user",
                    )
                )
                await db.commit()
                with pytest.raises(HTTPException, match="already been reconciled"):
                    await routes.approve_manual_match("overlap", db, user)
                assert (await db.get(ReconciliationMatch, "overlap")).status == "Proposed"
                with pytest.raises(HTTPException) as exc:
                    await routes.reject_manual_match("visible", db, user)
                assert exc.value.status_code == 409
        finally:
            await engine.dispose()

    asyncio.run(run())
