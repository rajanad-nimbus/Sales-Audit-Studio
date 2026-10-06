"""Extend the existing Sales Audit draft; run inside the Ontology Studio backend.

Administrative maintenance for an explicitly requested draft update. No identity
impersonation, approval, credentials, live grants, or financial data are created.
Default is rollback-only preview. --apply commits one audited transaction.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml
from backend.database import (SessionLocal, Workspace, Entity, Property, Relationship,
                              Persona, Consumer, ProcessDefinition, BusinessRule, AuditLog)
from backend.services import agent_policy, ontology_doc, process_actors, persona_scope

ALIASES = {"Case": "SalesAuditCase", "Exception": "FinancialException", "Recommendation": "ResolutionRecommendation"}
# code, name, runtime role/actor, kind, responsibilities, process codes, entity scope
PERSONAS = [
 ("NSA-FINANCE", "Sales Audit Finance Approver", "finance", "human", "Review evidence and policy outcomes; authorize or reject financial recommendations, corrections and close requests within current authority. Approval is bound to the reviewed evidence and recommendation.", ["APPROVAL", "CORRECTION", "CLOSE", "EXPORT"], ["SalesAuditCase", "ResolutionRecommendation", "HumanDecision", "PolicyEvaluation", "ApprovalMatrix", "EvidenceSnapshot", "TransactionAdjustment", "StoreDay", "StoreDayClosureRequest", "ExportBatch", "AuditEvent"]),
 ("NSA-ANALYST", "Sales Audit Analyst", "analyst", "human", "Investigate exceptions, request and provide evidence, document findings and escalate gaps. Shared operational routes are available; Finance-only approval routes are not.", ["INVESTIGATION", "EVIDENCE"], ["SalesAuditCase", "FinancialException", "CanonicalTransaction", "SourceRecord", "EvidenceSnapshot", "EvidenceRequest", "Finding", "CaseNote", "ResolutionRecommendation", "AuditTotal"]),
 ("NSA-AUDITOR", "Sales Audit Auditor", "auditor", "human", "Read case history, approvals, policy results and hash-chain audit evidence. No operational writes or approvals.", ["AUDIT"], ["NimbusSalesAudit"]),
 ("NSA-IT", "Sales Audit IT Operator", "it", "human", "Operate ingestion, batch runs, connectors and export recovery. Diagnose technical failures. No Finance-only approval authority.", ["INGESTION", "RECONCILIATION", "EXPORT"], ["Connector", "SourceFeedProfile", "SourceRecord", "BatchRun", "ExportBatch", "ExportItem", "ExportDestination", "AuditEvent"]),
 ("NSA-STORE-MANAGER", "Store Cash Reviewer", "store_manager", "human", "Review store-day information and maintain cash controls through permitted routes. Runtime store assignments are not implemented: this role remains impersonation-only; do not issue production store-scoped access.", ["CASH"], ["CashControl", "CashDeposit", "StoreDay"]),
 ("NSA-ADMIN", "Sales Audit Configuration Administrator", "admin", "human", "Manage agent settings and technical configuration, inspect control changes, and review deployment readiness. This persona definition does not assign any person an admin role.", ["CONFIGURATION"], ["AgentSetting", "ConfiguredAuditRule", "AuditTotalDefinition", "ReconciliationPolicy", "RetentionPolicy", "AuditEvent"]),
 ("NSA-RECONCILIATION", "Reconciliation Engine", "reconciliation-engine", "system", "Perform deterministic matching and control checks; open cases and exceptions. Cannot approve, execute corrections or close cases.", ["RECONCILIATION"], ["CanonicalTransaction", "SourceRecord", "FinancialException", "SalesAuditCase", "ReconciliationMatch", "ReconciliationPolicy", "AuditTotal", "ConfiguredAuditRule", "AuditEvent"]),
 ("NSA-INVESTIGATOR", "Investigation Agent", "investigation-agent", "system", "Use allow-listed read-only evidence tools within the case store and business date. Test candidate causes and cite only retrieved identifiers. Cannot set financial authority, approve recommendations or execute actions.", ["INVESTIGATION", "EVIDENCE"], ["SalesAuditCase", "FinancialException", "CanonicalTransaction", "SourceRecord", "AuditTotal", "EvidenceSnapshot", "Finding", "ResolutionRecommendation", "AuditEvent"]),
 ("NSA-POLICY", "Policy Engine", "policy-engine", "system", "Evaluate evidence completeness, financial impact and configured authority deterministically. Return RequestEvidence, RequireHuman or Permit. Permit alone never executes an action.", ["POLICY"], ["SalesAuditCase", "EvidenceSnapshot", "ResolutionRecommendation", "PolicyEvaluation", "ApprovalMatrix", "AuditEvent"]),
 ("NSA-ORCHESTRATOR", "Resolution Orchestrator", "orchestrator", "system", "Dispatch the workflow for a valid approved recommendation. Cannot create human approval or bypass a failed policy gate.", ["DISPATCH"], ["SalesAuditCase", "ResolutionRecommendation", "HumanDecision", "PolicyEvaluation", "Workflow", "AuditEvent"]),
 ("NSA-RESOLVER", "Resolution Agent", "resolution-agent", "system", "Advance an approved workflow and open its validation obligation. External action connectors are not implemented; an executed local workflow does not prove downstream financial completion.", ["RESOLUTION"], ["SalesAuditCase", "Workflow", "ResolutionRecommendation", "HumanDecision", "ValidationObligation", "ActionAttempt", "AuditEvent"]),
 ("NSA-VALIDATOR", "Validation Agent", "validation-agent", "system", "Check fresh linked source evidence after the action. Close only when the deterministic validation obligation is satisfied; otherwise wait or escalate.", ["VALIDATION"], ["SalesAuditCase", "Workflow", "ValidationObligation", "SourceRecord", "CanonicalTransaction", "EvidenceSnapshot", "AuditEvent"]),
 ("NSA-INGESTOR", "Source Ingestion Service", "ingestion service", "system", "Preserve source payload and identity; normalize accepted transactions and quarantine invalid records. No approval or financial correction authority.", ["INGESTION"], ["SourceRecord", "CanonicalTransaction", "Connector", "SourceFeedProfile", "BatchRun", "AuditEvent"]),
 ("NSA-EXPORTER", "Sales Audit Export Service", "export service", "system", "Build record-level export manifests, track deliveries and acknowledgments, and preserve retries and backposting lineage. Only approved and eligible work is exported.", ["EXPORT"], ["ExportDestination", "ExportBatch", "ExportItem", "CanonicalTransaction", "StoreDay", "AuditEvent"]),
 ("NSA-ASSISTANT", "Nimbus Read-only Assistant", "assistant", "system", "Explain scoped live case data and ontology definitions with citations. Cannot change records, approve decisions, execute workflows or claim draft definitions are approved.", ["ASSISTANCE"], ["SalesAuditCase", "FinancialException", "EvidenceSnapshot", "Finding", "ResolutionRecommendation", "ValidationObligation"]),
]
# suffix, label, target, ordered (actor, step) pairs
PROCESSES = [
 ("INGESTION", "Receive and normalize source evidence", "SourceRecord", [("NSA-IT", "Check connector and source-feed configuration"), ("NSA-INGESTOR", "Preserve payload, delivery identity and source hash"), ("NSA-INGESTOR", "Validate schema, sign, currency and deduplication; normalize or quarantine")]),
 ("RECONCILIATION", "Reconcile sales and detect exceptions", "FinancialException", [("NSA-RECONCILIATION", "Evaluate completeness and configured control totals"), ("NSA-RECONCILIATION", "Match POS, processor, bank and ERP evidence deterministically"), ("NSA-RECONCILIATION", "Open exceptions and cases for unresolved breaks")]),
 ("INVESTIGATION", "Investigate a sales audit case", "SalesAuditCase", [("NSA-ANALYST", "Review the case business scope and required evidence"), ("NSA-INVESTIGATOR", "Retrieve scoped evidence using permitted tools"), ("NSA-INVESTIGATOR", "Test causes with grounded citations; record gaps and contradictions"), ("NSA-ANALYST", "Review the finding and proposed disposition")]),
 ("EVIDENCE", "Fulfil missing evidence requirements", "EvidenceRequest", [("NSA-ANALYST", "Request the missing evidence and record its source"), ("NSA-ANALYST", "Provide or reject evidence with references"), ("NSA-INVESTIGATOR", "Reassess completeness using linked evidence")]),
 ("POLICY", "Evaluate disposition policy", "PolicyEvaluation", [("NSA-POLICY", "Check evidence completeness and deterministic financial impact"), ("NSA-POLICY", "Record policy outcome and ontology release provenance")]),
 ("APPROVAL", "Authorize the reviewed recommendation", "HumanDecision", [("NSA-FINANCE", "Review recommendation, evidence and policy result"), ("NSA-FINANCE", "Approve, reject or escalate using current authority; record the decision")]),
 ("DISPATCH", "Dispatch an approved resolution", "Workflow", [("NSA-ORCHESTRATOR", "Verify current approval and policy conditions"), ("NSA-ORCHESTRATOR", "Create the linked resolution workflow")]),
 ("RESOLUTION", "Execute approved local resolution workflow", "Workflow", [("NSA-RESOLVER", "Advance the approved workflow without claiming unimplemented external actions"), ("NSA-RESOLVER", "Create a downstream validation obligation")]),
 ("VALIDATION", "Verify outcome and close the case", "ValidationObligation", [("NSA-VALIDATOR", "Retrieve fresh source evidence linked to the action"), ("NSA-VALIDATOR", "Validate deterministically; close when satisfied or remain waiting")]),
 ("CORRECTION", "Govern financial transaction corrections", "TransactionAdjustment", [("NSA-FINANCE", "Review source lineage, adjustment rationale and financial impact"), ("NSA-FINANCE", "Authorize the controlled adjustment and preserve its audit record")]),
 ("CASH", "Reconcile store cash controls", "CashControl", [("NSA-STORE-MANAGER", "Record permitted cash count and deposit evidence"), ("NSA-FINANCE", "Review cash variances and unresolved differences")]),
 ("CLOSE", "Review and close the store business day", "StoreDayClosureRequest", [("NSA-FINANCE", "Review completeness, exceptions and outstanding validation obligations"), ("NSA-FINANCE", "Approve or reject close request and record rationale")]),
 ("EXPORT", "Export eligible sales audit records", "ExportBatch", [("NSA-FINANCE", "Review posting eligibility and accounting period"), ("NSA-EXPORTER", "Build a record manifest and deliver to the configured destination"), ("NSA-IT", "Review acknowledgment or retry failure with preserved record lineage")]),
 ("AUDIT", "Inspect audit lineage and integrity", "AuditEvent", [("NSA-AUDITOR", "Follow case, evidence, decision, policy and workflow links"), ("NSA-AUDITOR", "Verify hash-chain integrity and export supporting audit records")]),
 ("CONFIGURATION", "Govern agent and control configuration", "AgentSetting", [("NSA-ADMIN", "Review proposed configuration within supported options"), ("NSA-ADMIN", "Apply permitted settings and preserve change history")]),
 ("ASSISTANCE", "Explain current case information", "SalesAuditCase", [("NSA-ASSISTANT", "Read scoped case facts and cite available evidence"), ("NSA-ASSISTANT", "Explain gaps and next steps without executing changes")]),
]
POLICIES = [
 ("NSA-AGENT-SCOPE", "Agents remain inside assigned scope", "Evidence retrieval is restricted to the case store and business date and to the consumer persona. An ontology persona does not by itself implement row authorization in Nimbus."),
 ("NSA-AGENT-CITATIONS", "Findings require retrieved citations", "Agents may cite only record identifiers returned by permitted evidence tools. Unsupported hypotheses remain inconclusive."),
 ("NSA-AGENT-AUTHORITY", "Agents cannot grant financial authority", "Model output never determines financial arithmetic, approval authority, disposition permissions or close eligibility. Deterministic gates and authorized human decisions control these outcomes."),
 ("NSA-AGENT-TOOL-BOUNDARY", "Use only declared agent tools", "Investigators use allow-listed read-only tools within bounded turns, time and spend. They cannot run arbitrary queries or external write actions."),
 ("NSA-AGENT-APPROVAL", "Keep approval separate from execution", "The investigator, policy engine, orchestrator and resolver cannot create human approval. Material changes to reviewed evidence or recommendations require reassessment."),
 ("NSA-AGENT-PROVENANCE", "Preserve execution and ontology provenance", "Record actor, case, object, outcome and applicable ontology release identity in append-only audit history. Model run traces retain tools, usage, citations and failures; never record credentials."),
 ("NSA-AGENT-FAILURE", "Fail safely when context or tools are unavailable", "Invalid or unavailable approved ontology context is reported explicitly. Agent failures fall back to deterministic evidence checks and never fabricate evidence or successful downstream execution."),
 ("NSA-AGENT-VALIDATION", "Fresh evidence precedes closure", "A local workflow state is not proof of financial completion. Validate fresh linked source evidence after the action before closing the case."),
]


def apply(db, workspace_id, schema):
    workspace = db.query(Workspace).filter_by(id=workspace_id).with_for_update().one()
    if workspace.name != "Nimbus Sales Audit" or workspace.status != "draft" or workspace.deletedAt:
        raise ValueError("Only the existing editable Nimbus Sales Audit draft may be extended")
    before = ontology_doc.export_doc(db, workspace_id)
    entities = {e.name: e for e in db.query(Entity).filter_by(workspaceId=workspace_id)}
    area = entities["NimbusSalesAudit"]
    changed = {k: [] for k in ("entities", "properties", "relationships", "personas", "consumers", "processes", "policies")}
    by_table = {m["table"]: ALIASES.get(m["model"], m["model"]) for m in schema}
    for model in schema:
        name = ALIASES.get(model["model"], model["model"])
        if name not in entities:
            entity = Entity(workspaceId=workspace_id, parentEntityId=area.id, name=name,
                            label=re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name), entityType="fact",
                            domain="sales_audit", description=(model["description"] or name) +
                            f" Runtime persistence: Nimbus {model['table']}; metadata only, no warehouse connection granted.",
                            primaryKey="id", grainKeys="", status="draft")
            db.add(entity); db.flush(); entities[name] = entity; changed["entities"].append(name)
        entity = entities[name]
        known = {p.name for p in db.query(Property).filter_by(entityId=entity.id)}
        for column in model["columns"]:
            prop = entity.primaryKey if column["primary_key"] else column["name"]
            if prop in known:
                continue
            sql_type = column["type"].upper()
            dtype = ("decimal" if "NUMERIC" in sql_type else "integer" if "INT" in sql_type else
                     "datetime" if "TIME" in sql_type else "boolean" if "BOOL" in sql_type else
                     "json" if "JSON" in sql_type else "string")
            db.add(Property(entityId=entity.id, name=prop, dataType=dtype,
                            isPrimaryKey=column["primary_key"], isNullable=column["nullable"],
                            description=f"Runtime field {model['table']}.{column['name']}. " +
                            ("References " + ", ".join(column["references"]) if column["references"] else ""),
                            sensitivity="internal"))
            changed["properties"].append(f"{name}.{prop}")
    db.flush()
    for model in schema:
        source = entities[ALIASES.get(model["model"], model["model"])]
        for column in model["columns"]:
            for ref in column["references"]:
                target = entities[by_table[ref.split('.')[0]]]
                label = "references via " + column["name"]
                if db.query(Relationship).filter_by(workspaceId=workspace_id, fromEntityId=source.id,
                                                    toEntityId=target.id, name=label).first():
                    continue
                db.add(Relationship(workspaceId=workspace_id, fromEntityId=source.id, toEntityId=target.id,
                                    name=label, label=label, cardinality="N:1", type="structural", status="draft",
                                    description=f"Verified Nimbus foreign key: {model['table']}.{column['name']} -> {ref}."))
                changed["relationships"].append(f"{source.name}.{column['name']} -> {target.name}")
    parent = db.query(ProcessDefinition).filter_by(workspaceId=workspace_id, code="NSA-CASE-LIFECYCLE").one()
    processes = {}
    for suffix, name, target, steps in PROCESSES:
        code = "NSA-" + suffix
        process = db.query(ProcessDefinition).filter_by(workspaceId=workspace_id, code=code).first()
        if not process:
            doc = {"process": {"id": code, "name": name, "type": "multi_level", "trigger": "event",
                    "steps": [{"id": f"step-{i+1}", "name": label, "actor": actor,
                               "type": "data.read" if suffix in {"AUDIT", "ASSISTANCE", "INVESTIGATION"} else "manual",
                               "entities": [target], "description": label}
                              for i, (actor, label) in enumerate(steps)]}}
            process = ProcessDefinition(workspaceId=workspace_id, code=code, name=name, version="0.2.0",
                        description="Operational responsibility model derived from Nimbus. Not an executable action contract.",
                        triggerType="event", yamlDefinition=yaml.safe_dump(doc, sort_keys=False), stepsCount=len(steps),
                        status="draft", linkedEntities=target, targetEntityId=entities[target].id,
                        parentProcessId=parent.id, rrlLevel="Level 3: Operational Workflow", rrlCode=code,
                        retryPolicy="none", failurePolicy="stop")
            db.add(process); db.flush(); changed["processes"].append(code)
        processes[code] = process
    for code, name, runtime, kind, duties, suffixes, scope in PERSONAS:
        scope = sorted(set(entities if "NimbusSalesAudit" in scope else scope) | {
            target for _, _, target, steps in PROCESSES if any(actor == code for actor, _ in steps)})
        if any(e not in entities for e in scope):
            raise ValueError(f"Unknown persona entity scope: {code}")
        existing = db.query(Persona).filter_by(workspaceId=workspace_id, code=code).first()
        if not existing:
            db.add(Persona(workspaceId=workspace_id, code=code, name=name, kind=kind, status="draft",
                          description=f"Nimbus runtime role/actor: {runtime}.", responsibilities=duties,
                          typicalAuthorizedActionsJson=json.dumps(["read"]), entityScopeJson=json.dumps(scope),
                          processScopeJson=json.dumps(["NSA-" + s for s in suffixes]),
                          accessClassesJson="[]", rowLimitsJson="[]",
                          basis="Derived from Nimbus source and deployed schema. Draft responsibility definition; no user membership or runtime grant is created."))
            changed["personas"].append(code)
        elif (existing.status == "draft" and existing.basis and existing.basis.startswith("Derived from Nimbus source")
              and (json.loads(existing.typicalAuthorizedActionsJson or "[]") == [duties])):
            # Repair the initial extension's descriptive actions into supported Studio permissions.
            existing.typicalAuthorizedActionsJson = json.dumps(["read"])
            existing.entityScopeJson = json.dumps(scope)
            changed["personas"].append(code)
        if kind == "system" and not db.query(Consumer).filter_by(workspaceId=workspace_id, name=name).first():
            db.add(Consumer(workspaceId=workspace_id, kind="agent", name=name, persona=code,
                            status="disabled", assignments="{}", actsForUsers=False))
            changed["consumers"].append(name)
    for code, name, statement in POLICIES:
        if not db.query(BusinessRule).filter_by(workspaceId=workspace_id, ruleCode=code).first():
            config = agent_policy.validate_config({"appliesTo": ["all"], "enforcement": "guidance"})
            db.add(BusinessRule(workspaceId=workspace_id, ruleCode=code, name=name, expression=statement,
                               statement=statement, description="Draft agent behavior; runtime enforcement remains in Nimbus code.",
                               entityId=entities["AuditEvent"].id, category="policy", severity="critical",
                               ruleType="agent_policy", source="manual", status="draft", config=config))
            changed["policies"].append(code)
    db.flush()
    for persona in db.query(Persona).filter_by(workspaceId=workspace_id):
        if persona.code not in {p[0] for p in PERSONAS}:
            continue
        actions = json.loads(persona.typicalAuthorizedActionsJson or "[]")
        if not actions or not set(actions) <= set(persona_scope.ACTIONS):
            raise ValueError(f"Invalid Studio actions: {persona.code}")
        scope = set(json.loads(persona.entityScopeJson or "[]"))
        required = {target for _, _, target, steps in PROCESSES if any(a == persona.code for a, _ in steps)}
        if not required <= scope:
            raise ValueError(f"Process entity outside persona scope: {persona.code}")
    # Ensure the platform can derive persona/process relationships from these steps.
    participation = process_actors.performed_in(db.query(ProcessDefinition).filter_by(workspaceId=workspace_id))
    for code, *_ in PERSONAS:
        if not participation.get(code.lower()):
            raise ValueError(f"Persona has no performed process: {code}")
    if any(changed.values()):
        db.add(AuditLog(workspaceId=workspace_id, eventType="SALES_AUDIT_DRAFT_EXTENDED", entityType="workspace",
                        entityId=workspace_id, userName="Codex administrative maintenance (user requested)",
                        action="Extended Sales Audit draft with runtime schema, personas and process responsibilities",
                        details={"changes": changed, "beforeHash": hashlib.sha256(json.dumps(before, sort_keys=True, default=str).encode()).hexdigest(),
                                 "approvalChanged": False, "credentialsIssued": False}))
    return before, changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", required=True)
    parser.add_argument("--workspace", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    schema = json.loads(Path(args.schema).read_text())
    with SessionLocal() as db:
        before, changes = apply(db, args.workspace, schema)
        if args.apply:
            backup = Path(args.backup)
            # Never overwrite the original snapshot on an idempotent rerun.
            with backup.open("x") as out:
                json.dump(before, out, indent=2, default=str)
            db.commit()
        else:
            db.rollback()
        print(json.dumps({"applied": args.apply, "changes": {k: len(v) for k,v in changes.items()}, "details": changes}, indent=2))


if __name__ == "__main__":
    main()
