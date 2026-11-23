---
title: Case lifecycle for a failing audit
label: wayfinder:grilling
status: closed
assignee: claude
blocked_by: []
---
## Question
When is a case registered (per error, per store day, deduped?), when is evidence regathered, how does escalation and closure work, and how do remaining cases gate store-day close?

## Resolution
Decided with the user (all recommendations accepted):
- **Grouping (Q1):** one case per group of exceptions (failure type, store, business date, currency, detection rule). A reconciliation rerun updates the existing open case; a new case is created only after the previous one closed. Requirement to verify when built: the code is not confirmed to enforce this.
- **Regather (Q2):** on source change (marks evidence stale), on demand, and at decision time. If a regather changes a finding after a recommendation was proposed, the recommendation is marked "needs review".
- **Escalation (Q3):** priority uses exposure, not raw amount (timing differences with zero exposure don't outrank real loss); agent-driven triggers escalate regardless of amount: conflicting evidence, a contradicting finding, or a collect-only class.
- **Close gate (Q4):** only cases whose error definition blocks close gate the day; non-blocking cases carry forward to the next day but become blocking after an age limit. Deliberately looser than ReSA (all errors cleared before Audited); the age limit prevents carry-forward from hiding cases.
