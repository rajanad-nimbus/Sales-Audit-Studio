---
label: wayfinder:map
---
# Map: Build plan for the audit evidence decisions

## Destination

An ordered, sliced implementation plan in `documents/AUDIT_EVIDENCE_BUILD_PLAN.md`: each decided capability mapped to the code it touches, split into slices that each fit one agent session, ordered by dependency, with a rollout that keeps agent actions safe. A plan only; nothing is built on this map.

## Notes

- Decisions to implement are fixed in `documents/AUDIT_FAILURE_EVIDENCE_SCENARIOS.md`, `documents/EVIDENCE_PANEL_AND_AGENT_QUALITY.md` and `documents/AUDIT_EVIDENCE_DECISION_LOG.md`. Do not reopen them. Assumed and provisional values are inputs, not questions.
- Capabilities to cover: findings on evidence; change-based staleness; provenance; conflict block and ready rule; agent levels and class map in the ontology; kill switch, daily cap, reopen reversal; group-level case dedupe; exposure-based priority and agent-driven escalation; carry-forward with age limit; unexplained-variance and late-source cases; per-code override and who-may-override setting; evidence list on error definitions; the evidence panel; the missing quality signals and measures.
- Repo conventions: tests per feature (use /tdd), no commits unless asked, mimic Ontology Studio, navy tokens, be careful with the user's parallel edits.
- Tracker: local markdown here; blocking via `blocked_by`.

## Decisions so far

(none yet)

## Not yet specified

- Estimates per slice, once touch points are known.
- Data migration needs for existing cases and evidence snapshots.
- Ontology Studio changes needed for the class map and evidence requirements (a separate app).

## Out of scope

- Writing the code.
- Reopening decisions already made.
