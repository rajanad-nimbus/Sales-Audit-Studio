---
title: What Agents may do with evidence per failure class
label: wayfinder:grilling
status: closed
assignee: claude
blocked_by: [02-failure-scenario-inventory]
---
## Question
For each failure class, is the Agent collect-only, collect-and-propose, or allowed to act (as with exact duplicates and clean-day sign-off)? Define the criteria that move a class between levels and the guardrails (limits, second approver, audit event).

## Resolution
Decided with the user (all recommendations accepted):
- **Levels:** L1 collect, L2 propose (person decides), L3 act (agent resolves with audit event).
- **Criteria (Q1):** L3 needs ALL of: deterministic proof (rule over data; an LLM finding alone never qualifies), zero or reversible financial effect, amount under a per-class ceiling, and evidence ready (per the evidence-model rule). L2 needs evidence ready only. Everything else is L1.
- **Class map (Q2), held in the ontology so it is configurable:** L3 = exact duplicate sale, over/short within tolerance. L2 = timing difference, amount mismatch, unmatched sale, orphan payment, bank variance/missing deposit, GL variance/missing GL posting, discount missing reason. L1 = missing refund, missing transactions/incomplete feed, balancing variance, transaction out of balance.
- **Guardrails (Q3):** audit event + existing on/off option, plus a per-class kill switch and a daily cap on L3 actions. Sampling for spot-checks deferred (fog).
- **Reversal (Q4):** reopening the case undoes an L3 action: restores the transaction, re-totals, day returns to Auditing if closed.
