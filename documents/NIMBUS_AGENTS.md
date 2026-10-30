# Nimbus Agents — Design and Implementation

## Overview

Agents in Nimbus are bounded AI systems that investigate exceptions, propose resolutions, and validate outcomes. They operate under strict constraints: evidence-driven reasoning, no authority claims, no fabricated facts, and complete explainability.

**Core Principle:** Agents do the work; humans manage by exception.

---

## Agent Types

### 1. Investigation Agent

**Purpose:** Gather evidence and diagnose exceptions

**When Invoked:**
- New case created with exception
- Human requests "More Evidence"
- Evidence becomes stale (> 4 hours)
- Policy gate requires reassessment

**Input:**
```json
{
  "case_id": "ZA-20261006-0147",
  "exception_type": "TIMING_DIFFERENCE",
  "exception_amount": 550,
  "affected_transactions": ["TX-12345"],
  "ontology_context": {
    "evidence_requirements": ["Refund confirmation", "Timestamp", "Settlement status"],
    "known_causes": ["Late refund", "Processor delay", "Cutoff timing"],
    "investigation_procedures": "Retrieve supporting documentation and timestamp analysis"
  },
  "prior_findings": [],
  "evidence_already_held": []
}
```

**Process:**

1. **Retrieve Ontology Context**
   - Evidence requirements for exception type
   - Known causes and investigation procedures
   - Applicable business rules and thresholds
   - Example resolution workflows

2. **Check Held Evidence**
   - Query case for previously retrieved evidence
   - Assess freshness (use if < 4 hours old)
   - Identify gaps remaining

3. **Request New Evidence**
   - For each missing or stale requirement
   - Call evidence gateway with query
   - Record retrieval timestamp and source

4. **Evaluate Evidence Against Hypotheses**
   - Test each known cause against evidence
   - Identify which hypotheses are supported
   - Flag contradictions between sources

5. **Produce Findings**
   - Supported findings with evidence references
   - Unresolved hypotheses with gaps explained
   - Confidence assessment for each finding

6. **Propose Disposition**
   - Based on findings and applicable rules
   - Specify action class and expected workflow
   - Identify evidence sufficiency

**Output:**
```json
{
  "investigation_id": "INV-20261006-0147-001",
  "case_id": "ZA-20261006-0147",
  "timestamp": "2026-10-06T09:14:42Z",
  "agent_version": "investigation-v2.1",
  "findings": [
    {
      "id": "FIND-001",
      "conclusion": "Refund $550 occurred after 23:00 cutoff",
      "type": "Supported Finding",
      "confidence": "High",
      "evidence_ids": ["EVD-0001", "EVD-0002"],
      "supporting_rationale": "Refund timestamp 23:15 is after declared cutoff. Confirmed by processor settlement batch."
    }
  ],
  "unresolved_hypotheses": [],
  "missing_evidence": [],
  "evidence_completeness": "100%",
  "recommended_disposition": {
    "disposition_type": "Timing",
    "action_class": "Disposition Only",
    "expected_workflow": "Apply timing classification → Schedule validation → Monitor settlement → Close",
    "financial_impact": "None",
    "evidence_version_hash": "sha256:abc123",
    "confidence_in_recommendation": "High"
  }
}
```

**Constraints:**
- Cannot declare authority or override policy
- Cannot fabricate evidence or invent data
- Must explicitly document evidence gaps
- Cannot assume unavailable data equals an error
- Cannot access raw SQL or unrestricted data queries
- Timeout: 60 seconds (single investigation)

---

### 2. Resolution Agent

**Purpose:** Execute approved resolutions and coordinate subsequent work

**When Invoked:**
- Policy gate approves autonomous action
- Human decision approves recommendation
- Workflow step requires action coordination

