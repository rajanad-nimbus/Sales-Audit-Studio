---
title: Follow ReSA or take a better approach, area by area
label: wayfinder:grilling
status: closed
assignee: claude
blocked_by: []
---
## Question
For error handling, overrides, resolution and audit trail, present the user two options each — the ReSA approach and a better alternative with a concrete benefit and cost — and record the chosen one per area.

## Resolution
Decided with the user (all recommendations accepted). Basis: [ReSA research note](../research/01-resa-failure-handling.md).
| Area | ReSA | Chosen |
|---|---|---|
| Error handling | Error list; fix or override; all cleared -> Audited | **Alternative:** case with evidence, recommendation, decision. Errors an agent resolves at the act level close immediately and never reach a person's queue. |
| Overrides | Per-code override flags (schema only; guide defines no rule); no reason, no approver | **Alternative plus:** reason + second finance approver by default; ontology may relax per code (none / reason only / reason + approver). Store/HQ split not added until a real need appears. |
| Resolution | Auditor edits transaction directly; old version to revision table | **Alternative:** proposal + independent approver, except agent actions that meet the act criteria (deterministic, reversible, under ceiling), which need no approver because reopening reverses them. |
| Audit trail | Revision tables; total and over/short audit trails | **Alternative:** the same, plus evidence provenance and an audit event for every agent action and every evidence conflict. |
