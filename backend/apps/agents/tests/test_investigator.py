"""Investigation agent: tool scoping, citation grounding and failure fallback (fake model client, SQLite)."""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace as NS

from apps.agents import investigator
from apps.agents.agent_tools import EvidenceTools
from models import (
    AuditEvent,
    Base,
    CanonicalTransaction,
    Case,
    EvidenceSnapshot,
    HumanDecision,
    Recommendation,
    SourceRecord,
)
from models import (
    Exception as Exc,
)
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def run(c):
    return asyncio.run(c)


async def seed():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with Session() as db:
        case = Case(
            id="c1",
            case_number="CASE-1",
            case_type="Reconciliation",
            business_date="2026-10-01",
            store_id="S1",
        )
        db.add(case)
        ids = {}
        for store, key in (("S1", "in"), ("S2", "out")):
            sr = SourceRecord(
                id=str(uuid.uuid4()),
                source_system="POS",
                source_version="1",
                source_record_id=key,
                delivery_id="d",
                payload=b'{"k":"v"}',
                payload_hash="h",
                received_at=now,
                source_event_time=now,
            )
            db.add(sr)
            await db.flush()
            t = CanonicalTransaction(
                id=str(uuid.uuid4()),
                business_date="2026-10-01",
                transaction_date=now,
                event_timestamp=now,
                processing_timestamp=now,
                settlement_date="2026-10-02",
                transaction_type="Sale",
                source_lineage="POS",
                signed_amount=Decimal("10"),
                store_id=store,
                channel_id="c",
                source_record_id=sr.id,
            )
            db.add(t)
            ids[key] = (t.id, sr.id)
        exc = Exc(
            id="e1",
            case_id="c1",
            exception_type="UNMATCHED_SALE",
            exception_family="x",
            source_system="POS",
            detection_origin="d",
            detection_rule_id="r",
            exception_amount=Decimal("10"),
            estimated_exposure=Decimal("10"),
            severity="High",
            confidence="High",
            close_impact="None",
        )
        db.add(exc)
        await db.commit()
    return Session, case, exc, ids


def block(type_, **kw):
    return NS(type=type_, **kw)


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.messages = NS(create=self.create)

    async def create(self, **_):
        return NS(content=self.scripted.pop(0), usage=NS(input_tokens=5, output_tokens=3))


def test_grounded_citation_kept_and_fabricated_rejected():
    async def go():
        Session, case, exc, ids = await seed()
        tid, _ = ids["in"]
        client = FakeClient(
            [
                [block("tool_use", id="1", name="list_transactions", input={})],
                [
                    block(
                        "tool_use",
                        id="2",
                        name="submit_findings",
                        input={
                            "summary": "s",
                            "hypotheses": [
                                {
                                    "cause": "Capture not submitted",
                                    "verdict": "supported",
                                    "cited_ids": [tid, "made-up"],
                                    "reasoning": "r",
                                },
                                {
                                    "cause": "Processor file missing",
                                    "verdict": "supported",
                                    "cited_ids": ["made-up"],
                                    "reasoning": "r",
                                },
                            ],
                        },
                    )
                ],
            ]
        )
        async with Session() as db:
            out = await investigator.run(db, case, [exc], "Unmatched Sale", ["a"], client=client)
        h = out["result"]["hypotheses"]
        assert h[0]["cited_ids"] == [tid] and h[0]["verdict"] == "supported"
        assert h[1]["verdict"] == "inconclusive"
        assert out["result"]["rejected_citations"] == ["made-up", "made-up"]
        assert out["trace"]["tool_calls"][0]["tool"] == "list_transactions"

    run(go())


def test_tools_are_scoped_to_case_store():
    async def go():
        Session, case, exc, ids = await seed()
        async with Session() as db:
            tools = EvidenceTools(db, case)
            listed = await tools.call("list_transactions", {})
            assert [t["transaction_id"] for t in listed["transactions"]] == [ids["in"][0]]
            assert "error" in await tools.call(
                "get_source_record", {"source_record_id": ids["out"][1]}
            )
            assert (await tools.call("get_source_record", {"source_record_id": ids["in"][1]}))[
                "payload"
            ]

    run(go())


