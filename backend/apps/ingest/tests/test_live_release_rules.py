"""Replay the POS scenario matrix with the rules of the deployed ontology release (id 20).

`fixtures/live_release_20.json` is a copy of that release's audit controls. The standard audit errors
must be unchanged by them; the only extra codes are those the live rules are written to raise.
"""

import asyncio
import json
import uuid
from collections import Counter
from pathlib import Path

import pytest
from apps.controls.foundation.models import FoundationReference
from apps.ingest import reconciliation, scenarios, service
from integrations.ontology.audit_controls import parse_controls
from integrations.ontology.ontology_integration import integration
from models import Base, SalesAuditErrorDefinition
from models import Exception as DBException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

FIXTURES = Path(__file__).parent / "fixtures"
RELEASES = {}  # release id -> controls, as read from the deployed ontology
for _release in (20, 21):
    _live = json.loads((FIXTURES / f"live_release_{_release}.json").read_text())
    RELEASES[_release] = parse_controls(_live["release"], _live["rows"])
RULE_CODES = sorted(
    r["config"]["error_code"]
    for r in json.loads((FIXTURES / "live_release_20.json").read_text())["rows"]
    if "error_code" in r["config"]
)
ALL = scenarios.scenarios()


def everything_raised(scenario) -> Counter:
    async def go():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for code in RULE_CODES:  # the real database seeds these in a migration
                db.add(
                    SalesAuditErrorDefinition(
                        code=code, description=code, short_description=code,
                        form="Transaction Detail", tab="Item", recommended_solution="Review.",
                    )
                )
            for code in scenario.catalog:
                db.add(FoundationReference(id=str(uuid.uuid4()), category="Item", code=code, name=code, active=True))
            await service.ingest(db, "POS", "pos", scenario.pos)
            if scenario.processor:
                await service.ingest(db, "Processor", "processor", scenario.processor)
            await reconciliation.reconcile(db)
            counts = Counter((await db.execute(select(DBException.exception_type))).scalars().all())
        await engine.dispose()
        return counts

    return asyncio.run(go())


@pytest.fixture(autouse=True, params=[20, 21], ids=["release20", "release21"])
def live_release(request, monkeypatch):
    controls = RELEASES[request.param]
    monkeypatch.setattr(integration, "audit_controls", lambda: controls)
    return request.param


def test_the_live_release_defines_what_the_deployment_was_seen_to_have(live_release):
    controls = integration.audit_controls()
    assert len(controls.transaction_rules) == 9
    assert len(controls.total_rules) == (52 if live_release == 21 else 0)
    assert len(controls.levels) == (12 if live_release == 21 else 0)
    assert controls.limits["autonomy_limit"] and controls.limits["timing_autonomy_limit"]


@pytest.mark.parametrize("scenario", ALL, ids=[s.name for s in ALL])
def test_live_rules_leave_the_standard_audit_errors_unchanged(scenario):
    raised = everything_raised(scenario)
    assert set(raised) & scenarios.AUDIT_ERRORS == scenario.expect
    # Beyond those, only what the live rules raise: the matrix data carries no transaction number,
    # and POS-only days have no Shopify orders (the order-presence rule names no location trait).
    allowed = {"TRAN_NUMBER_REQ", "NO_ORDERS_DAY"}
    if "non-usd" in scenario.name:
        allowed.add("CURRENCY_MISMATCH")
    assert set(raised) - scenarios.AUDIT_ERRORS <= allowed


def test_the_order_presence_rule_is_not_limited_to_stores_that_take_web_orders():
    """Finding: NSA-TXN-DAY-NO-ORDERS has no location traits, so POS-only store days always trip it."""
    rule = next(r for r in integration.audit_controls().transaction_rules if r.error_code == "NO_ORDERS_DAY")
    assert rule.location_traits == ()
    assert everything_raised(ALL[0])["NO_ORDERS_DAY"] == 1


# ---- each live rule on the records it targets --------------------------------------------------------
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from apps.ingest.audit_rules import condition_matches  # noqa: E402


