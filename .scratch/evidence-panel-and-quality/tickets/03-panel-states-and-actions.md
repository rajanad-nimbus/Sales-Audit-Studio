---
title: Panel states and actions per role
label: wayfinder:grilling
status: closed
assignee: claude
blocked_by: [02-panel-prototype]
---
## Question
For auditor and finance approver: which actions the panel offers in each state (ready, conflict, stale, needs review, override pending), and what each action requires (reason, second approver, financial effect shown). Analyst and IT read-only scope.

## Resolution
All decisions here were taken on the user's blanket instruction to use the best recommendation, so they are assumed, not confirmed (see documents/AUDIT_EVIDENCE_DECISION_LOG.md).
Seven states (collecting, ready, conflict, stale, needs review, override pending, decided); decision actions blocked in the first four non-ready states; reject and conflict resolution need a reason; override needs reason, financial effect and a different second approver; analyst and IT read-only plus notes. Assumed decisions I2 to I5. Spec: documents/EVIDENCE_PANEL_AND_AGENT_QUALITY.md section 1.