**Input:**
```json
{
  "workflow_id": "WF-20261006-0147-001",
  "case_id": "ZA-20261006-0147",
  "approved_recommendation": {
    "disposition": "Timing",
    "action_class": "Disposition Only",
    "workflow_template": "timing-disposition-workflow",
    "evidence_snapshot_version": "sha256:abc123"
  },
  "human_decision": {
    "actor_id": "finance_director_001",
    "actor_role": "Finance",
    "timestamp": "2026-10-06T09:22:15Z",
    "decision": "Approved",
    "scope": "Timing disposition only; no financial write"
  },
  "process_definition": {
    "steps": [
      "Apply timing classification",
      "Create validation obligation",
      "Schedule next-cycle check"
    ]
  }
}
```

**Process:**

1. **Validate Approval Currency**
   - Check approval timestamp
   - Verify actor authority still valid
   - Confirm recommendation not superseded

2. **Load Workflow Template**
   - Retrieve process definition
   - Resolve semantic variables
   - Establish success/failure criteria

3. **Execute Approved Steps**
   - Apply timing disposition to exception
   - Create validation obligation (expected observation)
   - Record decision and evidence version
   - Link to approval audit event

4. **Request Tool Execution**
   - For each external action (if any)
   - Call action gateway with idempotency key
   - Record correlation ID
   - Handle uncertain outcomes

5. **Coordinate Subsequent Workflows**
   - Trigger validation obligations
   - Schedule settlement-cycle check
   - Link to related cases

6. **Report Status**
   - Workflow advanced from "Approved" to "Executing"
   - State: "Executed — Validation Pending"
   - Expected validation window provided

**Output:**
```json
{
  "workflow_execution_id": "WFX-20261006-0147-001",
  "workflow_id": "WF-20261006-0147-001",
  "case_id": "ZA-20261006-0147",
  "timestamp": "2026-10-06T09:22:31Z",
  "agent_version": "resolution-v2.1",
  "steps_executed": [
    {
      "step_id": "STEP-001",
      "step_name": "Apply timing classification",
      "status": "Completed",
      "timestamp": "2026-10-06T09:22:31Z",
      "changes": {
        "exception_status": "Timing",
        "disposition_recorded": true
      }
    },
    {
      "step_id": "STEP-002",
      "step_name": "Create validation obligation",
      "status": "Completed",
      "validation_obligation_id": "VAL-20261006-0147-001",
      "expected_observation": "Refund in next settlement batch",
      "due_window_start": "2026-10-07T05:00:00Z",
      "due_window_end": "2026-10-07T12:00:00Z"
    }
  ],
  "workflow_state": "Executed — Validation Pending",
  "next_action": "Wait for settlement cycle",
  "validation_obligation": "Monitor settlement batch Oct 7"
}
```

**Constraints:**
- Can only execute within approved scope
- Every mutation through action gateway
- Preconditions rechecked before execution
- Idempotency keys prevent duplicate execution
- No unrestricted SQL or raw connector credentials
- Timeout: 30 seconds per action (5 min total workflow)

---

### 3. Validation Agent

**Purpose:** Obtain evidence of actual outcome and verify success

**When Invoked:**
- Validation obligation due window reached
- Trigger event detected (e.g., settlement batch arrived)
- Manual validation requested

**Input:**
```json
{
  "validation_obligation_id": "VAL-20261006-0147-001",
  "case_id": "ZA-20261006-0147",
  "expected_observation": "Refund $550 in settlement batch",
  "observation_type": "Financial Movement",
  "evidence_requirement": "Settlement batch detail for Oct 7",
  "verification_rule_id": "verify-settlement-includes-refund",
  "verification_rule": "Expected amount matches actual amount, and refund is present",
  "due_window": {
    "start": "2026-10-07T05:00:00Z",
    "end": "2026-10-07T12:00:00Z"
  }
}
```

**Process:**

1. **Check Due Window**
   - Confirm within validation window
   - Skip if too early (evidence not yet available)
   - Escalate if past deadline without evidence

2. **Retrieve Fresh Outcome Evidence**
   - Query settlement batch for date/reference
   - Get refund detail if present
   - Record retrieval timestamp and source

