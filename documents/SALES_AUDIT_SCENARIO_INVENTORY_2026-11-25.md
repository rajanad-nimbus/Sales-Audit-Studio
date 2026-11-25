# Sales audit scenario execution inventory

Executed 25 November 2026 using `pytest -q apps/ingest/tests/test_scenario_matrix.py apps/cash/tests/test_cash_scenario_matrix.py apps/totals/tests/test_client_totals.py`: 120 passed. Each row below is one parameterized scenario that passed in that run. The source scenario definitions contain the full inputs. The POS result is the expected set of five line-audit error codes, not a claim about unrelated case types or downstream resolution. Cash outcomes are final statuses and variances after all listed steps.

## POS ingest and reconciliation

| Scenario | Group | POS records | Expected and observed line-audit errors |
| --- | --- | ---: | --- |
| sale cash zero rate | sale | 1 | — |
| sale cash flat | sale | 1 | — |
| sale cash two rates | sale | 1 | — |
| sale cash exempt line | sale | 1 | — |
| sale card zero rate | sale | 1 | — |
| sale card flat | sale | 1 | — |
| sale card two rates | sale | 1 | — |
| sale card exempt line | sale | 1 | — |
| sale voucher zero rate | sale | 1 | — |
| sale voucher flat | sale | 1 | — |
| sale voucher two rates | sale | 1 | — |
| sale voucher exempt line | sale | 1 | — |
| sale giftcard zero rate | sale | 1 | — |
| sale giftcard flat | sale | 1 | — |
| sale giftcard two rates | sale | 1 | — |
| sale giftcard exempt line | sale | 1 | — |
| sale storecredit zero rate | sale | 1 | — |
| sale storecredit flat | sale | 1 | — |
| sale storecredit two rates | sale | 1 | — |
| sale storecredit exempt line | sale | 1 | — |
| sale other zero rate | sale | 1 | — |
| sale other flat | sale | 1 | — |
| sale other two rates | sale | 1 | — |
| sale other exempt line | sale | 1 | — |
| sale split cash+card zero rate | sale | 1 | — |
| sale split cash+card flat | sale | 1 | — |
| sale split cash+card two rates | sale | 1 | — |
| sale split cash+card exempt line | sale | 1 | — |
| sale split cash+giftcard zero rate | sale | 1 | — |
| sale split cash+giftcard flat | sale | 1 | — |
| sale split cash+giftcard two rates | sale | 1 | — |
| sale split cash+giftcard exempt line | sale | 1 | — |
| discount promotion | discount | 1 | — |
| discount coupon | discount | 1 | — |
| discount voucher | discount | 1 | — |
| discount manual with reason | discount | 1 | — |
| discount line level | discount | 1 | — |
| discount stacked | discount | 1 | — |
| discount manual without reason | discount | 1 | DISC_REASON_REQ |
| return full | return | 2 | — |
| return one line | return | 2 | — |
| return partial quantity | return | 2 | — |
| return to another tender | return | 2 | — |
| return card refund | return | 2 | — |
| return in two steps | return | 3 | — |
| return more than sold | return | 2 | RETURN_OVER_SOLD |
| return item not on the sale | return | 2 | RETURN_OVER_SOLD |
| return twice the same item | return | 3 | RETURN_OVER_SOLD |
| edge penny sale | edge | 1 | — |
| edge very large sale | edge | 1 | — |
| edge decimal quantity | edge | 1 | — |
| edge rounding many small lines | edge | 1 | — |
| edge twelve line basket | edge | 1 | — |
| edge gift card sale | edge | 1 | — |
| edge non-usd currency | edge | 1 | — |
| exchange return of a discounted sale | exchange | 2 | — |
| exchange return without a receipt | exchange | 1 | — |
| exchange return the next day | exchange | 2 | — |
| exchange even | exchange | 3 | — |
| exchange customer pays more | exchange | 3 | — |
| exchange customer refunded the difference | exchange | 3 | — |
| exchange non-usd return | exchange | 2 | — |
| exchange gift card return | exchange | 2 | — |
| error tender short | error | 1 | TRAN_OUT_BAL |
| error tender over | error | 1 | TRAN_OUT_BAL |
| error header above lines | error | 1 | TRAN_OUT_BAL |
| error header below lines | error | 1 | TRAN_OUT_BAL |
| error item without a sku | error | 1 | SKU_NOT_FOUND |
| error item not in the item master | error | 1 | SKU_NOT_FOUND |
| error item in the item master | error | 1 | — |
| error tax too high | error | 1 | TAX_MISMATCH |
| error tax too low | error | 1 | TAX_MISMATCH |
| error out of balance hides the line rules | error | 1 | TRAN_OUT_BAL |

