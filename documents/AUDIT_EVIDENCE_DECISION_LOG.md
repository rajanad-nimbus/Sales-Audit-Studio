# Decision log: audit failure evidence, agents, panel and quality measures

A review log for the work in [AUDIT_FAILURE_EVIDENCE_SCENARIOS.md](AUDIT_FAILURE_EVIDENCE_SCENARIOS.md) and [EVIDENCE_PANEL_AND_AGENT_QUALITY.md](EVIDENCE_PANEL_AND_AGENT_QUALITY.md). Every question that was asked, the answer chosen, and how firm that answer is.

**Status key**
- **Confirmed**: the user answered ("agree", "go ahead with your recommendations", "I like your recommendations").
- **Assumed**: the user gave a blanket instruction to proceed with the best recommendations (`/goal` of 2026-11-24) or said "continue" without answering. The recommendation was taken. **Review these first.**
- **Provisional**: a number or value with no source in the repository, chosen so the spec is complete. Replace with a business value.

Wayfinder maps: `.scratch/audit-failure-evidence/` and `.scratch/evidence-panel-and-quality/`.

## A. Evidence model (map 1)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| A1 | What does one evidence item assert? | Link-existence baseline plus an optional agent finding (what the source showed, verdict, cited ids). ReSA has only a free-text comment. | Confirmed |
| A2 | When is evidence stale? | Change-based; time threshold only as a fallback. | Confirmed |
| A3 | Who may add, reject or override evidence? | Agents add and regather, never reject or override. A regather flags conflict with human items. | Confirmed |
| A4 | What provenance? | Producer, tool trace, data version, cited ids. | Confirmed |
| A5 | How complete before decision? | All required found and no contradicting or inconclusive finding; minimum per class in the ontology. | Confirmed |
| A6 | Conflicting evidence? | Source-vs-source or human-vs-agent blocks "ready"; inconclusive only warns. | Confirmed |

## B. Agent levels (map 1)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| B1 | Criteria for each level? | Act needs deterministic proof, zero or reversible effect, amount under a ceiling, ready evidence. Propose needs ready evidence. Else collect. | Confirmed |
| B2 | Which class at which level? | Act: exact duplicate, over/short in tolerance. Propose: nine classes. Collect: four classes. Held in the ontology. | Confirmed |
| B3 | Guardrails on act? | Audit event, on/off option, per-class kill switch, daily cap. Sampling deferred. | Confirmed |
| B4 | Reversal? | Reopening the case undoes the action. | Confirmed |

## C. ReSA comparison (map 1)

| # | Area | Decision | Status |
| --- | --- | --- | --- |
| C1 | Error handling | Cases with evidence; agent-resolved errors close immediately. | Confirmed |
| C2 | Overrides | Reason and second approver by default; ontology may relax per code; no store/HQ split. | Confirmed |
| C3 | Resolution | Proposal and approver; act-level agent actions exempt. | Confirmed |
| C4 | Audit trail | ReSA trails plus provenance and agent/conflict events. | Confirmed |

## D. Case lifecycle (map 1)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| D1 | What is one case? | Per group; reruns update the open case. | Confirmed |
| D2 | When regather? | On source change, on demand, at decision time; changed finding marks recommendation "needs review". | Confirmed |
| D3 | Escalation? | Exposure-based priority; agent-driven triggers. | Confirmed |
| D4 | Close gate? | Only blocking cases; non-blocking carry forward with an age limit. | Confirmed |

## E. Gaps closed after the first document (map 1)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| E1 | $550 timing difference automatic or reviewed? | Act under the ceiling, propose above it. | Confirmed |
| E2 | Do unexplained variances get cases? | Yes, above tolerance. | Confirmed |
| E3 | Do missing or late sources register cases? | Yes, collect-level, once overdue. | Confirmed |
| E4 | Default level for unmapped types? | Collect. | Confirmed |
| E5 | Scenario 1 inconsistency | Not an inconsistency: "Disposition Only" is the action class; Finance approval already applies above $250. My earlier claim was wrong. | Corrected |

## F. Panel and quality map scope (map 2)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| F1 | Destination of the follow-on map | Two specs in one document: panel and measures. | Assumed ("continue") |
| F2 | Who is the panel for? | Auditor and finance approver, one panel with role-specific actions; others read-only. | Assumed ("continue") |
| F3 | What do measures protect against? | Wrong act (first), wrong finding, missed escalation, cost and delay. | Assumed ("continue") |

## G. Quality measures (map 2)