3. **Apply Deterministic Verification Rule**
   - Execute rule logic using retrieved evidence
   - No model judgment; deterministic only
   - Document inputs and outputs

4. **Produce Verification Result**
   - Success: Expected observation confirmed
   - Mismatch: Actual differs from expected
   - Missing Evidence: Source unavailable
   - Inconclusive: Evidence insufficient

5. **Recommend Workflow Action**
   - Success → Close case
   - Mismatch → Reopen and investigate
   - Missing Evidence → Wait or escalate
   - Inconclusive → Escalate or wait

**Output:**
```json
{
  "validation_execution_id": "VALX-20261006-0147-001",
  "validation_obligation_id": "VAL-20261006-0147-001",
  "case_id": "ZA-20261006-0147",
  "timestamp": "2026-10-07T06:45:22Z",
  "agent_version": "validation-v2.1",
  "evidence_retrieved": {
    "evidence_id": "EVD-0003",
    "type": "Settlement batch detail",
    "source": "Payment processor",
    "retrieved_at": "2026-10-07T06:45:15Z",
    "content_summary": "Oct 7 settlement batch contains refund ID REF-54321 for $550"
  },
  "verification_rule_applied": "verify-settlement-includes-refund",
  "verification_inputs": {
    "expected_amount": 550,
    "expected_type": "Refund",
    "actual_amount": 550,
    "actual_type": "Refund",
    "amounts_match": true,
    "type_matches": true
  },
  "verification_result": "SUCCESS",
  "recommendation": {
    "action": "Close case",
    "reason": "Expected refund movement confirmed in settlement batch",
    "confidence": "High"
  }
}
```

**Constraints:**
- Uses only deterministic verification rules (no LLM judgment)
- Cannot override policy or create financial records
- Must wait for evidence if not yet available
- Cannot invent evidence if source unavailable
- Timeout: 30 seconds (including external queries)

---

## Agent Lifecycle

```
┌─────────────────────────────────────────────────────────┐
│                   Invoke Agent                          │
│  Workflow engine → Agent with bounded inputs            │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Model Gateway (Orchestration)              │
│  - Validate inputs                                      │
│  - Check rate limits and budgets                        │
│  - Route to model with system prompt                    │
│  - Enforce timeout (60s Investigation, 30s others)      │
│  - Validate output structure                           │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Agent Execution (Model)                    │
│  - Load case context from ontology                      │
│  - Reason about evidence and causes                     │
│  - Request tool calls through gateway                   │
│  - Synthesize findings and recommendations              │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│           Structured Output Validation                  │
│  - Check required fields present                        │
│  - Verify evidence/finding links valid                 │
│  - Confirm recommendations within scope                 │
│  - Reject if output policy violated                     │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│            Store Result & Audit Trail                   │
│  - Persist structured output                           │
│  - Record agent run details                             │
│  - Link to case and workflow                            │
│  - Update case state                                    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│          Return to Workflow Orchestrator                │
│  - Next workflow step determined by result              │
│  - Policy gate evaluates recommendation                 │
│  - Human notification if approval needed                │
└─────────────────────────────────────────────────────────┘
```

---

## Tool Access and Security

### Evidence Tool Gateway (Read-Only)

**Queries Agents Can Make:**
```
1. retrieve_transaction(source_system, transaction_id)
   Returns: Complete transaction record with timestamps
   
2. retrieve_payment_confirmation(processor_ref, amount)
   Returns: Authorization/capture status and proof
   
3. retrieve_refund_status(processor_ref, refund_id)
   Returns: Refund confirmation and posting status
   
4. query_settlement_batch(processor, business_date)
   Returns: Batch detail with transaction/refund records
   
5. retrieve_bank_deposit(bank_ref, amount)
   Returns: Deposit confirmation and posting status
   
6. query_rule_definition(rule_id, version)
   Returns: Rule logic and applicability conditions
   
7. query_ontology(entity_type, entity_id, version)
   Returns: Definition, relationships, effective dates
```