def test_failure_returns_trace_without_result():
    async def go():
        Session, case, exc, _ = await seed()

        async def boom(**_):
            raise RuntimeError("x")

        client = NS(messages=NS(create=boom))
        async with Session() as db:
            out = await investigator.run(db, case, [exc], "l", [], client=client)
        assert out["result"] is None and out["trace"]["error"] == "RuntimeError"

    run(go())


def test_missing_optional_agent_dependency_returns_fallback(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    result = run(investigator.run_via_sdk(None, None, [], "label", [], query_fn=object()))

    assert result["result"] is None
    assert result["trace"]["error"] == "SDK unavailable"


def test_auto_investigate_processes_new_cases_and_isolates_scope():
    async def go():
        from apps.agents import investigation_service

        Session, case, exc, ids = await seed()
        async with Session() as db:
            n = await investigation_service.auto_investigate(db, "2026-10-01")
            assert n == 1
            from sqlalchemy import select

            c = (await db.execute(select(Case).where(Case.id == "c1"))).scalar_one()
            assert c.investigation_status in ("Awaiting Evidence", "Ready for Decision")
            assert await investigation_service.auto_investigate(db, "2026-10-01") == 0  # idempotent

    run(go())


def test_auto_investigate_continues_after_case_rollback(monkeypatch):
    from apps.agents import investigation_service

    async def go():
        Session, _, _, _ = await seed()
        async with Session() as db:
            db.add(
                Case(
                    id="c2",
                    case_number="CASE-2",
                    case_type="Reconciliation",
                    business_date="2026-10-01",
                    store_id="S1",
                )
            )
            db.add(
                Exc(
                    id="e2",
                    case_id="c2",
                    exception_type="UNMATCHED_SALE",
                    exception_family="x",
                    source_system="POS",
                    detection_origin="d",
                    detection_rule_id="r",
                    exception_amount=Decimal("10"),
                    estimated_exposure=Decimal("10"),
                    severity="High",
                    confidence="High",
                    close_impact="None",
                )
            )
            await db.commit()
            visited = []

            async def investigate(_db, case, _exceptions, use_agent=True, evidence_cache=None):
                visited.append(case.id)
                if len(visited) == 1:
                    await evidence_cache.source_rows(case, ["Source record"])
                    assert evidence_cache._source_rows
                    raise RuntimeError("first case failed")
                assert evidence_cache._source_rows == {}
                await evidence_cache.source_rows(case, ["Source record"])
                case.investigation_status = "Awaiting Evidence"

            monkeypatch.setattr(investigation_service, "run_investigation", investigate)
            assert await investigation_service.auto_investigate(db, "2026-10-01") == 1
            assert len(visited) == 2

    run(go())


def test_investigation_evidence_cache_reuses_store_day_query():
    from apps.agents.investigation_service import InvestigationEvidenceCache

    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            statements = []

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def record_statement(
                _connection, _cursor, statement, _parameters, _context, _executemany
            ):
                if "FROM canonical_transactions" in statement:
                    statements.append(statement)

            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                cache = InvestigationEvidenceCache(db)
                first_case = NS(store_id="S1", business_date="2026-10-01")
                same_store_day = NS(store_id="S1", business_date="2026-10-01")

                first_rows = await cache.source_rows(first_case, ["Source record"])
                second_rows = await cache.source_rows(same_store_day, ["Source record"])

                assert first_rows == second_rows
                assert first_rows.records == ()
                assert len(statements) == 1
        finally:
            await engine.dispose()

    run(go())


def test_investigation_source_reads_are_capped_per_evidence_scope(monkeypatch):
    from apps.agents import investigation_service

    async def go():
        Session, case, _, _ = await seed()
        async with Session() as db:
            now = datetime.now(UTC)
            for index in range(5):
                source = "POS" if index < 2 else "Processor"
                transaction_type = "Sale" if source == "POS" else "Refund"
                source_record = SourceRecord(
                    id=f"bounded-source-{index}",
                    source_system=source,
                    source_version="1",
                    source_record_id=f"bounded-{index}",
                    delivery_id=f"bounded-delivery-{index}",
                    payload=b"{}",
                    payload_hash=f"hash-{index}",
                    received_at=now,
                    source_event_time=now,
                )
                db.add(source_record)
                db.add(
                    CanonicalTransaction(
                        id=f"bounded-transaction-{index}",
                        business_date=case.business_date,
                        transaction_date=now,
                        event_timestamp=now,
                        processing_timestamp=now,
                        settlement_date=case.business_date,
                        transaction_type=transaction_type,
                        source_lineage=source,
                        signed_amount=Decimal("1"),
                        store_id=case.store_id,
                        channel_id="test",
                        source_record_id=source_record.id,
                    )
                )
            await db.commit()

            monkeypatch.setattr(investigation_service, "MAX_SOURCE_EVIDENCE_ROWS_PER_SCOPE", 2)
            rows = await investigation_service.case_source_rows(
                db,
                case,
                ["Source record", "Refund confirmation"],
            )
            pos_evidence = investigation_service.evidence_for_requirement("Source record", rows)
            refund_evidence = investigation_service.evidence_for_requirement(
                "Refund confirmation", rows
            )

            assert (
                len(rows.records) == 4
            )  # two POS and two Processor refunds; no cross-scope starvation
            assert len(pos_evidence) == len(refund_evidence) == 2
            assert all(record["truncated"] for record in (*pos_evidence, *refund_evidence))

            from integrations.ontology import nimbus

            requirements = ["POS journal", "Processor capture status", "Authorization record"]
            evidence = {
                requirement: investigation_service.evidence_for_requirement(
                    requirement,
                    rows,
                    exception_type="UNMATCHED_SALE",
                )
                for requirement in requirements
            }
            nimbus.investigate(
                db,
                case,
                [
                    Exc(
                        id="primary",
                        exception_type="UNMATCHED_SALE",
                        exception_amount=Decimal("10"),
                    )
                ],
                evidence,
            )
            await db.flush()
            snapshot = (
                await db.execute(
                    select(EvidenceSnapshot).where(
                        EvidenceSnapshot.case_id == case.id,
                        EvidenceSnapshot.evidence_type == "POS journal",
                    )
                )
            ).scalar_one()
            assert snapshot.evidence_data["source_records_truncated"] is True

    run(go())


def test_investigation_source_query_honors_declared_evidence_sources_and_types():
    from apps.agents.investigation_service import case_source_rows

    async def go():
        Session, case, _, _ = await seed()
        async with Session() as db:
            now = datetime.now(UTC)
            inputs = [
                ("shopify", "Shopify", "Order"),
                ("processor-refund", "Processor", "Refund"),
                ("processor-sale", "Processor", "Sale"),
            ]
            for name, source, transaction_type in inputs:
                source_record = SourceRecord(
                    id=f"source-{name}",
                    source_system=source,
                    source_version="1",
                    source_record_id=name,
                    delivery_id=f"delivery-{name}",
                    payload=b"{}",
                    payload_hash="h",
                    received_at=now,
                    source_event_time=now,
                )
                db.add(source_record)
                db.add(
                    CanonicalTransaction(
                        id=f"transaction-{name}",
                        business_date=case.business_date,
                        transaction_date=now,
                        event_timestamp=now,
                        processing_timestamp=now,
                        settlement_date=case.business_date,
                        transaction_type=transaction_type,
                        source_lineage=source,
                        signed_amount=Decimal("1"),
                        store_id=case.store_id,
                        channel_id="test",
                        source_record_id=source_record.id,
                    )
                )
            await db.commit()

            source_rows = await case_source_rows(db, case, ["Source record"])
            refund_rows = await case_source_rows(db, case, ["Refund confirmation"])
            lineage_rows = await case_source_rows(db, case, ["Source transaction lineage"])

            assert {record.source_system for _, record in source_rows} == {"POS"}
            assert [
                (txn.transaction_type, record.source_system) for txn, record in refund_rows
            ] == [("Refund", "Processor")]
            assert {record.source_system for _, record in lineage_rows} == {
                "POS",
                "Shopify",
                "Processor",
            }

    run(go())


def test_shadow_report_classifies_agreement():
    from apps.agents.routes import shadow_report

    sup = {"hypotheses": [{"verdict": "supported", "cited_ids": ["x"]}]}
    unsup = {"hypotheses": [{"verdict": "supported", "cited_ids": []}]}  # ungrounded does not count
    rep = shadow_report(
        runs={"a": sup, "b": unsup, "c": sup, "d": None, "e": sup},
        decisions={"a": "Approved", "b": "Approved", "c": "Rejected", "d": "Approved"},
        types={k: "T" for k in "abcde"},
    )
    assert rep["by_exception_type"]["T"] == {
        "cases": 4,
        "agree": 1,
        "missed": 1,
        "overconfident": 1,
        "agent_failed": 1,
    }


def test_shadow_endpoint_streams_latest_run_and_decision_per_case():
    async def go():
        from apps.agents.routes import shadow

        Session, _case, _exc, _ids = await seed()
        now = datetime.now(UTC)
        async with Session() as db:
            db.add(
                Recommendation(
                    id="recommendation-shadow",
                    case_id="c1",
                    investigation_id="investigation-shadow",
                    disposition_type="Timing",
                    action_class="test",
                    expected_workflow="review",
                    financial_impact=Decimal("0"),
                    confidence="High",
                )
            )
            db.add(
                Case(
                    id="c2",
                    case_number="CASE-2",
                    case_type="Reconciliation",
                    business_date="2026-10-01",
                    store_id="S2",
                )
            )
            db.add_all(
                [
                    AuditEvent(
                        id="run-old",
                        event_type="AGENT_RUN",
                        actor="investigation-agent",
                        object_type="Case",
                        object_id="c1",
                        case_id="c1",
                        action_description="Old run",
                        after_state={"result": None},
                        created_at=now.replace(year=now.year - 1),
                    ),
                    AuditEvent(
                        id="run-new",
                        event_type="AGENT_RUN",
                        actor="investigation-agent",
                        object_type="Case",
                        object_id="c1",
                        case_id="c1",
                        action_description="Latest run",
                        after_state={
                            "result": {
                                "hypotheses": [{"verdict": "supported", "cited_ids": ["source"]}]
                            }
                        },
                        created_at=now,
                    ),
                    HumanDecision(
                        id="decision-old",
                        case_id="c1",
                        recommendation_id="recommendation-shadow",
                        decision_type="Approval",
                        decision="Rejected",
                        actor="finance-old",
                        authority_check="Authorized",
                        created_at=now.replace(year=now.year - 1),
                    ),
                    HumanDecision(
                        id="decision-new",
                        case_id="c1",
                        recommendation_id="recommendation-shadow",
                        decision_type="Approval",
                        decision="Approved",
                        actor="finance-new",
                        authority_check="Authorized",
                        created_at=now,
                    ),
                    Exc(
                        id="e2",
                        case_id="c2",
                        exception_type="UNMATCHED_SALE",
                        exception_family="Reconciliation",
                        source_system="POS",
                        detection_origin="Test",
                        detection_rule_id="TEST-UNMATCHED-SALE",
                        exception_amount=Decimal("10"),
                        estimated_exposure=Decimal("10"),
                        severity="Medium",
                        confidence="High",
                        close_impact="Yes",
                    ),
                    Recommendation(
                        id="recommendation-shadow-c2",
                        case_id="c2",
                        investigation_id="investigation-shadow-c2",
                        disposition_type="Timing",
                        action_class="test",
                        expected_workflow="review",
                        financial_impact=Decimal("0"),
                        confidence="High",
                    ),
                    AuditEvent(
                        id="run-c2",
                        event_type="AGENT_RUN",
                        actor="investigation-agent",
                        object_type="Case",
                        object_id="c2",
                        case_id="c2",
                        action_description="Cross-store run",
                        after_state={"result": None},
                        created_at=now,
                    ),
                    HumanDecision(
                        id="decision-c2",
                        case_id="c2",
                        recommendation_id="recommendation-shadow-c2",
                        decision_type="Approval",
                        decision="Rejected",
                        actor="finance-other-store",
                        authority_check="Authorized",
                        created_at=now,
                    ),
                ]
            )
            await db.commit()

            report = await shadow(
                db,
                {
                    "role": "finance",
                    "store_scoped": True,
                    "stores": ["S1"],
                },
            )

            assert report["total"] == {
                "cases": 1,
                "agree": 1,
                "missed": 0,
                "overconfident": 0,
                "agent_failed": 0,
            }
            assert report["by_exception_type"]["UNMATCHED_SALE"]["cases"] == 1

    run(go())


def test_agent_supported_ignores_malformed_result_shapes():
    from apps.agents.routes import agent_supported

    assert not agent_supported(["unexpected"])
    assert not agent_supported({"hypotheses": [None, "unexpected"]})


def test_eligible_endpoint_returns_bounded_highest_exposure_cases(monkeypatch):
    async def go():
        from apps.agents import routes

        Session, _case, _exc, _ids = await seed()
        now = datetime.now(UTC)
        monkeypatch.setattr(routes, "MAX_ELIGIBLE_CASES", 1)
        async with Session() as db:
            first = await db.get(Case, "c1")
            first.status = "In Review"
            first.evidence_completeness = 100
            first.total_exception_amount = Decimal("40")
            first.total_exposure = Decimal("100")
            first.currency = "USD"
            db.add(
                Case(
                    id="c2",
                    case_number="CASE-2",
                    case_type="Timing",
                    business_date="2026-10-01",
                    store_id="S1",
                    status="In Review",
                    evidence_completeness=100,
                    total_exception_amount=Decimal("30"),
                    total_exposure=Decimal("200"),
                    currency="USD",
                )
            )
            for case_id in ("c1", "c2"):
                db.add(
                    Recommendation(
                        id=f"recommendation-{case_id}",
                        case_id=case_id,
                        investigation_id=f"investigation-{case_id}",
                        disposition_type="Timing",
                        action_class="test",
                        expected_workflow="review",
                        financial_impact=Decimal("0"),
                        confidence="High",
                    )
                )
                db.add(
                    AuditEvent(
                        id=f"run-{case_id}",
                        event_type="AGENT_RUN",
                        actor="investigation-agent",
                        object_type="Case",
                        object_id=case_id,
                        case_id=case_id,
                        action_description="Agent supported a grounded timing finding",
                        after_state={
                            "result": {
                                "hypotheses": [{"verdict": "supported", "cited_ids": ["source"]}]
                            }
                        },
                        created_at=now,
                    )
                )
            await db.commit()

            result = await routes.eligible_cases(db, {"role": "finance"})

            assert result["count"] == 2
            assert result["truncated"] is True
            assert [case["case_id"] for case in result["cases"]] == ["c2"]

    run(go())


def test_eligibility_requires_all_permit_conditions_and_grounded_finding():
    from decimal import Decimal as D

    from apps.agents.routes import eligible

    sup = {"hypotheses": [{"verdict": "supported", "cited_ids": ["x"]}]}
    case = NS(
        status="In Review",
        evidence_completeness=100,
        total_exception_amount=D("100"),
        currency="USD",
    )
    rec = NS(status="Proposed", disposition_type="Timing", financial_impact=D("0"))
    assert eligible(case, rec, sup)
    assert not eligible(case, rec, {"hypotheses": [{"verdict": "supported", "cited_ids": []}]})
    assert not eligible(NS(**{**case.__dict__, "evidence_completeness": 80}), rec, sup)
    assert not eligible(NS(**{**case.__dict__, "total_exception_amount": D("251")}), rec, sup)
    assert not eligible(NS(**{**case.__dict__, "currency": "EUR"}), rec, sup)
    assert not eligible(NS(**{**case.__dict__, "currency": None}), rec, sup)
    assert not eligible(case, NS(**{**rec.__dict__, "disposition_type": "Correction"}), sup)
    assert not eligible(case, NS(**{**rec.__dict__, "financial_impact": D("5")}), sup)


def test_settings_update_persists_and_is_audited_and_gates_tools():
    async def go():
        from apps.agents import agent_settings
        from models import AuditEvent
        from sqlalchemy import select

        Session, case, exc, ids = await seed()
        async with Session() as db:
            cfg = await agent_settings.update(
                db, {"model": "claude-opus-5-5", "tools": ["get_store_day_totals"]}, "root@x"
            )
            assert cfg["model"] == "claude-opus-5-5" and cfg["tools"] == ["get_store_day_totals"]
            ev = (
                (
                    await db.execute(
                        select(AuditEvent).where(AuditEvent.event_type == "AGENT_SETTING_CHANGED")
                    )
                )
                .scalars()
                .all()
            )
            assert {e.after_state["setting"] for e in ev} == {"model", "tools"} and all(
                e.actor == "root@x" for e in ev
            )
            assert await agent_settings.update(
                db, {"model": "claude-opus-5-5"}, "root@x"
            )  # no-op adds no event
            assert (
                len(
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.event_type == "AGENT_SETTING_CHANGED"
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                == 2
            )
            tools = EvidenceTools(db, case, set(cfg["tools"]))
            assert "disabled" in (await tools.call("list_transactions", {}))["error"]

    run(go())


def test_sdk_tools_are_scoped_and_citations_validated():
    async def go():
        Session, case, exc, ids = await seed()
        async with Session() as db:
            tools = EvidenceTools(db, case)
            trace, captured = {"tool_calls": []}, {}
            sdk_tools = investigator.build_sdk_tools(
                tools, investigator.TOOL_SPECS, trace, captured
            )
            by_name = {t.name: t for t in sdk_tools}
            assert set(by_name) == {
                "list_transactions",
                "get_source_record",
                "get_store_day_totals",
                "lookup_ontology",
                "submit_findings",
            }
            listed = await by_name["list_transactions"].handler({})
            assert (
                ids["in"][0] in listed["content"][0]["text"]
                and ids["out"][0] not in listed["content"][0]["text"]
            )
            assert trace["tool_calls"][0]["tool"] == "list_transactions"
            await by_name["submit_findings"].handler(
                {
                    "summary": "s",
                    "hypotheses": [
                        {
                            "cause": "A",
                            "verdict": "supported",
                            "cited_ids": [ids["in"][0], "fabricated-id"],
                            "reasoning": "r",
                        }
                    ],
                }
            )
            h = captured["result"]["hypotheses"][0]
            assert h["cited_ids"] == [ids["in"][0]] and captured["result"][
                "rejected_citations"
            ] == ["fabricated-id"]

    run(go())


def test_run_via_sdk_returns_validated_findings_and_usage(monkeypatch):
    from claude_agent_sdk import ResultMessage

    async def go():
        Session, case, exc, ids = await seed()
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")
        seen = {}
        real_build = investigator.build_sdk_tools

        def spy(tools, specs, trace, captured):
            seen.update(tools=tools, captured=captured)
            return real_build(tools, specs, trace, captured)

        monkeypatch.setattr(investigator, "build_sdk_tools", spy)

        async def fake_query(prompt, options):
            assert (
                options.tools == []
                and options.setting_sources == []
                and options.max_budget_usd == investigator.MAX_COST_USD
            )
            assert "mcp__nimbus__submit_findings" in options.allowed_tools
            seen["tools"].seen_ids.add(ids["in"][0])
            seen["captured"]["result"] = investigator.validate(
                {
                    "summary": "ok",
                    "hypotheses": [
                        {
                            "cause": "A",
                            "verdict": "supported",
                            "cited_ids": [ids["in"][0]],
                            "reasoning": "r",
                        }
                    ],
                },
                seen["tools"].seen_ids,
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=2,
                session_id="s",
                total_cost_usd=0.02,
                usage={"input_tokens": 11, "output_tokens": 7},
            )

        async with Session() as db:
            out = await investigator.run_via_sdk(
                db, case, [exc], "Unmatched sale", ["A"], query_fn=fake_query
            )
        assert out["result"]["summary"] == "ok" and out["trace"]["via"] == "claude-agent-sdk"
        assert (
            out["trace"]["input_tokens"] == 11
            and out["trace"]["output_tokens"] == 7
            and out["trace"]["cost_usd"] == 0.02
        )

    run(go())


def test_run_via_sdk_failure_returns_trace_without_result(monkeypatch):
    async def go():
        Session, case, exc, ids = await seed()
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-token")

        async def boom(prompt, options):
            raise RuntimeError("cli failed")
            yield

        async with Session() as db:
            out = await investigator.run_via_sdk(db, case, [exc], "x", ["A"], query_fn=boom)
        assert out["result"] is None and out["trace"]["error"] == "RuntimeError"

    run(go())
