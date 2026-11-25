from decimal import Decimal
from types import SimpleNamespace as NS

from integrations.ontology import nimbus, ontology


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def _investigate(exception_type, level, monkeypatch):
    monkeypatch.setattr(nimbus, "agent_level", lambda code: level)
    monkeypatch.setattr(nimbus, "audit", lambda *args, **kwargs: None)
    od = ontology.get(exception_type)
    evidence = {
        req: [{"source_system": "POS", "source_record_id": "r", "transaction_id": "t"}]
        for req in od["evidence"]
    }
    case = NS(id="c", evidence_completeness=0, investigation_status="", status="")
    exception = NS(id="e", exception_type=exception_type, exception_amount=Decimal("25"))
    rec = nimbus.investigate(FakeDb(), case, [exception], evidence)
    return rec


def test_a_collect_class_hands_off_to_a_person_without_a_resolution(monkeypatch):
    rec = _investigate("MISSING_REFUND", "collect", monkeypatch)
    assert (rec.disposition_type, rec.action_class) == ("Escalation", "Manual Review")


def test_a_propose_class_keeps_its_own_recommendation(monkeypatch):
    rec = _investigate("MISSING_REFUND", "propose", monkeypatch)
    assert rec.disposition_type == ontology.get("MISSING_REFUND")["disposition"][0]
    assert rec.action_class != "Manual Review"