**Security Enforcement:**
- Authenticate every query with service credentials
- Apply field-level masking before returning data
  - Full card numbers → Masked (****1234)
  - SSN/IDs → Removed or masked
  - Personal addresses → Removed
- Log all queries (source, timestamp, actor, result)
- Rate limit: 100 queries/min per agent
- Timeout: 10 seconds per query
- Cache retrieved evidence for 4 hours (reduce redundant queries)

### Action Tool Gateway (Governed Writes)

**Actions Agents Can Request:**

```
1. apply_disposition(case_id, disposition_type, reason)
   Authority: Autonomous if policy permits
   Effect: Updates exception status and workflow state
   
2. create_validation_obligation(case_id, expected_observation, due_window, rule)
   Authority: Autonomous (workflow step)
   Effect: Creates obligation record and schedules check
   
3. escalate_to_analyst(case_id, reason, priority)
   Authority: Autonomous (bounded action)
   Effect: Creates case assignment for manual review
   
4. request_tool_retry(connector_name, request_id)
   Authority: IT autonomous within scope
   Effect: Retries failed connector call
   
5. change_case_status(case_id, new_status)
   Authority: Autonomous or approved (depends on status)
   Effect: Transitions case through lifecycle
```

**Security Enforcement:**
- Verify authorization immediately before execution
  - Agent credentials vs. action permissions
  - Scope limits (what entities can be modified)
- Recalculate policy permission if material inputs changed
- Idempotency keys prevent duplicate execution
- Concurrency control (version checks) ensure no conflicts
- Audit every action with actor, timestamp, correlation ID
- Transaction semantics: all-or-nothing per workflow step

**Actions Agents CANNOT Make:**
- Direct SQL writes to database
- Direct connector calls with raw credentials
- Financial postings (GL entries)
- Policy overrides
- Access control changes
- Data deletion or archival

---

## System Prompt Architecture

### Investigation Agent System Prompt

```
You are an Investigation Agent for Nimbus Sales Audit. Your role is to
investigate exceptions by gathering evidence and producing findings.

CONSTRAINTS:
1. You must gather EVIDENCE before forming conclusions
2. Findings must be SUPPORTED by actual evidence, not hypotheses
3. You must document EXPLICITLY which evidence supports each finding
4. Missing evidence is NOT proof of an error — it is a gap
5. You cannot declare AUTHORITY or override policy decisions
6. You cannot FABRICATE data or make assumptions without evidence

REQUIRED WORKFLOW:
1. Identify what evidence is required from the ontology
2. Retrieve evidence through the tool gateway
3. Compare evidence against known causes
4. Label each hypothesis as "Supported", "Unsupported", or "Unresolved"
5. Only findings with evidence can be labeled "Supported"
6. Produce recommendation with confidence assessment

OUTPUT FORMAT:
Provide structured JSON with:
- findings[] (each with evidence_ids and supporting_rationale)
- unresolved_hypotheses[] (what remains unclear)
- missing_evidence[] (what we couldn't obtain)
- recommended_disposition (based on findings, not speculation)

GUARDRAILS:
- Do not recommend actions requiring Finance authority unless
  recommendation has Complete evidence status
- Do not close cases with Incomplete evidence
- Explicit about timeout: if you cannot retrieve evidence within
  60 seconds, report the attempt and gap
```

### Resolution Agent System Prompt

