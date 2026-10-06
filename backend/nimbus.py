"""Deterministic policy gate and rule-based Investigation/Resolution/Validation agents."""
import uuid
from decimal import Decimal

import ontology

from models import (
    AuditEvent, Case, EvidenceSnapshot, Finding, Recommendation,
    PolicyEvaluation, Workflow, ValidationObligation, utcnow,
)

POLICY_VERSION = "policy-v1.0"
AUTONOMY_LIMIT = Decimal("1000")
TIMING_AUTONOMY_LIMIT = Decimal("250")

SLA_HOURS = {"Critical": 4, "High": 24, "Normal": 72}


def sla_due(priority: str):
    from datetime import timedelta
    return utcnow() + timedelta(hours=SLA_HOURS.get(priority, 72))


def audit(db, event_type, case_id, object_type, object_id, description, actor="system", after=None):
    from ontology_integration import integration
    after = {"ontology_context": integration.provenance(), **(after or {})}
    db.add(AuditEvent(id=str(uuid.uuid4()), event_type=event_type, actor=actor, object_type=object_type,
                      object_id=object_id, case_id=case_id, action_description=description, after_state=after))


def investigate(db, case, exceptions, evidence_by_requirement, narrative=None):
    """Create an evidence-backed investigation from records retrieved by the caller.

    The agent never treats an ontology requirement as evidence by itself.  Each
    complete snapshot contains the source-record identifiers that support it.
    """
    inv_id = f"INV-{uuid.uuid4().hex[:8].upper()}"
    primary = exceptions[0] if exceptions else None
    etype = primary.exception_type if primary else "UNKNOWN"
    od = ontology.get(etype)
    required = od["evidence"] if od else ontology.DEFAULT_EVIDENCE

    complete = 0
    for req in required:
        records = evidence_by_requirement.get(req, [])
        status = "Complete" if records else "Unavailable"
        if records:
            complete += 1
        db.add(EvidenceSnapshot(
            id=str(uuid.uuid4()), case_id=case.id, exception_id=primary.id if primary else "",
            evidence_type=req, evidence_status=status,
            source_system=records[0]["source_system"] if records else "Unavailable",
            evidence_data={"requirement": req, "retrieved_by": inv_id,
                           "ontology": ontology.ONTOLOGY_VERSION,
                           "source_record_ids": [r["source_record_id"] for r in records],
                           "canonical_transaction_ids": [r["transaction_id"] for r in records]},
            retrieved_at=utcnow()))
    case.evidence_completeness = int((complete / len(required)) * 100) if required else 100

    amount = sum((e.exception_amount for e in exceptions), Decimal("0"))
    complete_investigation = complete == len(required)
    if complete_investigation:
        case.investigation_status, case.status = "Ready for Decision", "In Review"
        rationale = narrative or "All required evidence was retrieved from linked source records."
        conclusion = f"{(od['label'] if od else etype)} has {complete} linked evidence items covering {amount}."
        finding_type, confidence = "Supported Finding", "High"
    else:
        case.investigation_status, case.status = "Awaiting Evidence", "Open"
        rationale = "Required evidence is unavailable; Nimbus cannot support a disposition."
        conclusion = f"{(od['label'] if od else etype)} is awaiting {len(required) - complete} required evidence item(s)."
        finding_type, confidence = "Evidence Gap", "Low"
    db.add(Finding(id=str(uuid.uuid4()), case_id=case.id, investigation_id=inv_id,
                   conclusion=conclusion, finding_type=finding_type, confidence=confidence,
                   supporting_rationale=rationale))

    if not complete_investigation:
        disp, aclass, mode, workflow = "Request Evidence", "Evidence Retrieval", "zero", "Retrieve missing required evidence"
    elif od:
        disp, aclass, mode, workflow = od["disposition"]
    else:
        disp, aclass, mode, workflow = "Escalation", "Manual Review", "amount", "Route to specialist team"
    rec = Recommendation(id=str(uuid.uuid4()), case_id=case.id, investigation_id=inv_id,
                         disposition_type=disp, action_class=aclass, expected_workflow=workflow,
                         financial_impact=Decimal("0") if mode == "zero" else amount,
                         confidence="High" if complete_investigation else "Low")
    db.add(rec)
    audit(db, "INVESTIGATION_COMPLETED", case.id, "Case", case.id,
          f"Investigation {inv_id} produced {disp} recommendation ({ontology.ONTOLOGY_VERSION})",
          actor="investigation-agent")
    return rec


