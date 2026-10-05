# Nimbus Sales Audit — Solution Design Overview

**Product:** Nimbus Zero Touch Sales Audit  
**Positioning:** Agentic Financial Exception Management for Retail Commerce  
**Document date:** October 5, 2026  
**Status:** Proposed logical solution design

## Purpose and Executive Summary

Nimbus provides continuous sales audit and financial exception management across retail sales channels, payments, refunds, settlements, and downstream financial systems. It combines a deterministic audit core, agents that investigate and coordinate work, durable resolution workflows, and the retailer's existing enterprise ontology.

### Core Lifecycle

```
Case → Evidence → Diagnosis → Policy → Decision → Action → Validation
```

Nimbus collects facts, establishes why an exception exists, recommends a resolution, enforces authority, executes permitted actions, and verifies the outcome. Its Finance and IT workspaces present different views of the same cases, evidence, runs, and history.

### Design Principles

1. **Reduce manual investigation** while preserving financial correctness
2. **Conditional autonomous resolution** on sufficient evidence and delegated authority
3. **Show consequences before decision** and actual progress afterward
4. **Distinguish exception amount, estimated loss exposure, close impact,** and pending validation
5. **Preserve original data** and record corrections through versions or adjustment events
6. **Make missing evidence explicit** — do not equate unavailable data with a financial mismatch
7. **Use shared identifiers and event history** across Finance and IT
8. **Apply exact decimal arithmetic**, explicit currencies, approved rounding, and effective-dated definitions
9. **Process routine transactions deterministically;** invoke agents where investigation adds value
10. **Separate suggested causes from supported findings** and approved knowledge from experimental patterns

## Scope and Deployment Boundary

### Supported Deployment Modes

| Mode | Nimbus Responsibility |
|---|---|
| Standalone sales audit | Ingestion, validation, totals, reconciliation, exception detection, case resolution, release eligibility, export orchestration, validation |
| Coexistence with audit application | Consume source exceptions, collect cross-system evidence, investigate, enforce decision authority, orchestrate actions, validate results |

### In Scope

- Sales, returns, discounts, taxes, tenders, voids, and cancellations
- Completeness, balancing, transaction audit, and selected financial reconciliations
- Source-generated and Nimbus-generated exceptions
- Evidence-driven investigation and governed resolution
- Finance decisions and IT recovery interventions
- Post-action validation, reopening, and escalation
- Traceability from source record to downstream result
- Ontology-driven definitions through MCP and REST

### Initial Exclusions

- Replacement of payment processing, banking, POS, OMS, or ERP functions
- Unrestricted agent SQL writes or arbitrary financial posting
- Autonomous material financial corrections in initial release
- Production authentication and real bank/ERP integration in synthetic demo
- Exhaustive administration, mobile applications, model training

## Key Concepts

### Exception vs. Finding vs. Recommendation

- **Exception**: Detected through source error, failed control, or Nimbus rule evaluation
- **Finding**: Supported conclusion backed by actual evidence
- **Hypothesis**: Candidate explanation awaiting evidence
- **Recommendation**: Agent-proposed disposition with evidence basis and expected workflow
- **Approval**: Finance/IT authorization for a specific recommendation and scope

### Evidence Completeness

Evidence requirements are defined in the ontology. Completeness is calculated from applicable requirements, not assigned by the model.

- **Complete**: All required evidence obtained and validated
- **Incomplete**: Some required evidence missing or stale
- **Conflicting**: Multiple evidence sources contradict
- **Unavailable**: Required evidence source is unreachable

### Disposition Types

- **Timing**: Classify a difference as timing-related; monitor through reconciliation window
- **Correction**: Apply a governed adjustment (Finance authorization required)
- **Recovery**: Request reprocessing from a source system (IT coordination required)
- **Escalation**: Route to specialist team with documented findings
- **Closure**: Close without action when resolved or waived

## Personas and Responsibilities

### Finance Command Center

**Focus**: Exception amount, financial exposure, approval authority, downstream impact