```
You are a Resolution Agent for Nimbus Sales Audit. Your role is to
execute approved resolutions according to a defined workflow.

CONSTRAINTS:
1. You ONLY execute within the approved recommendation scope
2. Every action goes through the governed action gateway
3. You MUST verify preconditions before each step
4. Idempotency keys prevent duplicate execution
5. You cannot override approval scope or act beyond authority
6. You must report status clearly: succeeded, failed, or uncertain

REQUIRED WORKFLOW:
1. Validate that approval is still current
2. Load the workflow template and resolve variables
3. For each step in the workflow:
   a. Check preconditions
   b. Recalculate authority (may have changed)
   c. Execute through action gateway
   d. Record correlation ID and timestamp
4. Handle uncertain outcomes: query status before retrying
5. Report final state and validation obligations created

OUTPUT FORMAT:
Provide structured JSON with:
- steps_executed[] (each with status and timestamp)
- workflow_state (advanced to next milestone)
- validation_obligations[] (what to check later)
- next_action (what workflow step is next)

GUARDRAILS:
- If a precondition fails, STOP and report the failure
- If evidence changed, flag for reassessment
- If multiple simultaneous workflows would conflict, escalate
- Treat uncertain tool responses as "may have succeeded";
  require status query before assuming completion
```

### Validation Agent System Prompt

```
You are a Validation Agent for Nimbus Sales Audit. Your role is to
verify that promised outcomes actually occurred.

CONSTRAINTS:
1. Validation is DETERMINISTIC, not based on model reasoning
2. You apply only pre-defined verification rules, never invent new logic
3. You cannot override the rule or bypass checks
4. Missing evidence is NOT proof of failure — escalate or wait
5. You report: Success, Mismatch, Missing Evidence, or Inconclusive

REQUIRED WORKFLOW:
1. Confirm we are within the validation window
2. Retrieve fresh outcome evidence from source
3. Apply the deterministic verification rule
4. Classify result: Success / Mismatch / Missing / Inconclusive
5. Recommend workflow action based on result

OUTPUT FORMAT:
Provide structured JSON with:
- evidence_retrieved (what was obtained)
- verification_rule_applied (which rule)
- verification_inputs (facts fed to rule)
- verification_result (outcome of rule)
- recommendation (workflow action: Close / Reopen / Escalate / Wait)

GUARDRAILS:
- Do not apply discretion to rule results
- If evidence is missing and deadline not passed: return to wait state
- If evidence is missing and deadline passed: escalate
- If result is inconclusive: escalate (do not guess)
```

---

## Model Gateway (Orchestration Layer)

**Responsibilities:**
- Authenticate agent invocation
- Validate input structure and required fields
- Load system prompt and case context
- Enforce rate limits and token budgets
- Call LLM with structured output schema
- Validate response structure
- Check for policy violations in output
- Timeout enforcement (60s investigation, 30s others)
- Retry logic for transient failures
- Logging and telemetry

**Rate Limits (per agent type, per hour):**
- Investigation Agent: 1,000 runs
- Resolution Agent: 500 runs
- Validation Agent: 2,000 runs

**Token Budgets (per run):**
- Investigation: 4,000 input, 2,000 output
- Resolution: 3,000 input, 1,000 output
- Validation: 2,000 input, 1,000 output

**Timeout Enforcement:**
```
Investigation Agent:
  Total timeout: 60 seconds
  Per evidence query: 10 seconds
  
Resolution Agent:
  Per action: 10 seconds
  Total workflow: 5 minutes
  
Validation Agent:
  Per evidence retrieval: 10 seconds
  Total: 30 seconds
```

**Retry Logic:**
- Transient API failures: 3 retries with exponential backoff
- Model timeout: 1 retry (then escalate)
- Input validation failure: 0 retries (report error)

---

## Agent Monitoring and Observability

### Telemetry Collection

**For each agent invocation, record:**
```json
{
  "agent_run_id": "unique-uuid",
  "agent_type": "Investigation|Resolution|Validation",
  "case_id": "ZA-20261006-0147",
  "workflow_id": "WF-20261006-0147-001",
  "invocation_timestamp": "2026-10-06T09:14:22Z",
  "completion_timestamp": "2026-10-06T09:14:41Z",
  "duration_ms": 19000,
  "input_tokens": 1200,
  "output_tokens": 450,
  "tool_calls": [
    {
      "tool": "retrieve_transaction",
      "duration_ms": 145,
      "status": "Success"
    }
  ],
  "result_status": "Success|Failed|Timeout|PolicyViolation",
  "error_code": null,
  "error_message": null,
  "model_version": "claude-opus-5-5",
  "policies_checked": ["evidence_completeness", "authority_check"],
  "output_validation_passed": true
}
```

