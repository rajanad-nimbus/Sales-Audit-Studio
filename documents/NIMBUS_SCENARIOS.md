# Nimbus Flagship Scenarios

## Scenario 1: Settlement Timing Difference (ZA-20261006-0147)

**Exception Details:**
- Amount: $550
- Estimated Exposure: $0 (timing only, no loss)
- Policy Rule: ST-004
- Autonomous Disposition Threshold: $250
- Required Action Class: Disposition Only (no financial write)

**Context:**
Sales TX total: $148,725.40  
Payment total: $148,175.40  
Bank deposit: $148,175.40  
Variance: $550 (unmatched sales)

### Investigation Process

**Step 1: Detect the Exception**
```
Reconciliation Engine:
  Input: Canonical sales and payment records
  Rule: Daily settlement reconciliation
  Detection: Sales - Payments = $550 variance
  Output: Exception created with type TIMING_DIFFERENCE
```

**Step 2: Retrieve Required Evidence**
```
Investigation Agent:
  Ontology lookup: TimingDifference evidence requirements
  Required: [Original refund record, Refund timestamp, Settlement batch status]
  
  Evidence queries:
  - Retrieve refund after sales cutoff (23:00)
  - Check settlement batch inclusion
  - Confirm refund amount $550
  
  Result: 
    - Refund found: $550, timestamp 23:15 (after 23:00 cutoff)
    - Settlement batch: Included in next-cycle settlement
    - Confidence: High
```

**Step 3: Diagnose and Recommend**
```
Investigation Agent:
  Finding: "Supported timing explanation - $550 refund occurred after cutoff"
  Evidence: [Refund Confirmation, Timestamp Log, Settlement Batch Record]
  Confidence: High
  
  Recommendation:
    Disposition: Apply timing classification
    Action: Schedule validation for next-cycle reconciliation
    Workflow: Timing Disposition → Wait for settlement → Validate → Close
    Financial Impact: None (no write)
```

**Step 4: Evaluate Policy**
```
Policy Engine:
  Input: Timing disposition, $550 amount, policy ST-004
  Rule: Amount > $250 requires Finance approval
  Threshold: $250
  Current Amount: $550
  Result: APPROVAL REQUIRED
  Authority Required: Finance Director level
  Reason: Exceeds autonomous action threshold
```

**Step 5: Finance Approval**
```
Finance Decision:
  Case shown: 
    - Situation: $550 unmatched sales due to late refund
    - Evidence: 3 supporting documents
    - Diagnosis: Timing difference (supported)
    - Recommendation: Timing disposition
    - Policy result: Approval required
    - Scope: Apply timing classification only
    - Validation: Monitor next-cycle settlement
  
  Actor: Finance Director
  Decision: APPROVED
  Timestamp: 2026-10-06 09:22 UTC
  Scope: Timing disposition, no financial write
```

**Step 6: Execute Disposition**
```
Workflow Execution:
  Step 1: Apply timing classification
    - Update exception status: Timing
    - Link to next settlement reconciliation
    - Record decision and evidence
  
  Step 2: Create validation obligation
    - Expected observation: Refund in next settlement batch
    - Due window: Oct 7 settlement cycle
    - Evidence type: Settlement batch confirmation
    - Check rule: Refund amount matches ($550)
  
  State transition: Executed — Validation Pending
```

**Step 7: Validation (Next Business Cycle)**
```
Validation Agent:
  Retrieves: Next settlement batch (Oct 7)
  Evidence: Settlement batch includes $550 refund
  Verification rule: Amount and identity match
  
  Deterministic check:
    Expected: Refund $550 in settlement
    Actual: Refund $550 found in batch
    Status: VERIFIED
  
  Result: CLOSE case
  Timestamp: 2026-10-07 06:45 UTC
```

**Outcome:**
- Exception resolved through timing disposition
- No financial write executed
- Validated through next-cycle settlement evidence
- Case closed with complete audit trail
- Zero false positives; timing explanation confirmed

---

## Scenario 2: Duplicate Sales Transaction

**Exception Details:**
- Transaction ID: 84291753
- Amount: $186.42
- Exception Type: DUPLICATE_SALES_TRANSACTION
- Source Code: DUP_TRAN
- Potential Impact: GL export double-posting

**Context:**
Two audit records showing same sale but conflicting evidence about whether one or two captures occurred.

