# Nimbus Data Model

## Core Entity Relationships

```
SourceRecord (1) ──→ CanonicalTransaction
                 ──→ AuditEvent

CanonicalTransaction (1) ──→ (M) Exception
                         ──→ (M) ReconciliationMatch
                         ──→ (M) ValidationObligation

Exception (1) ──→ Case
           ──→ (M) EvidenceSnapshot
           ──→ (M) AuditEvent

Case (1) ──→ (M) Exception (related exceptions)
         ──→ (M) Workflow
         ──→ (M) Finding
         ──→ (M) Recommendation
         ──→ (M) HumanDecision
         ──→ (M) AuditEvent

Recommendation (1) ──→ HumanDecision
                  ──→ Workflow
                  ──→ (M) AuditEvent

Workflow (1) ──→ (M) WorkflowStep
          ──→ (M) ActionAttempt
          ──→ (M) ValidationObligation
          ──→ (M) AuditEvent

ActionAttempt (1) ──→ Tool/ToolVersion
              ──→ AuditEvent

ValidationObligation (1) ──→ EvidenceSnapshot
                       ──→ VerificationRule
                       ──→ AuditEvent

ExportRecord (1) ──→ CanonicalTransaction
             ──→ Destination
             ──→ (M) AuditEvent
```

## Detailed Entity Definitions

### SourceRecord

**Purpose:** Preserve original payload before any transformation

**Attributes:**
- `id` (UUID) — unique within source system
- `source_system` — system name (e.g., "POS", "Processor", "Bank")
- `source_version` — source system version/schema version
- `source_record_id` — native record identifier in source system
- `delivery_id` — unique identifier for this delivery/batch
- `payload` — complete original content (binary or text)
- `payload_hash` — checksum for integrity verification
- `received_at` — timestamp when received by Nimbus
- `extracted_at` — timestamp when unpacked/normalized
- `source_event_time` — timestamp when event occurred in source
- `tenant_id` — multi-tenant isolation
- `legal_entity` — legal entity identifier
- `status` — Received, Archived, Quarantined, Processed
- `quarantine_reason` — if status is Quarantined
- `retention_until` — data retention date
- `created_by` — system or user who created record
- `created_at` — Nimbus ingestion timestamp

**Indexes:**
- (source_system, source_record_id)
- (delivery_id, source_system)
- (source_event_time, source_system)
- (status, created_at)

### CanonicalTransaction

**Purpose:** Normalized business record with complete lineage

**Attributes:**
- `id` (UUID) — canonical transaction identity
- `tenant_id` — multi-tenant isolation
- `legal_entity` — associated legal entity
- `business_date` — business date for the transaction
- `transaction_date` — date transaction occurred
- `event_timestamp` — precise timestamp of transaction event
- `processing_timestamp` — when transaction entered system
- `settlement_date` — expected settlement date
- `accounting_date` — GL accounting date
- `transaction_type` — Sale, Return, Discount, Refund, Void, Tender, Fee, Adjustment
- `transaction_subtype` — more specific classification
- `source_records` — array of related SourceRecord IDs
- `source_lineage` — version of mapping/transformation used
- `currency` — currency code (e.g., USD, GBP)
- `signed_amount` — signed amount (positive sales, negative refunds)
- `amount_components` — breakdown by line, tax, fee, tender type
- `quantity` — units involved (where applicable)
- `unit_amount` — per-unit price
- `store_id` — store/location identifier
- `register_id` — register/terminal identifier
- `channel_id` — sales channel (physical, online, etc.)
- `merchant_account` — merchant account for this transaction
- `customer_id` — customer reference (masked if needed)
- `session_id` — store session/shift identifier
- `parent_transaction_id` — if this is a return/reversal/adjustment
- `related_transaction_ids` — array of related transaction IDs
- `tender_type` — payment method
- `payment_reference` — external payment system reference
- `authorization_code` — if payment authorization exists
- `settlement_reference` — settlement batch or ID
- `reconciliation_status` — Unmatched, Matched, Partial, Ambiguous
- `audit_rule_version` — which rule detected any exception
- `exception_ids` — related exception IDs (if any)
- `disposition` — Final disposition (Closed, Timing, Awaiting, Disputed, etc.)
- `data_classification` — sensitivity level
- `access_scope` — who can access this data
- `created_at` — when normalized
- `updated_at` — last modification
- `version` — data version number (for concurrency control)

**Indexes:**
- (business_date, store_id)
- (transaction_type, business_date)
- (parent_transaction_id) — for returns/reversals
- (payment_reference, settlement_date)
- (reconciliation_status, business_date)
- (exception_ids)

