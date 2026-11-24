---
title: Which quality signals the data already records
label: wayfinder:research
status: closed
assignee:
blocked_by: []
---
## Question
For each measure (wrong act, wrong finding, missed escalation, cost and delay), which signals already exist in the backend data model and audit events (reopen events, evidence rejections, overrides, agent audit events, run cost and duration), and which are missing? Output a short factual note with file and table references.

## Resolution
See [note](../research/01-signals-available.md). Exists: store-day reopen events and duplicate-exclusion events; evidence rejections and overrides; human decisions against recommendations; priority, SLA and snooze; AGENT_RUN events with tokens (cost and turns on the SDK path only). Missing: case reopen and un-exclude events and any link from a reopen to the agent act it undid; findings linked to the evidence they cite; agent-versus-rule origin on recommendations; any SLA breach or escalation timestamp; run duration; any record for Store Day Agent runs; a dedicated run table (everything sits in audit_events JSON).