def txn(**fields):
    base = dict(
        transaction_type="Order", tender_type="Card", detail_status="Complete",
        signed_amount=Decimal("100"), quantity=Decimal("1"), transaction_number="T1",
        return_reason="Damaged", currency="USD", source_lineage="shopify-map-v1",
    )
    return SimpleNamespace(**{**base, **fields})


def fires(code, transaction) -> bool:
    [rule] = [r for r in integration.audit_controls().transaction_rules if r.error_code == code]
    if rule.record_type and transaction.transaction_type != rule.record_type:
        return False
    return all(condition_matches(c, transaction) for c in rule.conditions)


@pytest.mark.parametrize(
    ("code", "bad", "good"),
    [
        ("ORDER_NO_TENDER", dict(tender_type=None), dict(tender_type="Card")),
        ("ORDER_NO_LINES", dict(detail_status="No detail"), dict(detail_status="Complete")),
        ("ORDER_NONPOSITIVE_AMOUNT", dict(signed_amount=Decimal("0")), dict(signed_amount=Decimal("0.01"))),
        ("ORDER_NONPOSITIVE_AMOUNT", dict(signed_amount=Decimal("-5")), dict(signed_amount=Decimal("5"))),
        ("ORDER_QTY_INVALID", dict(quantity=Decimal("0")), dict(quantity=Decimal("1"))),
        ("HIGH_VALUE_ORDER", dict(signed_amount=Decimal("5000.01")), dict(signed_amount=Decimal("5000"))),
        ("TRAN_NUMBER_REQ", dict(transaction_number=None), dict(transaction_number="T1")),
        ("REFUND_REASON_REQ", dict(transaction_type="Refund", return_reason=None),
         dict(transaction_type="Refund", return_reason="Damaged")),
        ("CURRENCY_MISMATCH", dict(currency="CAD"), dict(currency="USD")),
    ],
)
def test_each_live_rule_fires_on_its_bad_record_and_not_on_a_good_one(code, bad, good):
    assert fires(code, txn(**bad)) and not fires(code, txn(**good))


def test_order_rules_ignore_other_record_types_and_the_refund_rule_ignores_orders():
    assert not fires("ORDER_NO_TENDER", txn(transaction_type="Sale", tender_type=None))
    assert not fires("REFUND_REASON_REQ", txn(transaction_type="Order", return_reason=None))


# ---- what release 21 adds: the agent levels and the $10 over/short rules --------------------------------
from apps.store_days.rule_evaluation import rule_breaches  # noqa: E402
from apps.totals.client_totals import CLIENT_TOTALS  # noqa: E402

NEW = RELEASES[21]


def test_release_21_holds_the_agent_levels_in_the_ontology_and_they_match_the_design():
    assert NEW.level_for("DUPLICATE_SALES") == NEW.level_for("SHOPIFY_DUPLICATE_ORDER") == "act"
    assert NEW.level_for("MISSING_REFUND") == "collect"
    propose = {"TIMING_DIFFERENCE", "AMOUNT_MISMATCH", "UNMATCHED_SALE", "ORPHAN_PAYMENT", "BANK_VARIANCE",
               "MISSING_DEPOSIT", "GL_VARIANCE", "MISSING_GL_POSTING", "DISC_REASON_REQ"}
    assert {code for code, level in NEW.levels.items() if level == "propose"} == propose
    assert NEW.level_for("A_CLASS_NOT_LISTED") == "collect"


def test_release_21_has_a_ten_dollar_rule_for_every_workbook_difference_total():
    names = {t["name"] for t in CLIENT_TOTALS}
    differences = {n for n in names if n.endswith("O") and n[:-1] + "P" in names}
    assert {r.total_name for r in NEW.total_rules} == {f"Configured: {n}" for n in differences}
    assert all(
        (r.comparison, r.threshold, r.threshold_currency, r.balance_level)
        == ("ABS_GT", Decimal("10"), "USD", "Store")
        for r in NEW.total_rules
    )


@pytest.mark.parametrize(
    ("difference", "breach"),
    [("0", False), ("10", False), ("-10", False), ("10.01", True), ("-10.01", True)],
)
def test_every_release_21_over_short_rule_breaches_only_beyond_ten_dollars(difference, breach):
    assert all(rule_breaches(Decimal(difference), r.threshold, r.comparison) is breach for r in NEW.total_rules)
