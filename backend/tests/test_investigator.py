"""Investigation agent: tool scoping, citation grounding and failure fallback (fake model client, SQLite)."""
import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import investigator
from agent_tools import EvidenceTools
from models import Base, Case, CanonicalTransaction, Exception as Exc, SourceRecord


def run(c):
    return asyncio.run(c)


async def seed():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with Session() as db:
        case = Case(id="c1", case_number="CASE-1", case_type="Reconciliation", business_date="2026-10-01", store_id="S1")
        db.add(case)
        ids = {}
        for store, key in (("S1", "in"), ("S2", "out")):
            sr = SourceRecord(id=str(uuid.uuid4()), source_system="POS", source_version="1", source_record_id=key,
                              delivery_id="d", payload=b'{"k":"v"}', payload_hash="h", received_at=now,
                              source_event_time=now)
            db.add(sr)
            await db.flush()
            t = CanonicalTransaction(id=str(uuid.uuid4()), business_date="2026-10-01", transaction_date=now,
                                     event_timestamp=now, processing_timestamp=now, settlement_date="2026-10-02",
                                     transaction_type="Sale", source_lineage="POS", signed_amount=Decimal("10"),
                                     store_id=store, channel_id="c", source_record_id=sr.id)
            db.add(t)
            ids[key] = (t.id, sr.id)
        exc = Exc(id="e1", case_id="c1", exception_type="UNMATCHED_SALE", exception_family="x", source_system="POS",
                  detection_origin="d", detection_rule_id="r", exception_amount=Decimal("10"),
                  estimated_exposure=Decimal("10"), severity="High", confidence="High", close_impact="None")
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
        client = FakeClient([
            [block("tool_use", id="1", name="list_transactions", input={})],
            [block("tool_use", id="2", name="submit_findings", input={
                "summary": "s", "hypotheses": [
                    {"cause": "Capture not submitted", "verdict": "supported", "cited_ids": [tid, "made-up"], "reasoning": "r"},
                    {"cause": "Processor file missing", "verdict": "supported", "cited_ids": ["made-up"], "reasoning": "r"}]})],
        ])
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
            assert "error" in await tools.call("get_source_record", {"source_record_id": ids["out"][1]})
            assert (await tools.call("get_source_record", {"source_record_id": ids["in"][1]}))["payload"]
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


def test_auto_investigate_processes_new_cases_and_isolates_scope():
    async def go():
        import investigation_service
        Session, case, exc, ids = await seed()
        async with Session() as db:
            n = await investigation_service.auto_investigate(db, "2026-10-01")
            assert n == 1
            from sqlalchemy import select
            c = (await db.execute(select(Case).where(Case.id == "c1"))).scalar_one()
            assert c.investigation_status in ("Awaiting Evidence", "Ready for Decision")
            assert await investigation_service.auto_investigate(db, "2026-10-01") == 0  # idempotent
    run(go())


def test_shadow_report_classifies_agreement():
    from routes_agent import shadow_report
    sup = {"hypotheses": [{"verdict": "supported", "cited_ids": ["x"]}]}
    unsup = {"hypotheses": [{"verdict": "supported", "cited_ids": []}]}  # ungrounded does not count
    rep = shadow_report(
        runs={"a": sup, "b": unsup, "c": sup, "d": None, "e": sup},
        decisions={"a": "Approved", "b": "Approved", "c": "Rejected", "d": "Approved"},
        types={k: "T" for k in "abcde"})
    assert rep["by_exception_type"]["T"] == {"cases": 4, "agree": 1, "missed": 1, "overconfident": 1, "agent_failed": 1}