### Exception

**Purpose:** Detected anomaly or control failure

**Attributes:**
- `id` (UUID) — exception identity
- `case_id` (UUID) — associated case (created on first exception)
- `exception_type` — e.g., DUPLICATE_SALES, TIMING_DIFFERENCE, MISSING_REFUND
- `exception_family` — category from ontology taxonomy
- `source_code` — original error code from source system
- `source_system` — system that originated or detected
- `detection_origin` — Source Error, Audit Rule, Reconciliation
- `detection_rule_id` — which rule/process detected this
- `detection_rule_version` — version of detection rule
- `affected_transaction_ids` — which transactions involved
- `exception_amount` — absolute amount of discrepancy
- `estimated_exposure` — financial loss exposure (may differ from amount)
- `exposure_basis` — how exposure was calculated
- `close_impact` — impact on close eligibility (Yes, No, Conditional)
- `close_blocker_reason` — why it blocks close (if applicable)
- `severity` — Critical, High, Medium, Low
- `confidence` — High, Medium, Low
- `business_date` — date exception pertains to
- `first_detected_at` — when exception first detected
- `current_owner` — person/team currently responsible
- `status` — Open, Investigating, Decision Pending, Approved, Executed, Validated, Closed, Escalated
- `requires_human_review` — boolean
- `requires_finance_approval` — boolean
- `related_exception_ids` — linked exceptions (shared incident)
- `tenant_id` — multi-tenant isolation
- `created_at` — timestamp
- `updated_at` — last change timestamp

**Indexes:**
- (case_id)
- (business_date, source_system)
- (status, current_owner)
- (requires_finance_approval, status)
- (exception_type, business_date)

### Case

**Purpose:** Groups related exceptions and orchestrates resolution

**Attributes:**
- `id` (UUID) — case identity
- `case_number` — human-readable identifier (e.g., "ZA-20261006-0147")
- `related_exceptions` — array of exception IDs
- `primary_exception_id` — leading exception in group
- `incident_id` — if multiple cases share common incident
- `total_exception_amount` — sum of related exceptions
- `total_estimated_exposure` — aggregated exposure across exceptions
- `close_blocker_count` — how many blockers remain
- `close_eligible` — boolean, can this close now?
- `close_impact_reason` — why it is/isn't eligible
- `earliest_detection_date` — min date among exceptions
- `most_recent_update_at` — latest update timestamp
- `current_lifecycle_state` — Open, Investigation, Decision, Approved, Executing, Validation Pending, Closed, Reopened, Escalated
- `evidence_status` — Complete, Incomplete, Stale, Conflicting
- `workflow_state` — what step of workflow active
- `investigation_owner` — who's investigating
- `decision_required_by` — deadline for approval
- `investigation_history` — array of investigation run records
- `audit_events` — array of audit event IDs linked to case
- `affected_stores` — which stores involved
- `affected_channels` — which channels involved
- `tenant_id` — multi-tenant isolation
- `legal_entity` — which legal entity case pertains to
- `priority` — Urgent, High, Normal, Low
- `tags` — free-form categorization
- `created_at` — case creation timestamp
- `updated_at` — last state change

**Indexes:**
- (case_number)
- (related_exceptions)
- (current_lifecycle_state)
- (investigation_owner)
- (business_date)
- (close_eligible, current_lifecycle_state)

### EvidenceSnapshot

**Purpose:** Record retrieved evidence with metadata

**Attributes:**
- `id` (UUID) — evidence identity
- `case_id` — associated case
- `exception_id` — associated exception
- `evidence_type` — required type (e.g., "Refund_Confirmation", "Settlement_Batch")
- `evidence_requirement_id` — ontology requirement this satisfies
- `source_system` — which system provided evidence
- `source_record_ids` — references to supporting SourceRecord IDs
- `canonical_transaction_ids` — associated CanonicalTransaction IDs
- `retrieved_at` — when evidence was obtained
- `event_timestamp` — when event occurred (in source system time)
- `content` — evidence content (may be summarized or masked)
- `content_hash` — integrity check
- `status` — Available, Stale, Missing, Conflicting, Quarantined
- `freshness_hours` — how old this evidence is
- `data_classification` — sensitivity level
- `access_restrictions` — who can view
- `contradictions` — array of other evidence this conflicts with
- `confidence` — High, Medium, Low
- `supporting_finding_ids` — findings backed by this evidence
- `investigation_run_id` — which investigation retrieved this
- `retrieved_by` — who/what retrieved this (system or user)
- `retention_until` — data retention date
- `created_at` — record creation
- `updated_at` — last change

