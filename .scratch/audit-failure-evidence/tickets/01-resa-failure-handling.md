---
title: How Oracle ReSA handles an audit failure end to end
label: wayfinder:research
status: closed
assignee:
blocked_by: []
---
## Question
From the ReSA 15.0.1 guide (`documents/resa-1501-ug.pdf`) and schema, how does ReSA take a failing audit from error raised to resolved: error list, required vs optional errors, store vs HQ errors pending, override rules, audit trail, and whether anything resembles "evidence" or investigation support? Output: a short factual note with page citations, separating guide facts from inference.

## Resolution
See [research note](../research/01-resa-failure-handling.md). ReSA: errors must be corrected or overridden, then the day auto-sets to Audited. No evidence concept beyond free-text transaction comments, no case, no approver, no override reason. Guide does not define required errors, store/HQ override rules, or how Store/HQ Errors Pending are set.