### Investigation Process

**Step 1: Detect Duplicate**
```
Audit Engine:
  Rule: Check transaction ID uniqueness per business date/register
  Finding: Transaction 84291753 appears twice in audit records
  Evidence: Two CanonicalTransaction entries, same source IDs
  Exception: DUPLICATE_SALES_TRANSACTION created
```

**Step 2: Gather Conflicting Evidence**
```
Investigation Agent:
  Evidence queries:
  
  1. Payment processor capture records
     Result: ONE capture for $186.42 (not two)
  
  2. Settlement batch search
     Result: $186.42 appears once in settlement
  
  3. Store POS journal
     Result: ONE register entry for transaction
  
  4. Bank deposit
     Result: $186.42 deposits only once
  
  Conclusion: 
    Finding: One legitimate sale, one duplicate audit record
    Evidence: Payment, settlement, deposit all show single transaction
    Confidence: High
```

**Step 3: Identify Legitimate Transaction**
```
Investigation Agent:
  Cross-reference:
    - Original capture time (confirmed: 14:22:33)
    - Settlement batch (confirmed: in Oct 6 batch)
    - Bank posting (confirmed: deposited Oct 6)
    - Amount (confirmed: $186.42)
  
  Recommendation:
    - Keep legitimate transaction record
    - Mark duplicate for quarantine
    - Protect from export duplication
    - No financial adjustment needed (never posted twice)
```

**Step 4: Policy Evaluation**
```
Policy Engine:
  Action: Apply governed disposition (quarantine duplicate)
  Financial write: No (duplicate detected pre-posting)
  Export impact: Yes (must prevent duplicate GL posting)
  Automation allowed: Yes (evidence complete, policy clear)
  Authority: Autonomous within IT scope
```

**Step 5: Execute Governed Disposition**
```
Resolution Workflow:
  
  Step 1: Mark duplicate record
    - Update CanonicalTransaction status: Quarantined
    - Link: Duplicate of transaction_id (legitimate)
    - Reason: Duplicate audit record, not actual sale
  
  Step 2: Protect legitimate transaction
    - Ensure legitimate record is NOT suppressed
    - Verify GL posting reference if already posted
    - Flag: "Duplicate resolved; export original only"
  
  Step 3: Update export controls
    - Add duplicate ID to "do not export" list
    - Link to this case for future export runs
    - Document: Quarantine reason and decision
  
  State: Executed
```

**Step 6: Validate No Duplication**
```
Validation Agent:
  
  Checks:
  1. Legitimate transaction still exportable
     Result: Yes, normal export status
  
  2. Duplicate transaction excluded from exports
     Result: Yes, quarantine prevents inclusion
  
  3. GL impact confirmed
     Result: Only one posting (the legitimate one)
  
  4. Settlement reconciliation updated
     Result: Duplicate no longer blocks matching
  
  Verification: PASSED
```

**Outcome:**
- Duplicate quarantined before false GL posting
- Legitimate sale protected and exported correctly
- No financial correction needed (error prevented at source)
- Audit trail complete with discovery evidence
- Case closed with high confidence

---

## Scenario 3: Zero-Touch Card Tender Over/Short

**Exception Details:**
- Variance: $3.42
- Policy Tolerance: $5.00 (within tolerance)
- Evidence Status: Complete
- Automation Mode: Governed Automation
- Expected Human Involvement: None

**Context:**
Card tender total vs. processor settlement shows $3.42 short. Evidence is complete and within approved tolerance.

### Automated Process

**Step 1: Detect**
```
Reconciliation:
  Card tenders: $47,829.16
  Processor settlement: $47,825.74
  Variance: $3.42
  Policy: Card tender tolerance $5.00
  Exceeds tolerance: No
```

**Step 2: Investigate Autonomously**
```
Investigation Agent:
  Evidence required: [Tender records, Processor batch, Bank posting]
  Evidence status: All available, current
  
  Analysis:
    - All card transactions reconcile individually
    - Processor fee? No unexpected fees
    - Currency rounding? USD, no conversion
    - Timing? All in same settlement window
    - Possible cause: Rounding or processor adjustment
  
  Confidence: Medium (within tolerance, no clear cause)
  Recommendation: Apply tolerance disposition
```