**Indexes:**
- (case_id)
- (evidence_type, status)
- (source_system, retrieved_at)
- (supporting_finding_ids)

### Finding

**Purpose:** Supported conclusion backed by evidence

**Attributes:**
- `id` (UUID) — finding identity
- `case_id` — associated case
- `conclusion` — statement of finding
- `conclusion_type` — Supported Finding, Hypothesis, Contradiction, Unresolved
- `evidence_ids` — array of evidence IDs supporting this finding
- `confidence_category` — High, Medium, Low
- `confidence_rationale` — why this confidence level
- `supported_by_investigation_run` — which investigation produced this
- `contradicts` — array of contradicted findings
- `supporting_hypotheses` — if this finding tests a hypothesis
- `impact_on_disposition` — how this changes potential actions
- `investigation_agent_version` — which agent model produced this
- `produced_at` — timestamp
- `updated_at` — last change
- `verified_by` — if human has validated this finding
- `verified_at` — when human verified

**Indexes:**
- (case_id)
- (conclusion_type)
- (confidence_category)

### Recommendation

**Purpose:** Proposed resolution with evidence and expected workflow

**Attributes:**
- `id` (UUID) — recommendation identity
- `case_id` — associated case
- `based_on_findings_ids` — which findings informed this
- `evidence_version` — hash/version of evidence reviewed
- `disposition` — proposed disposition (Timing, Correction, Recovery, Escalation, Closure)
- `disposition_subtype` — more specific action
- `action_class` — Disposition Only, Financial Write, Connector Action, Escalation
- `proposed_workflow_template_id` — which workflow template applies
- `expected_subsequent_steps` — next steps after approval
- `success_criteria` — what proves this worked
- `expected_side_effects` — known consequences
- `financial_impact` — if any monetary change expected
- `financial_impact_basis` — how impact was calculated
- `affected_totals` — which totals or accounts affected
- `evidence_sufficiency_check` — yes/no whether evidence is complete
- `policy_permission_to_execute` — yes/no whether can execute autonomously
- `requires_human_approval` — boolean
- `approval_authority_required` — Finance, IT, etc.
- `exposure_reduction` — how much exposure this resolves
- `risk_if_not_executed` — consequences of inaction
- `alternative_recommendations` — other options considered
- `produced_by_agent_version` — which investigation agent
- `produced_at` — timestamp
- `expires_at` — recommendation validity window
- `approval_status` — Pending, Approved, Rejected, Expired
- `investigation_run_id` — which investigation produced this

**Indexes:**
- (case_id)
- (disposition)
- (requires_human_approval, approval_status)
- (produced_at)

### PolicyEvaluation

**Purpose:** Record policy decision logic and result

**Attributes:**
- `id` (UUID) — evaluation identity
- `case_id` — associated case
- `recommendation_id` — what was evaluated
- `evaluation_timestamp` — when policy was evaluated
- `policy_rule_ids` — which rules applied
- `policy_rule_versions` — versions of each rule
- `input_attributes` — inputs to policy evaluation
- `resolved_variables` — semantic variables evaluated
- `applied_thresholds` — which thresholds applied and values
- `actor_id` — who triggered evaluation
- `actor_role` — Finance, IT, Automation, etc.
- `authority_check_result` — Authorized, Unauthorized, Insufficient Role
- `evidence_sufficiency_result` — Complete, Incomplete, Conflicting
- `aggregate_exposure_check` — Within Limit, Exceeds Limit
- `period_restriction_result` — Allowed, Restricted
- `automation_mode` — Governed Automation, Recommendation Only, Investigation Only, Paused
- `automation_allowed` — boolean
- `final_decision` — Permit Autonomous, Require Approval, Request Evidence, Block
- `block_reason` — if blocked, why
- `request_reason` — if requesting evidence, why
- `approval_authority_if_required` — who must approve
- `approval_deadline` — when approval needed by
- `decision_evidence_snapshot_version` — hash of evidence reviewed
- `audit_trail` — narrative of evaluation
- `created_at` — timestamp

**Indexes:**
- (case_id)
- (final_decision)
- (approval_deadline)

### HumanDecision

**Purpose:** Record human approval or escalation

