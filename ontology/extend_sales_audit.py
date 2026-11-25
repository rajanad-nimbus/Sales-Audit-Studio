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
from backend.services import agent_policy, audit_controls, ontology_doc, process_actors, persona_scope

ALIASES = {"Case": "SalesAuditCase", "Exception": "FinancialException", "Recommendation": "ResolutionRecommendation"}
# code, name, runtime role/actor, kind, responsibilities, process codes, entity scope
PERSONAS = [
 ("NSA-FINANCE", "Sales Audit Finance Approver", "finance", "human", "Review evidence and policy outcomes; authorize or reject financial recommendations, corrections and close requests within current authority. Approval is bound to the reviewed evidence and recommendation.", ["APPROVAL", "CORRECTION", "CLOSE", "EXPORT"], ["SalesAuditCase", "ResolutionRecommendation", "HumanDecision", "PolicyEvaluation", "ApprovalMatrix", "EvidenceSnapshot", "TransactionAdjustment", "StoreDay", "StoreDayClosureRequest", "ExportBatch", "AuditEvent"]),
 ("NSA-ANALYST", "Sales Audit Analyst", "analyst", "human", "Investigate exceptions, request and provide evidence, document findings and escalate gaps. Shared operational routes are available; Finance-only approval routes are not. Scoped to a local portfolio of stores or location traits, and sees no stores without one.", ["INVESTIGATION", "EVIDENCE"], ["SalesAuditCase", "FinancialException", "CanonicalTransaction", "SourceRecord", "EvidenceSnapshot", "EvidenceRequest", "Finding", "CaseNote", "ResolutionRecommendation", "AuditTotal"]),
 ("NSA-AUDITOR", "Sales Audit Auditor", "auditor", "human", "Read case history, approvals, policy results and hash-chain audit evidence. No operational writes or approvals.", ["AUDIT"], ["NimbusSalesAudit"]),
 ("NSA-IT", "Sales Audit IT Operator", "it", "human", "Operate ingestion, batch runs, connectors and export recovery. Diagnose technical failures. No Finance-only approval authority.", ["INGESTION", "RECONCILIATION", "EXPORT"], ["Connector", "SourceFeedProfile", "SourceRecord", "BatchRun", "ExportBatch", "ExportItem", "ExportDestination", "AuditEvent"]),
 ("NSA-STORE-MANAGER", "Store Cash Reviewer", "store_manager", "human", "Review store-day information and maintain cash controls through permitted routes. Retired as a runtime role (ADR 0001): store managers and store staff are no longer assignable. Store-scoped access is held by the Sales Auditor (analyst) through a local portfolio; do not issue this role.", ["CASH"], ["CashControl", "CashDeposit", "StoreDay"]),
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
# code, name, statement, limit, amount: the autonomy limits Nimbus reads from an approved release. They are
# created as drafts at the values Nimbus previously hard-coded; a data steward approves or changes them.
AUDIT_LIMITS = [
 ("NSA-LIMIT-AUTONOMY", "Autonomy limit", "A recommendation whose financial impact exceeds this amount always needs a human decision.", "autonomy_limit", "1000"),
 ("NSA-LIMIT-TIMING-AUTONOMY", "Timing autonomy limit", "A no-movement timing classification may be permitted without a human only up to this exposure.", "timing_autonomy_limit", "250"),
]
# The ReSA workbook's over/short difference totals (calculated minus declared). Each gets a draft rule that
# raises a case when the absolute difference exceeds the limit, so a person reviews it before the day closes.
OVER_SHORT_LIMIT = "10"  # USD
OVER_SHORT_TOTALS = [
 "AGNCYVOUO", "AMAZONO", "AMEXO", "BCOLLPLCCO", "BEALLSCCO", "BLSPRDEXPO", "CASHO", "CASHTOTALO", "CHECKO",
 "CRPCHKREQO", "CTSYMTRSLO", "CTSYSTPSLO", "CTSYSTREPO", "CUSTGDWLO", "DISCOVERO", "DISP_SUPO", "DONATCOLLO",
 "DONATEO", "EBAYO", "FCLTYRPRO", "GFTCRDSLO", "GFTWRPSLO", "GIFTCRDUO", "HURCANDONO", "INVENEXPEO",
 "MASTERCDO", "MCVISADBTO", "MISC_EXPO", "MISC_INCO", "OCOLLPLCCO", "OUTLTCCO", "PAY_ADVO", "PAYADPYBKO",
 "PAYPALO", "POSTAGEO", "POSTAGEXPO", "PROEXPLCCO", "PROPSTEXPO", "REMSLESCLO", "SALESCLO", "SHIPAMZNO",
 "SHIPEBAYO", "SHIPWLMTO", "SLSTAXACCO", "SLSTXAMZNO", "SLSTXEBAYO", "SOSPSTEXPO", "STRTRAVELO", "VIRTGCO",
 "VISAO", "WALMARTO", "MCRCASHEDO",
]
# code, name, failure class, agent level: how far the agent may go on each class. Nimbus falls back to
# the same defaults for any class without a rule, so these only make the defaults visible and editable.
AUDIT_LEVELS = [
 ("NSA-LEVEL-DUPLICATE-SALES", "Exact duplicate sale", "DUPLICATE_SALES", "act"),
 ("NSA-LEVEL-SHOPIFY-DUPLICATE-ORDER", "Shopify exact duplicate order", "SHOPIFY_DUPLICATE_ORDER", "act"),
 ("NSA-LEVEL-TIMING-DIFFERENCE", "Timing difference", "TIMING_DIFFERENCE", "propose"),
 ("NSA-LEVEL-AMOUNT-MISMATCH", "Amount mismatch", "AMOUNT_MISMATCH", "propose"),
 ("NSA-LEVEL-UNMATCHED-SALE", "Sale without payment", "UNMATCHED_SALE", "propose"),
 ("NSA-LEVEL-ORPHAN-PAYMENT", "Payment without sale", "ORPHAN_PAYMENT", "propose"),
 ("NSA-LEVEL-BANK-VARIANCE", "Bank variance", "BANK_VARIANCE", "propose"),
 ("NSA-LEVEL-MISSING-DEPOSIT", "Missing deposit", "MISSING_DEPOSIT", "propose"),
 ("NSA-LEVEL-GL-VARIANCE", "GL variance", "GL_VARIANCE", "propose"),
 ("NSA-LEVEL-MISSING-GL-POSTING", "Missing GL posting", "MISSING_GL_POSTING", "propose"),
 ("NSA-LEVEL-DISC-REASON-REQ", "Discount missing a reason", "DISC_REASON_REQ", "propose"),
 ("NSA-LEVEL-MISSING-REFUND", "Return without refund", "MISSING_REFUND", "collect"),
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
    # The approved ontology is named "Nimbus Sales Audit"; a draft started from it is "Nimbus Sales Audit: <what it changes>".
    if not workspace.name.startswith("Nimbus Sales Audit") or workspace.status != "draft" or workspace.deletedAt:
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
        # Consumers belong to the approved ontology; a draft carries only the release content.
        if kind == "system" and workspace.kind != "draft" and not db.query(Consumer).filter_by(workspaceId=workspace_id, name=name).first():
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
    changed.setdefault("audit_controls", [])
    for code, name, statement, limit, amount in AUDIT_LIMITS:
        if not db.query(BusinessRule).filter_by(workspaceId=workspace_id, ruleCode=code).first():
            config = audit_controls.validate_config({"family": "policy_limit", "limit": limit, "amount": amount, "currency": "USD"})
            db.add(BusinessRule(workspaceId=workspace_id, ruleCode=code, name=name, expression=statement,
                               statement=statement, description="Sales Audit policy limit read by Nimbus from an approved release.",
                               entityId=entities["ResolutionRecommendation"].id, category="policy", severity="error",
                               ruleType=audit_controls.RULE_TYPE, source="manual", status="draft", config=config))
            changed["audit_controls"].append(code)
    for total in OVER_SHORT_TOTALS:
        code = f"NSA-TOTAL-OVERSHORT-{total.replace('_', '-')}"
        if not db.query(BusinessRule).filter_by(workspaceId=workspace_id, ruleCode=code).first():
            config = audit_controls.validate_config({
                "family": "store_day_total", "total_name": f"Configured: {total}", "balance_level": "Store",
                "comparison": "ABS_GT", "threshold": OVER_SHORT_LIMIT, "threshold_currency": "USD", "severity": "Medium"})
            statement = f"Raise a case when the store-day difference {total} is more than {OVER_SHORT_LIMIT} USD over or short."
            db.add(BusinessRule(workspaceId=workspace_id, ruleCode=code, name=f"Over/short {total}", expression=statement,
                               statement=statement, description="Sales Audit store-day total control read by Nimbus from an approved release.",
                               entityId=entities["ResolutionRecommendation"].id, category="policy", severity="error",
                               ruleType=audit_controls.RULE_TYPE, source="manual", status="draft", config=config))
            changed["audit_controls"].append(code)
    for code, name, exception_type, level in AUDIT_LEVELS:
        if not db.query(BusinessRule).filter_by(workspaceId=workspace_id, ruleCode=code).first():
            config = audit_controls.validate_config({"family": "agent_level", "exception_type": exception_type, "level": level})
            statement = f"The agent may {level} on {exception_type}: collect gathers evidence, propose also recommends, act resolves with an audit event."
            db.add(BusinessRule(workspaceId=workspace_id, ruleCode=code, name=name, expression=statement,
                               statement=statement, description="Sales Audit agent level read by Nimbus from an approved release.",
                               entityId=entities["ResolutionRecommendation"].id, category="policy", severity="error",
                               ruleType=audit_controls.RULE_TYPE, source="manual", status="draft", config=config))
            changed["audit_controls"].append(code)
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
