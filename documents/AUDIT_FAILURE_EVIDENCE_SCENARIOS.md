# Audit failures: cases, evidence and agents

This document records the decisions for how ZeTSA handles a failing audit: what case is registered, what evidence Agents gather, what Agents may do with it, and where ZeTSA follows or departs from Oracle ReSA. It is a **decision record, not a description of current behaviour**. Where today's code differs, the section says so. It builds on [STORE_DAY_BUSINESS_SCENARIOS.md](STORE_DAY_BUSINESS_SCENARIOS.md), [NIMBUS_SCENARIOS.md](NIMBUS_SCENARIOS.md), [NIMBUS_AGENTS.md](NIMBUS_AGENTS.md) and [RESA_DIFFERENCES.md](RESA_DIFFERENCES.md).

## 1. Summary of decisions

1. **Evidence** is a deterministic link to source records, plus an optional agent **finding** that says what the source showed.
2. **Agents** work at one of three levels: collect, propose, or act. Only deterministic, reversible, bounded, evidence-ready cases are acted on.
3. **Cases** are one per group of exceptions. Reruns update the open case.
4. **Close gate:** only blocking cases hold a store day open. Others carry forward with an age limit.
5. **ReSA:** ZeTSA takes the alternative in all four areas compared (error handling, overrides, resolution, audit trail).

## 2. What ReSA does (15.0.1 guide)

Source: [research note](../.scratch/audit-failure-evidence/research/01-resa-failure-handling.md), PDF page indexes.

- Errors must be corrected or overridden (p.49). The auditor fixes via Error List and Transaction Detail (p.66), or acknowledges and overrides (p.71, p.38). When all are cleared the day becomes Audited (p.50).
- Per-error override and system impact are set when the error is defined (p.38, p.43-44).
- The only support for investigation is a free-text Comments window on a transaction (p.55). There are no cases, attachments, approvals, or override reason.
- The guide does not define required errors, a store versus HQ override rule, or how Store/HQ Errors Pending are set. Those appear only in the schema or as search filters.

## 3. Evidence model

| Topic | Decision |
| --- | --- |
| Shape | Every item keeps the baseline: linked source records exist for an ontology-required item. An agent may add a **finding**: what the source showed, a verdict (supported, contradicted, inconclusive) and cited record ids. |
| Staleness | Change-based. A re-import, re-total or duplicate exclusion makes earlier evidence stale. A time threshold applies only to sources with no change signal. |
| Who may change it | Agents add and regather. They never reject or override. A regather adds a new item and flags any conflict with human evidence. Overriding needs a reason and a second finance approver. |
| Provenance | Producer (deterministic, or agent and model version), tool or query trace, data version (ontology version and source batch), and cited ids checked against what was retrieved. |
| Ready for decision | All required items found **and** no contradicting or inconclusive finding. The minimum set is per failure class, held in the ontology. |
| Conflicts | A conflict between sources, or between a human item and an agent finding, blocks "ready" until a person resolves it. An inconclusive finding only warns. |

Today: evidence is link-existence only, and "Ready" needs every required item found. Findings, change-based staleness, provenance beyond source system and ids, and the conflict block are not built.

## 4. Agent levels

- **Collect:** gather evidence and stop.
- **Propose:** also recommend a resolution. A person decides.
- **Act:** resolve the case with an audit event.

**Act requires all of:** deterministic proof (an LLM finding alone never qualifies), zero or reversible financial effect, an amount under a per-class ceiling, and ready evidence. **Propose requires ready evidence.** Everything else is collect.

**Guardrails on act:** the audit event, the existing on/off option, a per-class kill switch, and a daily cap. **Reversal:** reopening the case restores the transaction, re-totals the day, and returns a closed day to Auditing. Spot-check sampling is deferred.

## 5. Failure classes

The level is configured in the ontology, so it can change without code.