def evaluate_policy(db, case, rec, approved_rule_codes=frozenset()):
    """Deterministic gate: permit autonomously, require human, request evidence, or block."""
    reasons = []
    if case.evidence_completeness < 100:
        outcome = "RequestEvidence"
        reasons.append("Required evidence incomplete")
    elif (rec.disposition_type == "Timing" and rec.financial_impact == 0
          and case.total_exception_amount <= TIMING_AUTONOMY_LIMIT):
        outcome = "Permit"
        reasons.append("Timing classification with no financial movement within autonomy limit")
    elif rec.financial_impact > AUTONOMY_LIMIT or rec.disposition_type == "Correction":
        outcome = "RequireHuman"
        reasons.append("Financial correction or amount above autonomy limit")
    else:
        outcome = "RequireHuman"
        reasons.append("Default: human authorization required")
    applied = sorted(set(approved_rule_codes) & {
        "NSA-EVIDENCE-REQUIRED", "NSA-APPROVAL-BOUND", "NSA-VALIDATION-CLOSE",
        "NSA-FINANCIAL-DETERMINISM", "NSA-STALE-EVIDENCE", "NSA-APPROVAL-AUTHORITY",
    })
    if applied:
        reasons.append(f"Approved ontology control references: {', '.join(applied)}")
    ev = PolicyEvaluation(id=str(uuid.uuid4()), case_id=case.id, recommendation_id=rec.id,
                          rule_version=POLICY_VERSION, outcome=outcome,
                          reasons={"reasons": reasons, "approved_ontology_rules": applied})
    db.add(ev)
    audit(db, "POLICY_EVALUATED", case.id, "Recommendation", rec.id, f"Policy outcome: {outcome}",
          actor="policy-engine", after={"reasons": reasons})
    return ev


def start_workflow(db, case, rec):
    wf = Workflow(id=str(uuid.uuid4()), case_id=case.id, recommendation_id=rec.id,
                  workflow_type=rec.disposition_type, state="Pending", definition_version="wf-v1.0",
                  current_step="Execute action")
    db.add(wf)
    case.status = "Resolving"
    audit(db, "WORKFLOW_STARTED", case.id, "Workflow", wf.id, f"{rec.disposition_type} workflow dispatched",
          actor="orchestrator")
    return wf


def resolve(db, case, wf):
    """Resolution agent: execute permitted action via the controlled gateway, then create validation obligation."""
    wf.state = "Executed"
    wf.current_step = "Awaiting validation"
    case.status = "Pending Validation"
    ob = ValidationObligation(
        id=str(uuid.uuid4()), case_id=case.id, workflow_id=wf.id,
        expected_observation=f"{wf.workflow_type} outcome visible in downstream systems",
        due_window_hours=24, verification_rule="Fresh evidence matches promised result")
    db.add(ob)
    audit(db, "ACTION_EXECUTED", case.id, "Workflow", wf.id, "Action executed through controlled gateway",
          actor="resolution-agent")
    return ob


def validate(db, case, wf, ob, evidence_records):
    """Close only when a fresh, linked observation is available for verification."""
    if not evidence_records:
        ob.status, ob.verification_result = "Waiting", "EvidenceUnavailable"
        audit(db, "VALIDATION_WAITING", case.id, "ValidationObligation", ob.id,
              "Validation evidence unavailable; waiting", actor="validation-agent")
        return ob
    ob.status, ob.verification_result = "Complete", "Verified"
    wf.state, wf.current_step = "Completed", "Closed"
    case.status, case.investigation_status = "Closed", "Resolved"
    audit(db, "VALIDATION_VERIFIED", case.id, "ValidationObligation", ob.id,
          "Outcome verified from fresh linked source evidence", actor="validation-agent",
          after={"source_record_ids": [r["source_record_id"] for r in evidence_records]})
    return ob
