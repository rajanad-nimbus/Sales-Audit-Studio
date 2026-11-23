---
title: What counts as evidence on a case
label: wayfinder:grilling
status: closed
assignee: claude
blocked_by: [02-failure-scenario-inventory]
---
## Question
Define the evidence model: evidence kinds, provenance (source system, query, agent), freshness/staleness, who may add or override it, and how it ties to a recommendation. Compare with ReSA (no evidence concept expected; see research ticket if closed) and choose: ReSA approach vs alternative.

## Resolution
Decided with the user (all recommendations accepted):
1. **Shape (Q1):** every evidence item keeps the deterministic link-existence baseline; an agent may add a content finding (what the source showed + supported/contradicted/inconclusive + cited ids). ReSA has only a free-text comment, so this goes beyond it.
2. **Staleness (Q2):** change-based — evidence is stale when its source data changed (re-import, re-total, duplicate exclusion). Time threshold only as fallback for sources with no change signal.
3. **Who (Q3):** agents add and regather, never reject or override. Regather adds a new item and flags conflict with human items; humans are never silently superseded. Adding needs no approval; override keeps reason + second finance approver.
4. **Provenance (Q4):** producer (deterministic or agent+model version), tool/query trace, data version (ontology version + source batch), cited ids validated against retrieved records.
5. **Completeness (Q5):** ready = required items found AND no contradicting/inconclusive finding; minimum set per failure class, held in the ontology.
6. **Conflicts (Q6):** source-vs-source or human-vs-agent conflict blocks "ready" until a person resolves it; an inconclusive agent finding only warns. Agent may state a belief but does not settle conflicts.