See section 2 of [EVIDENCE_PANEL_AND_AGENT_QUALITY.md](EVIDENCE_PANEL_AND_AGENT_QUALITY.md).

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| G1 | How is "wrong act" counted? | Agent actions later reversed (case or store day reopened and attributed) over agent actions, rolling 30 days, per class, agent and model version. | Assumed |
| G2 | What if a reopen has another cause? | A reopen must carry a coded reason; only "agent action was wrong" counts. | Assumed |
| G3 | How is "wrong finding" counted? | Findings later contradicted by a person (evidence rejected, supporting finding but recommendation rejected, conflicting human evidence) over findings produced. | Assumed |
| G4 | How is "missed escalation" counted? | Cases meeting an escalation trigger but decided without escalating, over cases meeting a trigger. | Assumed |
| G5 | What counts for cost and delay? | Gather duration (median and 95th percentile), cost per case, share of runs that hit the turn, cost or time limit. | Assumed |
| G6 | Minimum sample before a rate is shown as a rate? | 30 events; below that show counts only. | Assumed |
| G7 | Signals that must be added | Reopen-to-act link, case reopen event, finding-to-evidence links, recommendation origin, escalation timestamps and SLA breach, run duration, store-day agent run records. | Assumed |

## H. What a bad reading triggers (map 2)

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| H1 | What does a wrong-act breach do? | Trips the class kill switch automatically (act drops to propose); a person re-enables. | Assumed |
| H2 | What does a wrong-finding breach do? | Class drops from propose to collect; notify. | Assumed |
| H3 | Missed escalation and cost breaches? | Notify only; no level change. | Assumed |
| H4 | Who is notified? | Agent owner (IT) and finance lead. | Assumed |
| H5 | How does a class earn its way back? | Minimum 14 days at the lower level, then a person reviews at least 20 recent cases and re-enables. | Assumed |
| H6 | Threshold values | Left unset until real volume exists. | Assumed |

## I. Panel (map 2)

See section 1 of [EVIDENCE_PANEL_AND_AGENT_QUALITY.md](EVIDENCE_PANEL_AND_AGENT_QUALITY.md). Prototype: `.scratch/evidence-panel-and-quality/prototype/panel.html`.

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| I1 | Layout | One case panel: header, conflict and state banner, evidence list with findings, recommendation, role actions. | Assumed (prototype not reviewed by the user) |
| I2 | Which states? | Collecting, Ready, Conflict, Stale, Needs review, Override pending, Decided. | Assumed |
| I3 | What is blocked in each state? | Decision actions are blocked in Collecting, Conflict, Stale and Needs review. | Assumed |
| I4 | What do actions require? | Reject evidence and resolve conflict need a reason; an override needs a reason and financial effect and a second approver other than the requester. | Assumed |
| I5 | Analyst and IT | Read-only plus notes. | Assumed |

## J. Provisional values (first-map open points)

| # | Question | Value | Status |
| --- | --- | --- | --- |
| J1 | Act ceiling, timing difference | $250 per case (the autonomous threshold already in scenario 1, policy ST-004) | Provisional |
| J2 | Act ceiling, over/short | $5 (the tolerance in scenario 3) | Provisional |
| J3 | Act ceiling, exact duplicate (POS and Shopify) | $500 per case. No source in the repository, and today there is no ceiling, so this adds a limit. | Provisional |
| J4 | Daily cap on act actions | 200 per class per day | Provisional |
| J5 | Overdue deadline for a missing source | 12 hours after the business date ends | Provisional |
| J6 | The 20 draft class assignments | Accepted as drafted | Assumed |
| J7 | Spot-check sampling | 5% of act resolutions per class per week, at least 5, queued to an auditor; outcome feeds the wrong-act label | Provisional |
| J8 | Carry-forward age limit | 3 business days | Provisional |

## K. Last two open items

| # | Question | Decision | Status |
| --- | --- | --- | --- |
| K1 | What evidence do rule-raised codes carry when the ontology has no entry? | The error definition table gains an optional evidence list and an exposure setting. Until set, the code uses the default list, stays at collect (E4), and is flagged as "evidence not defined". | Assumed |
| K2 | Do later ReSA releases change the comparison? | Mostly no. They confirm the per-code Store Override and HQ Override flags (13.x, 14.0; HQ column in 24.0) and the "System Required" blocking flag (19.3, 24.0). No evidence concept, second approver or override reason found. Found by web search summaries only. | Checked, low confidence |
| K3 | Revisit C2 (no store/HQ override split)? | Yes, flagged for review. C2 rested on the 15.0.1 guide not defining the split, but the flags exist in other releases. Not changed here. | Open for the user |
