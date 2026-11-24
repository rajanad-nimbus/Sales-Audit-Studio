---
label: wayfinder:map
---
# Map: Evidence panel and agent evidence quality measures

## Destination

Two decision-ready specs in one document, `documents/EVIDENCE_PANEL_AND_AGENT_QUALITY.md`: (1) the evidence panel an auditor or finance approver uses to decide a case; (2) the measures that tell us whether agents' evidence and actions are good, and what a bad reading triggers. The measures name metrics and signals and leave target numbers open until agents run on real volume. Planning only.

## Notes

- Builds on `documents/AUDIT_FAILURE_EVIDENCE_SCENARIOS.md` (evidence model, agent levels, case lifecycle, ReSA comparison). Do not reopen those decisions.
- Panel users: auditor deciding a case and finance approver (override, financial effect). Analysts and IT use it read-only at first.
- Measures protect against: wrong act (led by reopen rate per class), wrong finding (human rejection as label), missed escalation, cost and delay.
- UI style: mimic Ontology Studio, keep screens simple, navy accent via tokens (see memory feedback files).
- Tracker: local markdown here; blocking via `blocked_by`. Use /grilling and /domain-modeling for grilling tickets, /prototype for the prototype ticket.

## Decisions so far

- [Which quality signals the data already records](tickets/01-signals-available.md) — reopen, rejection, override and decision signals exist; missing the reopen-to-act link, finding-to-evidence links, SLA breach and escalation times, run duration, and store-day-agent run records.

- [Evidence panel layout for deciding a case](tickets/02-panel-prototype.md) — one case panel with a state banner, evidence list with findings, recommendation, role actions; prototype built, not yet reviewed by a person.
- [Panel states and actions per role](tickets/03-panel-states-and-actions.md) — seven states, decision actions blocked until clear, reasons required, override needs a different finance approver.
- [Definition of each quality measure](tickets/04-metric-definitions.md) — wrong act, wrong finding, missed escalation, cost and delay defined; 30-day window; 30-event minimum; seven signals to add.
- [What a bad reading triggers](tickets/05-metric-triggers.md) — breach demotes the class or notifies; 14 days plus a 20-case review to earn back; thresholds unset.
- [Write the evidence panel and agent quality document](tickets/06-write-spec-document.md) — destination reached: `documents/EVIDENCE_PANEL_AND_AGENT_QUALITY.md`.

## Not yet specified

- Target numbers for each metric (need real volume).
- Spot-check sampling design for act resolutions.
- A metrics dashboard for the measures (where and for whom) — hangs on which signals exist.
- Per-class act ceilings and the overdue-source deadline: provisional values are set (decision log section J); a business value should replace them.

## Out of scope

- Building the panel, metrics, or any agent change.
- Reopening decisions already made in the first map.
