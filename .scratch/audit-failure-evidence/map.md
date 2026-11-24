---
label: wayfinder:map
---
# Map: Audit failure scenarios — cases registered, evidence gathered by Agents

## Destination

A decision-ready spec, `documents/AUDIT_FAILURE_EVIDENCE_SCENARIOS.md`: for each audit failure ZeTSA can raise, what case is registered, what evidence Agents gather, what they may do with it, and — per area — whether ZeTSA follows Oracle ReSA or a justified alternative. Planning only; no build.

## Notes

- Domain: ZeTSA sales audit (store days, cases, evidence, agents). Existing material to reuse, not redo: `documents/STORE_DAY_BUSINESS_SCENARIOS.md`, `NIMBUS_SCENARIOS.md`, `NIMBUS_AGENTS.md`, `RESA_DIFFERENCES.md`, `RESA_PARITY.md`, `AUDIT_PROCESS.md`, `resa-1501-ug.pdf`.
- Standing preference: where ZeTSA differs from ReSA, present the user a choice — "ReSA approach" vs "better alternative" — and the alternative must be justified by a concrete audit benefit, not novelty.
- Default stance proposed: Agents collect evidence and propose; humans decide non-trivial failures (matches today's duplicate/clean-day agents for trivial cases).
- Tracker: local markdown (`.scratch/audit-failure-evidence/`). Tickets in `tickets/`, blocking via `blocked_by`.
- Use /grilling and /domain-modeling for grilling tickets.

## Decisions so far

- [How Oracle ReSA handles an audit failure end to end](tickets/01-resa-failure-handling.md) — fix or override each error, then auto-Audited; no evidence, case, approver or override reason; required/HQ-override rules are schema-only, not in the guide.

- [Inventory every failing scenario already gathered](tickets/02-failure-scenario-inventory.md) — 16 failure rows, 6 gaps; evidence today is link-existence only; agents resolve only exact duplicates and in-tolerance over/short.

- [What counts as evidence on a case](tickets/03-evidence-model.md) — link baseline + optional agent finding; change-based staleness; agents never override; full provenance; ready needs complete AND non-contradicting; conflicts block.

- [What Agents may do with evidence per failure class](tickets/04-agent-role-boundary.md) — L1 collect / L2 propose / L3 act; L3 needs deterministic proof + reversible + under ceiling + evidence ready; class map in ontology; kill switch and daily cap; reopen reverses.

- [Follow ReSA or take a better approach, area by area](tickets/05-resa-vs-alternative.md) — alternative chosen in all four areas (cases, per-code override strictness, proposal+approver with agent exception, provenance audit trail); store/HQ override split not added.

- [Case lifecycle for a failing audit](tickets/06-case-lifecycle.md) — one case per group, rerun updates open case; regather on change/demand/decision; priority by exposure + agent triggers; non-blocking cases carry forward with age limit.

- [Write the audit failure evidence scenarios document](tickets/07-write-destination-doc.md) — destination reached: `documents/AUDIT_FAILURE_EVIDENCE_SCENARIOS.md`.

## Not yet specified

- Nothing left in the fog for this destination. The evidence panel, quality measures and spot-check sampling moved to the follow-on map `../evidence-panel-and-quality/map.md`; provisional values are in `documents/AUDIT_EVIDENCE_DECISION_LOG.md`.

## Out of scope

- Implementing any agent, rule, or UI change.
- Authoring audit rules (belongs to Ontology Studio).