**Step 3: Policy Evaluation (Autonomous)**
```
Policy Engine:
  Amount: $3.42
  Tolerance: $5.00
  Within tolerance: Yes
  Automation mode: Governed Automation
  Authority required: No
  Decision: APPROVE AUTONOMOUS EXECUTION
```

**Step 4: Execute Disposition**
```
Workflow:
  Action: Apply tolerance disposition
  Entry: Mark as "Tolerance — Rounding/Processor Adjustment"
  Amount: $3.42 accepted variance
  No financial write
  No escalation
```

**Step 5: Validate**
```
Validation Agent:
  Check: Variance remains $3.42 (no new activity)
  Result: Confirmed
  
  Verification: PASSED — Closed
```

**Case Lifecycle (Automated):**
- Detection: 09:14:22
- Investigation: 09:14:41 (27 seconds)
- Policy evaluation: 09:14:42 (1 second)
- Execution: 09:14:43 (1 second)
- Validation: 09:15:12 (29 seconds in next scheduled check)
- **Total time: ~2 minutes**
- **Human involvement: 0 minutes**
- **Avoided manual review: Yes**

**Outcome:**
- Exception resolved without human touch
- Within approved automation parameters
- Complete audit trail captures investigation
- Tolerance-based disposition documented

---

## Scenario 4: Return Missing Tender Evidence

**Exception Details:**
- Source Context: RETURN_TENDER_REQ
- Amount: $94.22
- Missing Evidence: Payment refund confirmation
- Evidence Completeness: 60%
- Policy Requirement: 100% evidence required
- Human Review: Required

**Context:**
A return transaction $94.22 exists, but payment refund confirmation not yet available from processor.

### Investigation Process with Escalation

**Step 1: Detect**
```
Audit Exception:
  Exception: Return without refund confirmation
  Amount: $94.22
  Source: POS return record
  Status: Pending refund evidence
```

**Step 2: Investigate**
```
Investigation Agent:
  Evidence required: [Return record, Refund confirmation, Settlement]
  Evidence available: Return record only
  Evidence missing: Refund confirmation
  
  Status:
    Return record: Available (complete)
    Refund confirmation: Pending (not yet received)
    Settlement evidence: Not yet due
  
  Completeness: 60% (have return, missing refund proof)
  
  Options:
    1. Request more evidence (wait for processor refund proof)
    2. Route to analyst for manual investigation
    3. Escalate (if deadline approaches without evidence)
  
  Recommendation: HOLD — Request More Evidence
```

**Step 3: Policy Evaluation**
```
Policy Engine:
  Evidence completeness: 60% (below 100% requirement)
  Policy decision: REQUEST_MORE_EVIDENCE
  Requires: Refund processor confirmation
  Timeline: Due within 24 hours (processor lag)
  If deadline passes: Escalate to analyst
```

**Step 4: Finance Decision Point**
```
Finance sees:
  Case: ZA-20261006-0152
  Situation: $94.22 return awaiting refund proof
  Evidence: Return record present
  Missing: Refund confirmation from processor
  Policy: Cannot disposition without complete evidence
  
  Options:
  A) WAIT — Monitor for refund confirmation (automated)
  B) REQUEST EVIDENCE — Manually query processor
  C) ESCALATE — Route to analyst for investigation
  
  Selection: Option A (Wait) — set monitoring
  Deadline: +24 hours
  Escalation trigger: If processor refund not received by deadline
```

**Step 5: Wait for Evidence**
```
Workflow:
  State: AWAITING_EVIDENCE
  Monitoring: For refund confirmation from processor
  Deadline: 2026-10-07 09:14 UTC
  Escalation: If deadline passes
  
  [Processor refund arrives 2026-10-07 08:30 UTC]
  
  Evidence updated: Refund confirmation received
  Evidence completeness: 100%
  Triggers: Resume investigation
```

**Step 6: Resume Investigation with Complete Evidence**
```
Investigation Agent:
  All evidence now available:
    - Return record: $94.22
    - Refund confirmation: $94.22 refund ID XXX
    - Match: Amounts and IDs align
  
  Confidence: High
  Finding: Return properly refunded
  
  Recommendation: Closure (no disposition needed)
```

**Step 7: Auto-Approve and Close**
```
Policy Engine:
  Evidence: Complete
  Confidence: High
  Automation: Permitted
  
  Execution:
    - Mark exception resolved
    - Link to refund confirmation
    - Close case
```

