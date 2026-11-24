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
| Timing difference | `TIMING_DIFFERENCE` | Propose | Zero exposure and clears when settlement arrives, but it has no Oracle ReSA equivalent and closing it early would hide a settlement that never arrives. A person always decides. |
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

**Types not listed above.** An unmapped or newly added type defaults to **collect** until a level is assigned, so a new code cannot propose or act by accident. Assignments for the rest of the ontology:

| Types | Level | Reason |
| --- | --- | --- |
| `SHOPIFY_DUPLICATE_ORDER` | Act when the exact-duplicate criteria hold, otherwise collect | Mirrors the POS exact duplicate. |
| `SHOPIFY_POSSIBLE_DUPLICATE_ORDER` | Collect | "Possible" is not proof. |
| `SHOPIFY_UNMATCHED_REFUND`, `SHOPIFY_REFUND_MANUAL_REVIEW`, `SHOPIFY_PARTIAL_REFUND`, `SHOPIFY_OVERAGE_REFUND` | Collect | Mirror missing refund. |
| `SHOPIFY_EOD_TOTAL_MISMATCH`, `SHOPIFY_INCOMPLETE_FEED` | Collect | Mirror balancing variance and incomplete feed. |
| `SKU_NOT_FOUND`, `TAX_MISMATCH`, `RETURN_OVER_SOLD`, `VOID_NO_ORIG`, `PAIDOUT_REASON_REQ`, `TENDER_NOT_FOUND`, `TENDER_REF_REQ`, `TENDER_OVER_LIMIT`, `MISSING_TRAN`, `NOSALE_EXCESS`, `CONFIGURED_RULE_VARIANCE`, `CONNECTOR_TIMEOUT` | Collect | Data-entry or source problems that need a person. |

## 6. Case lifecycle

- **Grouping:** one case per group (failure type, store, business date, currency, detection rule). A reconciliation rerun updates the open case. A new case is created only after the previous one closed. *Today the rule is enforced only in part:* rule-raised and missing-sequence-number cases check for an open case first, and transaction-level findings do not repeat because a flagged transaction leaves the `Unmatched` set. Bank, GL and manifest findings are raised from group totals and do not check for an open case, so the group-level rule needs building.
- **Regather:** on source change, on demand, and when a decision is due. If a regather changes a finding after a recommendation, the recommendation is marked "needs review".
- **Escalation:** priority uses **exposure**, not raw amount, so a zero-exposure timing difference does not outrank a real loss. Conflicting evidence, a contradicting finding, or a collect-level class escalates regardless of amount.
- **Closing the day:** only cases whose error definition blocks close gate the store day. Non-blocking cases carry forward and become blocking after an age limit. This is **looser than ReSA**, which clears all errors before Audited. The age limit stops carry-forward from hiding cases.

Today: case grouping, priority by raw amount with an SLA, and a close gate on unresolved variances and open duplicate cases exist. Exposure-based priority, agent-driven escalation and carry-forward are not built.

### Variances and late sources

- **Unexplained variance:** a store-day variance above tolerance that no case explains registers a case, so the evidence and lifecycle rules apply and the close gate has an owner and a trail. Today it has none and only blocks close. No second threshold is added, because tolerance already filters small amounts.
- **Missing or late source** (end-of-day marker, feed, connector): it waits quietly until the source is overdue, then registers a collect-level case carrying delivery evidence (manifest, connector log). A source that is merely late does not create work. `CONNECTOR_TIMEOUT` and `INCOMPLETE_FEED` already exist with that evidence. The overdue deadline is not yet set.

## 7. ReSA compared with the chosen approach