## Cash office

| Scenario | Group | Operations | Expected and observed final status / variance |
| --- | --- | --- | --- |
| register balanced | register | declare | Balanced 0.00 |
| register short | register | declare | Over/Short 10.00 |
| register over | register | declare | Over/Short -10.00 |
| register paid-out explains the gap | register | declare | Balanced 0.00 |
| register safe drop | register | declare | Balanced 0.00 |
| register paid-out and safe drop | register | declare | Balanced 0.00 |
| register paid-out larger than the gap | register | declare | Over/Short -15.00 |
| register with no sales balanced at zero | register | declare | Balanced 0.00 |
| register with no sales but cash declared | register | declare | Over/Short -5.00 |
| register recounted twice | register | declare,declare | Over/Short 10.00;Balanced 0.00 |
| register cashier balanced | register | declare | Balanced 0.00 |
| register cashier with no sales | register | declare | Balanced 0.00 |
| register cashier short | register | declare | Over/Short 10.00 |
| store-wide declaration balanced | register | declare | Balanced 0.00 |
| store-wide declaration short | register | declare | Over/Short 10.00 |
| a day with no sales | register | declare | Balanced 0.00 |
| card tender declaration | register | declare | Balanced 0.00 |
| voucher tender declaration short | register | declare | Over/Short 10.00 |
| deposit matches | deposit | declare,deposit | Balanced 0.00;Balanced 0.00 |
| deposit short | deposit | declare,deposit | Balanced 0.00;Over/Short 15.00 |
| deposit over | deposit | declare,deposit | Balanced 0.00;Over/Short -10.00 |
| deposit recorded before the count | deposit | deposit,declare | Balanced 0.00;Balanced 0.00 |
| deposit of two registers | deposit | declare,declare,deposit | Balanced 0.00;Balanced 0.00;Balanced 0.00 |
| deposit of two registers short | deposit | declare,declare,deposit | Balanced 0.00;Balanced 0.00;Over/Short 30.00 |
| store-wide declaration replaces register counts | deposit | declare,declare,deposit | Balanced 0.00;Balanced 0.00;Balanced 0.00 |
| deposit of a safe drop only | deposit | declare,deposit | Balanced 0.00;Balanced 0.00 |
| paid-out is not deposited | deposit | declare,deposit | Balanced 0.00;Balanced 0.00 |
| paid-out deposited by mistake | deposit | declare,deposit | Balanced 0.00;Over/Short -10.00 |
| deposit with nothing counted | deposit | deposit | Over/Short -130.00 |

## Narrative scenarios

The five narratives in `NIMBUS_SCENARIOS.md` and two in `STORE_DAY_BUSINESS_SCENARIOS.md` describe broader workflows. The matrix results above do not establish those complete narratives; map and run their handling, evidence, decision, closure and export steps separately. The failure-evidence document is a decision record, including future behavior, and must not be treated as proof of implementation.

### Narrative evidence reviewed

| Narrative | Executed evidence | Remaining verification |
| --- | --- | --- |
| Clean store day and automatic closure | `test_clean_store_days_open_total_and_close_without_a_person` passed, checking sign-off, close, audit events and idempotent rerun. | Its Shopify fixture is not the exact POS, GL and Shopify dataset described in the narrative. |
| Duplicate with different payment references | `test_a_duplicate_with_a_different_payment_reference_is_still_resolved_by_the_agent` and tests for exact and differing duplicates passed. | The specific $40 supporting controls and subsequent GL and other variance outcomes in the narrative were not replayed. |
| Store day not ready | `test_a_store_day_that_disagrees_with_the_source_is_left_for_a_person` passed; a mismatched Shopify count remains Auditing until the case is manually marked closed. | This does not replay the missing end-of-day marker and IT recovery in Nimbus scenario 5. |
| Settlement timing, card over/short, missing refund | Currency-policy, tolerance, readiness and some evidence checks passed. | These do not establish the complete Nimbus narratives, evidence, approval and downstream outcomes. |

The decision record explicitly describes future behavior and unimplemented elements. Passing current tests does not establish those decisions are implemented.