**Attributes:**
- `id` (UUID) — decision identity
- `case_id` — associated case
- `recommendation_id` — what was approved/rejected
- `decision_actor_id` — who made decision
- `decision_actor_role` — Finance, IT, Manager, etc.
- `decision_authority_level` — what authority this person has
- `decision_type` — Approve, Reject, Request Evidence, Escalate, Override
- `decision_timestamp` — when decided
- `decision_scope` — what exactly is approved (full recommendation, partial, alternative)
- `chosen_disposition` — if alternative chosen
- `alternative_rationale` — why deviation from recommendation
- `evidence_reviewed` — array of evidence IDs reviewed
- `evidence_snapshot_version` — what evidence version reviewed
- `recommendation_version` — version of recommendation reviewed
- `conditions_attached` — any approval conditions
- `approval_validity_window` — when this approval expires
- `notes` — human commentary
- `authorization_check_at_decision` — was authority current at time?
- `created_at` — timestamp

**Indexes:**
- (case_id)
- (decision_actor_id, decision_timestamp)
- (decision_type)

### Workflow

**Purpose:** Durable orchestration of resolution process

**Attributes:**
- `id` (UUID) — workflow identity
- `case_id` — associated case
- `workflow_type` — Investigation, Resolution, Validation, Settlement
- `workflow_template_id` — which template instantiated this
- `workflow_template_version` — template version
- `trigger_event` — what started this workflow
- `trigger_timestamp` — when triggered
- `current_step_id` — which step executing now
- `current_state` — Idle, Running, Waiting, Paused, Failed, Completed
- `steps` — array of WorkflowStep objects
- `approval_gates` — array of decisions required
- `completion_gates` — conditions for completion
- `timers` — array of scheduled timers/events
- `retries_exhausted` — count of retries used
- `max_retries` — allowed retry count
- `failure_reason` — if failed, why
- `recovery_attempts` — array of recovery attempts
- `alternate_path_taken` — if alternate workflow used
- `validation_obligations` — array of obligations created
- `expected_completion_by` — deadline
- `completion_timestamp` — when actually completed
- `parent_workflow_id` — if this is sub-workflow
- `linked_audit_events` — related audit events
- `created_at` — timestamp
- `updated_at` — last state change
- `completed_at` — when workflow finished

**Indexes:**
- (case_id)
- (current_state)
- (current_step_id)

### WorkflowStep

**Purpose:** Individual step within a workflow

**Attributes:**
- `id` (UUID) — step identity
- `workflow_id` — parent workflow
- `step_sequence` — execution order
- `step_type` — Action, Decision, Wait, Validate, Escalate, Recover
- `step_name` — human-readable name
- `step_status` — Not Started, Running, Waiting, Blocked, Complete, Failed
- `responsible_actor` — who performs this step
- `tool_or_action` — what tool/system invoked
- `tool_inputs` — parameters for action
- `tool_outputs` — result of action
- `start_timestamp` — when started
- `completion_timestamp` — when finished
- `timeout_duration` — how long to wait before timeout
- `retry_count` — times retried
- `max_retries` — max allowed retries
- `dependencies` — array of prerequisite step IDs
- `next_step_on_success` — what step if successful
- `next_step_on_failure` — what step if failed
- `error_handling` — Retry, Escalate, Alternate Path
- `preconditions` — what must be true to start
- `postconditions` — what should be true after
- `version` — concurrency control version

**Indexes:**
- (workflow_id, step_sequence)
- (step_status)

### ActionAttempt

**Purpose:** Record an attempt to execute a tool or action

**Attributes:**
- `id` (UUID) — attempt identity
- `workflow_step_id` — which step triggered this
- `case_id` — associated case
- `action_type` — Write, Read, Retry, Query, Recover
- `target_system` — which system this targets
- `tool_name` — which tool invoked
- `tool_version` — tool contract version
- `idempotency_key` — for deduplication
- `correlation_id` — trace this execution
- `request_payload` — what was sent
- `request_timestamp` — when sent
- `response_status` — Accepted, Processing, Completed, Failed, Timeout, Uncertain
- `response_payload` — what was received
- `response_timestamp` — when response received
- `uncertainty_flag` — boolean, was outcome uncertain?
- `status_query_initiated` — if uncertain, did we query status?
- `status_query_result` — outcome of status query
- `retry_count` — times retried
- `max_retries` — allowed retries
- `precondition_check_at_execution` — preconditions verified?
- `authorization_check_at_execution` — authority verified?
- `concurrency_version_checked` — version conflict check?
- `error_code` — if failed, error classification
- `error_message` — error description
- `technical_trace` — system trace/stack for debugging
- `executed_by` — who/what executed
- `audit_events` — related audit event IDs

**Indexes:**
- (workflow_step_id)
- (idempotency_key) — for deduplication
- (correlation_id) — for tracing
- (case_id, response_status)

### ValidationObligation

**Purpose:** Record what must be verified after execution