def test_eligibility_requires_all_permit_conditions_and_grounded_finding():
    from decimal import Decimal as D
    from routes_agent import eligible
    sup = {"hypotheses": [{"verdict": "supported", "cited_ids": ["x"]}]}
    case = NS(status="In Review", evidence_completeness=100, total_exception_amount=D("100"))
    rec = NS(status="Proposed", disposition_type="Timing", financial_impact=D("0"))
    assert eligible(case, rec, sup)
    assert not eligible(case, rec, {"hypotheses": [{"verdict": "supported", "cited_ids": []}]})
    assert not eligible(NS(**{**case.__dict__, "evidence_completeness": 80}), rec, sup)
    assert not eligible(NS(**{**case.__dict__, "total_exception_amount": D("251")}), rec, sup)
    assert not eligible(case, NS(**{**rec.__dict__, "disposition_type": "Correction"}), sup)
    assert not eligible(case, NS(**{**rec.__dict__, "financial_impact": D("5")}), sup)


def test_settings_update_persists_and_is_audited_and_gates_tools():
    async def go():
        import agent_settings
        from sqlalchemy import select
        from models import AuditEvent
        Session, case, exc, ids = await seed()
        async with Session() as db:
            cfg = await agent_settings.update(db, {"model": "claude-opus-5-5", "tools": ["get_store_day_totals"]}, "root@x")
            assert cfg["model"] == "claude-opus-5-5" and cfg["tools"] == ["get_store_day_totals"]
            ev = (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_SETTING_CHANGED"))).scalars().all()
            assert {e.after_state["setting"] for e in ev} == {"model", "tools"} and all(e.actor == "root@x" for e in ev)
            assert await agent_settings.update(db, {"model": "claude-opus-5-5"}, "root@x")  # no-op adds no event
            assert len((await db.execute(select(AuditEvent).where(AuditEvent.event_type == "AGENT_SETTING_CHANGED"))).scalars().all()) == 2
            tools = EvidenceTools(db, case, set(cfg["tools"]))
            assert "disabled" in (await tools.call("list_transactions", {}))["error"]
    run(go())


def test_sdk_tools_are_scoped_and_citations_validated():
    async def go():
        Session, case, exc, ids = await seed()
        async with Session() as db:
            tools = EvidenceTools(db, case)
            trace, captured = {"tool_calls": []}, {}
            sdk_tools = investigator.build_sdk_tools(tools, investigator.TOOL_SPECS, trace, captured)
            by_name = {t.name: t for t in sdk_tools}
            assert set(by_name) == {"list_transactions", "get_source_record", "get_store_day_totals", "submit_findings"}
            listed = await by_name["list_transactions"].handler({})
            assert ids["in"][0] in listed["content"][0]["text"] and ids["out"][0] not in listed["content"][0]["text"]
            assert trace["tool_calls"][0]["tool"] == "list_transactions"
            await by_name["submit_findings"].handler({"summary": "s", "hypotheses": [
                {"cause": "A", "verdict": "supported", "cited_ids": [ids["in"][0], "fabricated-id"], "reasoning": "r"}]})
            h = captured["result"]["hypotheses"][0]
            assert h["cited_ids"] == [ids["in"][0]] and captured["result"]["rejected_citations"] == ["fabricated-id"]
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
            assert options.tools == [] and options.setting_sources == [] and options.max_budget_usd == investigator.MAX_COST_USD
            assert "mcp__nimbus__submit_findings" in options.allowed_tools
            seen["tools"].seen_ids.add(ids["in"][0])
            seen["captured"]["result"] = investigator.validate({"summary": "ok", "hypotheses": [
                {"cause": "A", "verdict": "supported", "cited_ids": [ids["in"][0]], "reasoning": "r"}]}, seen["tools"].seen_ids)
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                                session_id="s", total_cost_usd=0.02, usage={"input_tokens": 11, "output_tokens": 7})
        async with Session() as db:
            out = await investigator.run_via_sdk(db, case, [exc], "Unmatched sale", ["A"], query_fn=fake_query)
        assert out["result"]["summary"] == "ok" and out["trace"]["via"] == "claude-agent-sdk"
        assert out["trace"]["input_tokens"] == 11 and out["trace"]["output_tokens"] == 7 and out["trace"]["cost_usd"] == 0.02
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