| Failure | Code | Level | Notes |
| --- | --- | --- | --- |
| Exact duplicate sale | `DUPLICATE_SALES` | **Act** | Single original, checked fields match (payment reference ignored). Today's behaviour. Differing fields or no single original fall to a person. |
| Over/short within tolerance | tolerance rule | **Act** | Complete evidence, governed automation, no human. |
| Timing difference | `TIMING_DIFFERENCE` | Propose | Zero exposure. See open point 1. |
| Amount mismatch | `AMOUNT_MISMATCH` | Propose | |
| Sale without payment | `UNMATCHED_SALE` | Propose | |
| Payment without sale | `ORPHAN_PAYMENT` | Propose | |
| Bank variance, missing deposit | `BANK_VARIANCE`, `MISSING_DEPOSIT` | Propose | |
| GL variance, missing GL posting | `GL_VARIANCE`, `MISSING_GL_POSTING` | Propose | |
| Discount missing reason | `DISC_REASON_REQ` | Propose | |
| Return without refund | `MISSING_REFUND` | Collect | A wrong proposal could approve a refund that never reached the customer. Scenario 4 needs 100% evidence and a person. |
| Missing transactions, incomplete feed | `MISSING_TRAN_BIG_GAP`, `INCOMPLETE_FEED` | Collect | The agent cannot tell which side is wrong. |
| Balancing variance | `BALANCING_VARIANCE` | Collect | |
| Transaction out of balance | `TRAN_OUT_BAL` | Collect | |

## 6. Case lifecycle

- **Grouping:** one case per group (failure type, store, business date, currency, detection rule). A reconciliation rerun updates the open case. A new case is created only after the previous one closed. *The code is not confirmed to enforce the rerun rule.*
- **Regather:** on source change, on demand, and when a decision is due. If a regather changes a finding after a recommendation, the recommendation is marked "needs review".
- **Escalation:** priority uses **exposure**, not raw amount, so a zero-exposure timing difference does not outrank a real loss. Conflicting evidence, a contradicting finding, or a collect-level class escalates regardless of amount.
- **Closing the day:** only cases whose error definition blocks close gate the store day. Non-blocking cases carry forward and become blocking after an age limit. This is **looser than ReSA**, which clears all errors before Audited. The age limit stops carry-forward from hiding cases.

Today: case grouping, priority by raw amount with an SLA, and a close gate on unresolved variances and open duplicate cases exist. Exposure-based priority, agent-driven escalation and carry-forward are not built.

## 7. ReSA compared with the chosen approach

| Area | ReSA | Chosen |
| --- | --- | --- |
| Error handling | Error list. Fix or override. All cleared sets Audited. | Cases with evidence, recommendation and decision. Errors an agent resolves at the act level close immediately and never reach a person's queue. |
| Overrides | Per-code override flags (schema only). No reason, no approver. | Reason and second finance approver by default. The ontology may relax this per code to none, reason only, or reason and approver. No store/HQ split until a real need appears. |
| Resolution | Auditor edits the transaction. Old version goes to a revision table. | Proposal and independent approver. Agent actions that meet the act criteria need no approver, because reopening reverses them. |
| Audit trail | Revision tables. Total and over/short audit trails. | The same, plus evidence provenance and an audit event for every agent action and every evidence conflict. |

## 8. Open points

1. **Scenario inconsistency.** The $550 timing difference in scenario 1 is above the $250 autonomous threshold yet is shown as disposition only. Decide which is right.
2. **Per-class evidence requirements** live in the ontology and were not enumerated. Check whether any code falls back to the default list.
3. **Unexplained variances and "store day not ready"** have no case and so no evidence trail. Whether they should is undecided.
4. **Missing or late sources:** undecided whether they register cases or remain store-day blockers.
5. **Evidence panel in the UI** and **agent evidence quality measures** are not specified.
6. **Spot-check sampling** of act resolutions is deferred until there is volume.
7. ReSA release 15.0.1 is the only guide consulted. Later releases are unchecked.