**Attributes:**
- `id` (UUID) — obligation identity
- `case_id` — associated case
- `workflow_step_id` — which action created obligation
- `created_by_action_attempt_id` — which action
- `expected_observation` — what should we find
- `observation_type` — Financial Movement, Status Change, Reconciliation Match
- `evidence_requirement_id` — what evidence needed to verify
- `validation_rule_id` — which rule validates result
- `validation_rule_version` — version of rule
- `due_window_start` — earliest to check
- `due_window_end` — latest to check before escalation
- `check_timestamp` — when actually checked
- `check_result` — Success, Mismatch, Missing Evidence, Inconclusive
- `supporting_evidence_ids` — what evidence supported result
- `mismatch_details` — if check failed, what was wrong
- `escalation_if_deadline_breach` — yes/no
- `escalation_timestamp` — if deadline missed
- `workflow_disposition` — Close, Reopen, Escalate, Hold
- `disposition_timestamp` — when obligation resolved
- `audit_event_ids` — related audit events

**Indexes:**
- (case_id)
- (due_window_end) — for deadline management
- (check_result)

### ExportRecord

**Purpose:** Track delivery to downstream destinations

**Attributes:**
- `id` (UUID) — export identity
- `case_id` — associated case
- `transaction_ids` — which transactions exported
- `transaction_versions` — versions exported
- `export_set_id` — batch identity
- `destination_system` — ERP, GL, Inventory, Analytics
- `destination_reference` — destination's export ID
- `export_timestamp` — when sent
- `export_content_hash` — checksum of what was sent
- `acceptance_status` — Not Sent, Sent, Accepted, Rejected, Processing
- `acceptance_timestamp` — when destination acknowledged
- `rejection_reason` — if rejected, why
- `processing_status` — Not Started, Processing, Completed, Failed
- `processing_result` — result/error from destination
- `posting_reference` — GL posting ID if applicable
- `duplicate_check_performed` — yes/no
- `duplicate_found` — yes/no/unknown
- `prior_versions` — array of earlier export attempts
- `retry_count` — times retried
- `max_retries` — retry limit
- `superseded_by_export_id` — if this export was replaced
- `related_audit_events` — audit trail

**Indexes:**
- (case_id)
- (destination_system, acceptance_status)
- (export_timestamp)

### AuditEvent

**Purpose:** Immutable record of all material changes

**Attributes:**
- `id` (UUID) — event identity
- `event_type` — Case Created, Exception Detected, Evidence Retrieved, Decision Made, Action Attempted, Validation Completed, etc.
- `event_timestamp` — when event occurred
- `actor_id` — who caused event (system or user)
- `actor_type` — User, System, Agent, Timer
- `actor_role` — Finance, IT, etc.
- `subject_entity_type` — Case, Exception, Workflow, etc.
- `subject_entity_ids` — what entities affected
- `before_state` — prior state (if state change)
- `after_state` — new state
- `before_values` — prior attribute values
- `after_values` — new attribute values
- `event_reason` — why this happened
- `correlation_id` — trace across events
- `causation_id` — what event triggered this
- `related_case_ids` — associated cases
- `related_workflow_ids` — associated workflows
- `evidence_references` — what evidence relates
- `compliance_flags` — whether event is compliance-relevant
- `retention_until` — how long to retain this event
- `immutable` — marked immutable after creation
- `created_at` — timestamp (with microsecond precision)

**Indexes:**
- (event_timestamp DESC) — for chronological queries
- (subject_entity_ids) — for case history
- (actor_id, event_timestamp) — for user activity
- (event_type) — for event filtering
- (correlation_id) — for tracing

## Data Integrity Constraints

1. **Referential Integrity**
   - Every Case must have at least one Exception
   - Every Exception must belong to exactly one Case
   - Every FindingStatement must reference at least one EvidenceSnapshot
   - Every HumanDecision must reference exactly one Recommendation

2. **Concurrency Control**
   - Use optimistic locking with version numbers
   - Workflows use sequence numbers for step ordering
   - ActionAttempts use idempotency keys for deduplication

3. **Financial Accuracy**
   - All amounts stored as exact decimal (NUMERIC/DECIMAL type)
   - Currency is explicit (not assumed)
   - Signed correctly (positive sales, negative refunds)
   - Totals reconcile across components

4. **Audit Trail**
   - AuditEvent records are append-only (never updated)
   - SourceRecord payloads are immutable after archiving
   - CanonicalTransaction versions track changes
   - Policy decisions record timestamp and actor

5. **Temporal Consistency**
   - Event times, processing times, business dates kept distinct
   - Settlement dates tracked separately
   - GL accounting dates tracked separately
   - Effective dates for policy versions tracked