| Area | ReSA | Chosen |
| --- | --- | --- |
| Error handling | Error list. Fix or override. All cleared sets Audited. | Cases with evidence, recommendation and decision. Errors an agent resolves at the act level close immediately and never reach a person's queue. |
| Overrides | Per-code override flags (schema only). No reason, no approver. | Reason and second finance approver by default. The ontology may relax this per code to none, reason only, or reason and approver. Each error code also says **who may override**: store, HQ, or both, defaulting to HQ only (ReSA's Store Override and HQ Override flags; see section 9). |
| Resolution | Auditor edits the transaction. Old version goes to a revision table. | Proposal and independent approver. Agent actions that meet the act criteria need no approver, because reopening reverses them. |
| Audit trail | Revision tables. Total and over/short audit trails. | The same, plus evidence provenance and an audit event for every agent action and every evidence conflict. |

## 8. Open points

Decisions for the items below were taken on a blanket instruction to use the best recommendation, so they are **provisional or assumed**, not confirmed. Each is logged in [AUDIT_EVIDENCE_DECISION_LOG.md](AUDIT_EVIDENCE_DECISION_LOG.md), section J.

| Open point | Value taken | Status |
| --- | --- | --- |
| Act ceiling, over/short | $5 (scenario 3 tolerance) | Provisional |
| Act ceiling, exact duplicate (POS and Shopify) | $500 per case. Today there is no ceiling, so this adds a limit. | Provisional |
| Daily cap on act actions | 200 per class per day | Provisional |
| Overdue deadline for a missing or late source | 12 hours after the business date ends | Provisional |
| Carry-forward age limit | 3 business days | Provisional |
| Spot-check sampling of act resolutions | 5% per class per week, at least 5 | Provisional |
| Class assignments for the 20 added types | As drafted in section 5 | Assumed |
| Evidence requirements for rule-raised codes | The error definition table gains an optional evidence list and an exposure setting. Until set, the code uses the default list, stays at collect, and is flagged in the error-code admin as "evidence not defined". | Assumed |
| Evidence panel and agent quality measures | Specified in [EVIDENCE_PANEL_AND_AGENT_QUALITY.md](EVIDENCE_PANEL_AND_AGENT_QUALITY.md) | Assumed |
| Scenario 1 | No rewording needed. "Disposition only" is the action class and Finance approval already applies above $250. | Resolved |
| ReSA release | Checked by web search (search summaries, not the full guides): see section 9. The 15.0.1 guide remains the only one read in full. | Checked, low confidence |

## 9. Later ReSA releases

A web search of Oracle documentation (summaries only; the full guides were not read) found:

- **Store and HQ override flags are real.** The 13.x and 14.0 user guides describe a Store Override and an HQ Override checkbox on each error definition. The 24.0.101.0 implementation guide still has an HQ Override column. Section 7 first left the split out because the 15.0.1 guide did not define it. It has since been added (decision log K3).
- **"System Required" decides blocking.** The 19.3 and 24.0 implementation guides describe it per error and per target system. Custom codes default to not required. This matches ZeTSA's export-block destinations on each error definition.
- **Status codes.** An operations guide lists audit status H (HQ errors pending) and S (store errors pending). Another Oracle guide lists status values without S, so versions differ.
- **Acknowledge, then override.** The 23.1 guide says errors that cannot be corrected must be acknowledged and then overridden. The behaviour matches 15.0.1.
- **No evidence concept found,** and no second approver or override reason. The conclusion in section 2 holds for the later releases searched.

Sources: [13.2.5 user guide](https://docs.oracle.com/cd/E12448_01/rms/pdf/1325/resa-1325-ug.pdf), [16.0.21 user guide](https://docs.oracle.com/cd/E79623_01/rms/pdf/16021/resa-16021-ug.pdf), [19.3 implementation guide](https://docs.oracle.com/cd/E79623_01/rms/pdf/193000/resa-impg.pdf), [24.0.101.0 implementation guide](https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/24.0.101.0/rsaim/F91829_01.pdf), [23.1.301.0 Do the Basics guide](https://docs.oracle.com/en/industries/retail/retail-merchandising-foundation-cloud/23.1.301.0/rsadb/F83911_02.pdf).
