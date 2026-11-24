# Failure scenario inventory

Sources: `documents/NIMBUS_SCENARIOS.md`, `documents/STORE_DAY_BUSINESS_SCENARIOS.md`, `backend/apps/ingest/{reconciliation,controls}.py`, `store_days/{duplicates,automation}.py`, `integrations/ontology/nimbus.py`, `agents/{investigator,agent_tools}.py`, `cases/models.py`.

## How evidence works today (code)
- A case is registered by reconciliation/controls (`flag`/`add` codes below). Each exception has an ontology entry listing **required evidence items**.
- Deterministic investigation (`nimbus.investigate`) writes one `EvidenceSnapshot` per required item: `Complete` if linked source records were found, else `Unavailable`. Completeness % = complete / required. All complete -> case "Ready for Decision / In Review".
- Optional LLM investigator (read-only tools: list_transactions, get_source_record, get_store_day_totals, lookup_ontology; max 6 turns) submits hypotheses with verdict supported/contradicted/inconclusive, cited ids, contradictions, missing evidence. Output is validated against ids actually seen.
- Humans can request, provide, reject, or override evidence (override needs reason + second approver).
- Snapshot staleness threshold: 4 h (field exists).

## Matrix
| Failure | Raised by | Case/exception code | Evidence gathered today | Agent action today | Outcome today |
|---|---|---|---|---|---|
| Exact duplicate sale | reconciliation | DUPLICATE_SALES | Original vs copy field comparison (ignores payment ref) | store-day-agent resolves if single original matches | Copy excluded, case closed, day re-totaled |
| Duplicate, fields differ / no single original | reconciliation | DUPLICATE_SALES | Ontology evidence snapshots | Investigation only | Left for human |
| Settlement timing difference | reconciliation | TIMING_DIFFERENCE | Ontology evidence; scenario 1 | Investigation; scenario 1 sends $550 to Finance approval because it exceeds the $250 threshold | Human decision |
| Sale without payment | reconciliation | UNMATCHED_SALE | Ontology evidence | Investigation | Human |
| Return without refund | reconciliation | MISSING_REFUND | Scenario 4: evidence 60% vs 100% required | Investigation + evidence request | Human; escalation |
| Payment without sale | reconciliation | ORPHAN_PAYMENT | Ontology evidence | Investigation | Human |
| Amount mismatch sale vs payment | reconciliation | AMOUNT_MISMATCH | Ontology evidence | Investigation | Human, tolerance rules |
| Transaction out of balance | reconciliation | TRAN_OUT_BAL | Ontology evidence | Investigation | Human |
| Discount missing reason | reconciliation | DISC_REASON_REQ | Ontology evidence | Investigation | Human |
| Bank variance / missing deposit | controls | BANK_VARIANCE, MISSING_DEPOSIT | Ontology evidence | Investigation | Human |
| GL variance / missing GL posting | controls | GL_VARIANCE, MISSING_GL_POSTING | Ontology evidence | Investigation | Human; blocks close |
| Missing transactions (big gap) / incomplete feed | controls (manifest) | MISSING_TRAN_BIG_GAP, INCOMPLETE_FEED | Manifest count/total vs loaded | Investigation | Human |
| Balancing variance vs declared total | controls | BALANCING_VARIANCE | Manifest total vs loaded | Investigation | Human |
| Card tender over/short within tolerance | totals | (over/short, no case) | Scenario 3: complete evidence | Governed automation, no human | Auto-resolved |
| Store day not ready (marker file missing) | readiness | not a case | Missing prerequisite listed | store-day-agent waits | Load blocked |
| Unexplained variance after duplicate removal | totals | open variance | Totals only | none | Human |

## Gaps (failure with a case but no evidence story)
1. Evidence is only ever "linked source records exist"; nothing records **what the records showed** or whether they explain the variance.
2. Evidence requirements per code live in the ontology; this inventory did not enumerate each code's list. Worth checking whether any code falls back to `DEFAULT_EVIDENCE`.
3. ~~Scenario doc inconsistency~~ (corrected later): scenario 1 already routes the $550 to Finance approval above the $250 threshold; "Disposition Only" is its action class, not an automation claim.
4. Unexplained variances and "store day not ready" have no case, so no evidence trail.
5. Staleness: field exists, but regather trigger on stale evidence was not verified in code.
6. LLM investigator is optional and bounded; deterministic path is the default, so most cases carry link-existence evidence only.