- View Ready for Decision, Needs Review, and collapsed Agent Handled work
- Show exception amount and financial exposure separately
- Controls: Approve Recommendation, Request More Evidence, Escalate
- Validation: Confirm expected financial movement occurred

### IT Operations Command Center

**Focus**: Connector health, evidence request status, recovery state, close obligations

- View Needs Intervention, Self-Recovering, and Resolved work
- Show affected evidence requests, runs, cases, and exposure scope
- Interventions: Retry Now, Pause Connector, Open Run Trace
- Mode changes: Governed Automation, Recommendation Only, Investigation Only, Pause Automation

### Ask Nimbus

**Capability**: Contextual assistant using current case state, ontology definitions, and evidence

- Answer questions about cases, evidence, and policies
- Cite relevant references and policy versions
- Present proposed actions through normal decision controls
- Cannot bypass authorization or invent completed work

## Workflows and Automation

### Investigation Workflow

1. Case created from source exception or Nimbus control
2. Investigation Agent retrieves ontology definitions and requirements
3. Evidence acquired through controlled tool gateway
4. Diagnosis and recommendation produced
5. Policy engine evaluates permission and evidence sufficiency
6. Result: Permit autonomously, require human approval, request evidence, or block

### Resolution Workflow

1. Approved recommendation dispatched through durable orchestrator
2. Resolution Agent coordinates permitted steps
3. Every mutation passes through controlled action gateway
4. Gateway rechecks authorization, applies idempotency, records receipts
5. Result: Disposed, failed, or uncertain (requires status query)

### Validation Workflow

1. Validation obligation created after action execution
2. Validation Agent gathers fresh evidence at appropriate time
3. Deterministic verification checks promised result
4. Outcome: Verified → Closed, mismatch/breach → Reopen/Escalate, evidence unavailable → Wait

## Runtime Data Model (Simplified)

| Object | Purpose |
|---|---|
| SourceRecord | Preserve original payload with metadata |
| CanonicalTransaction | Normalized business record with lineage to source |
| Exception | Detected through source error or failed control |
| Case | Groups related exceptions and orchestrates resolution |
| EvidenceSnapshot | Required evidence type, record references, status |
| Finding | Supported conclusion backed by evidence references |
| Recommendation | Proposed disposition with evidence version and expected impact |
| PolicyEvaluation | Policy rule versions, resolved variables, decision |
| HumanDecision | Actor, scope, choice, override reason, timestamp |
| Workflow/Step | Definition version, state, gates, next action, dependencies |
| ActionAttempt | Tool call with idempotency key, correlation ID, response |
| ValidationObligation | Expected observation, due window, verification rule |
| ExportRecord | Destination, transaction version, acknowledgment status |
| AuditEvent | Event identity, actor, object references, causation links |

## Integration Contract Requirements

### Required Inputs

| Integration | Data | Purpose |
|---|---|---|
| POS/Sales channels | Headers, lines, quantities, prices, taxes, tenders, voids, returns, IDs | Transaction audit and balancing |
| POS controls | Registers, sequences, manifests, declared totals, completion markers | Independent completeness checks |
| Payment processor | Authorizations, captures, reversals, voids, refunds, statuses, references | Payment confirmation |
| Bank feeds | Deposits, withdrawals, posting/value dates, processor references | Cash movement confirmation |
| Reference systems | Products, promotions, prices, stores, tenders, calendars | Validation context |
| ERP/GL | Period status, mappings, posting references, acknowledgments | Downstream validation |
| Existing audit tool | Errors, transactions, matches, rule versions, export status | Source-generated cases |

### Supported Recovery Actions

Per-connector basis:
- Retrieve file or transaction
- Replay verified source record
- Reprocess rejected record
- Query action status
- Pause/resume connector

## Next Steps

See detailed documentation:
- [Architecture Design](NIMBUS_ARCHITECTURE.md)
- [Data Model](NIMBUS_DATA_MODEL.md)
- [Integration Guide](NIMBUS_INTEGRATIONS.md)
- [Scenarios and Use Cases](NIMBUS_SCENARIOS.md)
- [Controls and Security](NIMBUS_CONTROLS.md)