### SLOs (Service Level Objectives)

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Investigation success rate | 98% | < 95% |
| Average investigation time | < 20s | > 30s |
| Evidence retrieval success | 99% | < 97% |
| Policy violation detection | 100% | Any miss |
| Validation accuracy | 99% | < 98% |

### Logging Requirements

**Log Levels:**
- INFO: Agent invoked, tool called, result produced
- WARNING: Timeout approaching, rate limit approaching, evidence gap
- ERROR: Failed invocation, tool failure, output validation failed
- CRITICAL: Policy violation detected, unauthorized action attempted

**Retention:**
- Successful runs: 90 days
- Failed runs: 1 year (for debugging)
- Audit-relevant events: 7 years (compliance)

---

## Agent Safety and Guardrails

### Policy Violation Detection

**Before returning agent output, check:**

1. **Evidence Integrity**
   - All cited evidence IDs actually exist
   - Confidence scores align with evidence quantity
   - No circular reasoning (finding based on itself)

2. **Scope Compliance**
   - Recommendations within approved action class
   - No financial writes without Finance authority
   - No operational actions beyond IT scope

3. **Authority Compliance**
   - Recommendations don't claim to override policy
   - No "approve yourself" patterns
   - No bypassing required gates

4. **Factual Consistency**
   - No contradictions within findings
   - Amounts match across references
   - Timestamps logically consistent

5. **Explainability**
   - Every finding has evidence references
   - Recommendations explain their rationale
   - Missing pieces explicitly documented

**On Policy Violation:**
- Reject output and escalate
- Log violation with agent run details
- Alert security team
- Do not use truncated/modified output
- Request human review

---

## Example Agent Interactions

### Investigation Agent Interaction

**Workflow → Investigation Agent:**
```json
{
  "case_id": "ZA-20261006-0147",
  "exception_type": "TIMING_DIFFERENCE",
  "amount": 550,
  "evidence_required": [
    "Refund confirmation",
    "Refund timestamp",
    "Settlement batch status"
  ]
}
```

**Investigation Agent Queries:**
1. `retrieve_transaction(POS, TX-12345)` → Full sale record
2. `retrieve_refund_status(Processor, REF-54321)` → Refund confirmation + timestamp
3. `query_settlement_batch(Processor, 2026-10-06)` → Batch includes refund

**Investigation Agent Response:**
```json
{
  "findings": [
    {
      "conclusion": "Refund $550 occurred after 23:00 cutoff",
      "type": "Supported Finding",
      "confidence": "High",
      "evidence_ids": ["EVD-001", "EVD-002"]
    }
  ],
  "evidence_completeness": "100%",
  "recommended_disposition": "Timing",
  "confidence_in_recommendation": "High"
}
```

**Workflow → Policy Engine:**
Takes recommendation and evaluates authority

**Policy Engine → Finance:**
If approval needed, shows case and recommendation

**Finance → Workflow:**
Approval or request for more evidence

**Workflow → Resolution Agent:**
Execute approved resolution

---

## Summary Table

| Aspect | Investigation | Resolution | Validation |
|--------|---|---|---|
| **Purpose** | Gather evidence & diagnose | Execute approved actions | Verify outcomes |
| **Invoked When** | New exception, more evidence needed | Approval granted | Due window reached |
| **Timeout** | 60 seconds | 30 sec/action (5 min total) | 30 seconds |
| **Tool Access** | Read-only evidence queries | Governed action gateway | Read-only evidence |
| **Authority** | None (recommends only) | Within approved scope | None |
| **Output** | Findings + recommendation | Workflow progress | Verification result |
| **Key Constraint** | Evidence-backed conclusions | Policy-authorized execution | Deterministic rules only |
| **Escalation** | Evidence gaps | Failed preconditions | Missing evidence at deadline |
