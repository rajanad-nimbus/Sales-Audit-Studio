# Evidence panel and agent quality measures

Two specs that follow [AUDIT_FAILURE_EVIDENCE_SCENARIOS.md](AUDIT_FAILURE_EVIDENCE_SCENARIOS.md): the panel an auditor or finance approver uses to decide a case, and the measures that show whether agents are doing well. Every decision here was taken on a blanket instruction to use the best recommendation, so **none is confirmed one by one**. See [AUDIT_EVIDENCE_DECISION_LOG.md](AUDIT_EVIDENCE_DECISION_LOG.md), sections F to I, to review each. Target numbers are left unset until agents have run on real volume.

## 1. Evidence panel

One panel on the case screen. A rough prototype for reaction is at `.scratch/evidence-panel-and-quality/prototype/panel.html` (open it in a browser; the state and role selectors change what it shows). It uses the navy theme tokens and was not reviewed by a person.

### Contents, top to bottom

1. **Header:** case number, failure class, agent level (collect, propose or act), priority, exposure, SLA time left.
2. **State banner:** shown only when the state needs attention.
3. **Evidence list:** each required item with status (Complete or Unavailable), source system, retrieval time and data version, the agent finding (verdict and cited records) and badges for Stale or Conflict.
4. **Recommendation:** the proposal, who produced it (agent or rule), provenance, and a "needs review" mark if a finding changed afterward.
5. **Actions:** only those the role may take in the current state.

### States and what they block

| State | Meaning | Decision actions |
| --- | --- | --- |
| Collecting | Required evidence still being gathered | Blocked |
| Ready | All required found, no contradiction, no conflict | Allowed |
| Conflict | Sources, or a human item and an agent finding, disagree | Blocked until a person resolves it |
| Stale | Source data changed; regather running | Blocked until the regather ends |
| Needs review | A finding changed after the recommendation | Blocked until a person marks it reviewed |
| Override pending | An evidence override awaits a second approver | Approval of the override is for the finance role only |
| Decided | A decision is recorded | Read-only |

### Actions by role

| Action | Auditor | Finance approver | Analyst, IT |
| --- | --- | --- | --- |
| Approve or reject the recommendation (reject needs a reason) | Yes | Yes, with the financial effect shown | No |
| Request more evidence, provide evidence, add a note | Yes | Yes | Notes only |
| Resolve a conflict (choose a side, give a reason) | Yes | No | No |
| Request an evidence override (reason and financial effect), where the error code lets that role override | Yes | No | No |
| Approve an evidence override (approver differs from the requester) | No | Yes | No |
| Escalate, snooze | Yes | Yes | No |

Agents never appear as an actor on these actions. Their output is read-only content in the evidence list and recommendation.

## 2. Agent quality measures

### Purpose

Show whether agents' evidence and actions are good enough to stay at their level, and move a class down when they are not. The first concern is **wrong act**, because it has financial consequences.

### Measures

| Measure | Counts | Over | Label source |
| --- | --- | --- | --- |
| **Wrong act rate** | Agent actions later reversed: the case or the store day was reopened, with reopen reason "agent action was wrong" | Agent actions in the window | Reopen events, plus spot-check outcomes |
| **Wrong finding rate** | Findings a person later contradicted: evidence rejected, a recommendation rejected despite a supporting finding, or conflicting human evidence added | Findings produced | Human rejections and decisions |
| **Missed escalation rate** | Cases that met an escalation trigger (conflict, contradicting finding, collect-level class, SLA breach) but were decided without escalating | Cases that met a trigger | Case and decision history |
| **Cost and delay** | Gather duration (median and 95th percentile), cost per case, and the share of runs that hit the turn, cost or time limit | Agent runs | Run records |

All measures use a rolling 30 days and are cut by failure class, agent and model version. Below 30 events a measure shows counts only, not a rate. A reopen must carry a coded reason, so reopens unrelated to the agent do not count.

### Signals that exist and that must be added

The research note is `.scratch/evidence-panel-and-quality/research/01-signals-available.md`.

| Measure | Exists today | Must be added |
| --- | --- | --- |
| Wrong act | `STORE_DAY_REOPENED` events with reopener and reason; `DUPLICATE_SALE_EXCLUDED` events | A case reopen event, an un-exclude event, a link from a reopen to the agent action it undid, a coded reopen reason |
| Wrong finding | Evidence rejection and override fields and events; human decisions against recommendations | Findings linked to the evidence they cite, recommendation origin (agent or rule), a record of whether a finding later held |
| Missed escalation | Priority, SLA due time, snooze, an "Escalated" decision | SLA breach record, escalation time and target, priority history, case closed time |
| Cost and delay | Investigator run events with tokens; cost and turns on the SDK path only | Run duration, cost on every path, a record for every agent run including the store-day agent, a run table with indexed cost and duration |

### What a bad reading triggers

| Reading | Effect |
| --- | --- |
| Wrong act rate above its threshold | The class kill switch trips automatically and the class drops from act to propose. A person re-enables it. |
| Wrong finding rate above its threshold | The class drops from propose to collect, and a person is notified. |
| Missed escalation or cost and delay above threshold | Notification only. No level change. |

- **Who is notified:** the agent owner (IT) and the finance lead.
- **Earning a level back:** the class stays at the lower level for at least 14 days. Then a person reviews at least 20 recent cases and re-enables it.
- **Thresholds are unset.** They need real volume to choose.

## 3. Provisional values

These have no source in the repository and are placeholders to replace with business values. All are logged in section J of the decision log.

| Value | Provisional |
| --- | --- |
| Act ceiling: over/short | $5 (the tolerance in scenario 3) |
| Act ceiling: exact duplicate, POS and Shopify | $500 per case. Today there is no ceiling, so this adds a limit. |
| Daily cap on act actions | 200 per class per day |
| Overdue deadline for a missing source | 12 hours after the business date ends |
| Carry-forward age limit | 3 business days |
| Spot-check sampling | 5% of act resolutions per class per week, at least 5, to an auditor |

## 4. Still open

- The panel prototype has not been reviewed by a person. Layout may change.
- Target thresholds for every measure.
- A metrics dashboard: where the measures are shown and for whom.
- Every item marked Assumed or Provisional in the decision log.
