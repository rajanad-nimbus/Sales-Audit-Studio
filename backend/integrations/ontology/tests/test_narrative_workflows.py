"""Narrative workflows through investigate() and the policy gate, with the narratives' figures."""

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from integrations.ontology import nimbus, ontology


class FakeDb:
    def add(self, row):
        pass


def work(exception_type, amount, *, complete, monkeypatch):
    monkeypatch.setattr(nimbus, "audit", lambda *args, **kwargs: None)
    needed = ontology.get(exception_type)["evidence"]
    given = needed if complete else needed[:1]
    evidence = {
        req: [{"source_system": "POS", "source_record_id": "r", "transaction_id": "t"}]
        for req in given
    }
    case = NS(
        id="c",
        evidence_completeness=0,
        investigation_status="",
        status="",
        total_exception_amount=Decimal(amount),
        currency="USD",
    )
    exception = NS(id="e", exception_type=exception_type, exception_amount=Decimal(amount))
    db = FakeDb()
    rec = nimbus.investigate(db, case, [exception], evidence)
    case.evidence_completeness = 100 if complete else 60
    return case, rec, nimbus.evaluate_policy(db, case, rec)


def test_scenario_1_timing_difference_above_the_limit_goes_to_a_person(monkeypatch):
    _, rec, policy = work("TIMING_DIFFERENCE", "550", complete=True, monkeypatch=monkeypatch)
    assert rec.disposition_type == "Timing"
    assert policy.outcome == "RequireHuman"


def test_scenario_1_timing_difference_within_the_limit_is_permitted(monkeypatch):
    _, rec, policy = work("TIMING_DIFFERENCE", "100", complete=True, monkeypatch=monkeypatch)
    assert rec.disposition_type == "Timing"
    assert policy.outcome == "Permit"


def test_scenario_4_return_without_refund_confirmation_asks_for_evidence(monkeypatch):
    _, _, policy = work("MISSING_REFUND", "94.22", complete=False, monkeypatch=monkeypatch)
    assert policy.outcome == "RequestEvidence"


@pytest.mark.parametrize("etype", ["MISSING_REFUND", "TIMING_DIFFERENCE"])
def test_complete_evidence_never_asks_for_more(etype, monkeypatch):
    _, _, policy = work(etype, "94.22", complete=True, monkeypatch=monkeypatch)
    assert policy.outcome != "RequestEvidence"