**Outcome:**
- Evidence gap identified early
- Deterministic hold (not abandoned)
- Human oversight (monitoring configured)
- Automatic resumption when evidence arrived
- Escalation path clear (analyst available if deadline missed)
- Zero false resolution; waited for actual proof

---

## Scenario 5: Store Day Not Ready to Load

**Exception Details:**
- Store: 0847 (Example Store)
- Business Date: 2026-10-06
- Status: Incomplete prerequisites
- Missing: End-of-day marker file
- Load status: Blocked (cannot process incomplete day)

**Context:**
Sales data received, but missing the end-of-day completion marker that certifies the day is closed at the store.

### Dependency Management

**Step 1: Detect Prerequisite Missing**
```
Ingestion Engine:
  Expected for business date Oct 6:
    ✓ Sales TLog received (normal time)
    ✗ EOD marker file (missing)
    ✓ Refund TLog received
    ✓ Control totals received
  
  Decision: HOLD — prerequisite not met
  Cannot proceed to audit without store certification
```

**Step 2: Exception Created**
```
Exception:
  Type: STORE_DAY_NOT_READY
  Reason: Missing end-of-day marker
  Store: 0847
  Business date: Oct 6
  Data available: Sales, refunds, controls
  Data awaiting: EOD certification
  
  Status: AWAITING_DEPENDENCY
```

**Step 3: IT Intervention Option**
```
IT Operations sees:
  Case: WAITING for EOD marker from store 0847
  Status: Data held (not discarded)
  Automated actions available:
    - Check store system status (automated)
    - Query store for EOD status (manual)
    - Retry EOD marker download (automated)
  
  IT Decision: Retry EOD marker download
```

**Step 4: Retry Mechanism**
```
Workflow:
  Action: Retry EOD marker download
  Attempt 1: 09:30 — No response
  Wait: 5 minutes
  
  Attempt 2: 09:35 — No response
  Wait: 10 minutes
  
  Attempt 3: 09:45 — EOD marker received!
  Status: SUCCESS
```

**Step 5: Resume Loading**
```
Ingestion:
  EOD marker: Received and validated
  Store certification: Confirmed
  Data readiness: 100%
  
  Action: Proceed with normal ingestion
    - Validate all three feeds together
    - Run audit engine on complete day
    - Detect any exceptions
    - Proceed to reconciliation
```

**Step 6: Validation After Completion**
```
Validation:
  Store 0847 day now complete:
    - All expected data present
    - EOD marker certifies completeness
    - Audit engine runs
    - Reconciliation proceeds
    - Cases created for exceptions
```

**Outcome:**
- Data held safely (not discarded)
- Dependency tracked explicitly
- Automated retry attempted
- Escalation path available (manual contact if retries exhausted)
- Normal processing resumed on prerequisite satisfaction
- Store day processed only when truly complete

---

## Dataset Consistency Requirements

### Exception Partition (128 Total)

| Category | Count | Status | Note |
|----------|-------|--------|------|
| Closed (autonomous) | 74 | Closed | Auto-handled, no analyst touch |
| Investigation complete | 31 | Various | Diagnosis complete, awaiting decisions |
| Investigation in-progress | 13 | Open | Currently investigating |
| Awaiting decision | 6 | Decision Pending | Finance approval needed |
| Validation pending | 2 | Validation | Executed, waiting outcome |
| Escalated | 2 | Escalated | Requires specialist attention |
| **Total** | **128** | | |

### Operational Metrics (Illustrative)

| Metric | Value | Basis |
|--------|-------|-------|
| Readiness | 91% | 117 closed/completed ÷ 128 total |
| Total Exception Value | $18,740 | Sum of all exception amounts |
| Estimated Exposure | $2,460 | Sum of exceptions requiring resolution |
| Hours Avoided | 22.6 | Automated cases × 2.8 hrs saved per case |
| Avg Investigation Time | 18 min | Time from exception to recommendation |
| Finance Decisions | 22 | Approvals required above automation threshold |

### Resettable Demo State

**Provided:**
- Consistent synthetic transaction dataset
- Predictable exception distribution
- Known ground truth for validation
- Repeatable scenario outcomes
- Clear before/after state for each workflow

**Reset capability:**
- Restore to initial state
- Replay specific scenarios
- Verify all workflows
- Test edge cases
