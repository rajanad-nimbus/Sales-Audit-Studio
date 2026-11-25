# Sales audit test execution — 25 November 2026

## Scope and results

The source setup is `System_ReSA Total setup.xlsx`. Its `All Client Totals` sheet lists sales, tender, tax, paid-in/paid-out and over/short totals, with calculated, declared and combined variants. The application currently defines 200 client totals (133 raw and 67 combined). Existing workbook tests validate definitions, dependencies and selected calculations, but do not assert an independent expected amount for every total and business scenario.

Executed from `backend` using `.venv/bin/python`:

| Run | Result | Scope |
| --- | --- | --- |
| `pytest -q apps/ingest/tests/test_scenario_matrix.py apps/cash/tests/test_cash_scenario_matrix.py apps/totals/tests/test_client_totals.py` | 120 passed | 73 POS scenarios, 29 cash scenarios, and workbook definition and selected calculation checks. |
| `pytest -q apps/totals/tests apps/store_days/tests apps/ingest/tests apps/cash/tests apps/corrections/tests apps/exports/tests` | 789 passed, 23 failed, 12 skipped | In-memory and other available backend tests for the listed areas. |
| `pytest -q --continue-on-collection-errors` | Interrupted after 46 passed and four collection errors | Full backend run did not complete. Four rule tests import local rule-management symbols removed from the current read-only Ontology Studio integration. |

The POS matrix asserts the set of generated audit error types for sale, discount, return, exchange and edge cases. The cash matrix asserts declaration/deposit statuses and variances. These are useful checks, but do not establish full case resolution, UI behavior, export or production configuration for each scenario. The shared test fixture supplies a mocked ontology release with no rules unless explicitly installed. SQLite tests do not establish PostgreSQL concurrency behavior; PostgreSQL tests require a disposable database configured with `ZETSA_TEST_POSTGRES_URL`. Skips were not individually classified in this run.

## Failed checks requiring investigation

- Total change impact: `test_impact_reports_audit_rules_that_would_newly_breach_or_clear` expected one cleared rule and received none. The test inserts a local `ConfiguredAuditRule`, whereas the current rules integration reads the ontology release; determine whether the test or impact behavior needs updating.
- Total request contract: `test_total_definition_rejects_oversized_nested_metadata` received a realm type validation error instead of the expected size error.
- Store-day configured rules: two `test_configured_tax_totals` cases expecting rejection of a register-level rule target did not raise; both install local rule records. Verify the corresponding active ontology rule behavior.
- Register events: two post-void checks expected `VOID_NO_ORIG` but did not see it. Two POS-log endpoint tests also failed. Inspect active rule configuration and endpoint errors before assigning cause.
- Shopify: 11 feed, pagination, refund and daily-total tests failed. Their individual causes were not classified in this execution.
- Corrections: four checks failed across missing-transaction approval, concurrent review and store-scope facets. The observed facet failure was an unexpected foundation-reference query; it does not by itself prove a scope leak.

The four full-suite collection errors are in `apps/controls/rules/tests/test_approval_revalidation.py`, `test_integrity_errors.py`, `test_packages.py` and `test_rule_precision.py`. The current `apps/controls/rules/routes.py` exposes read-only rule views and delegates rule changes to Ontology Studio. The tests need reconciliation with that architecture, not blind restoration of the old API.

## Remaining work

1. Identify the authoritative previously prepared functional and business scenario inventory, map every row to a runnable test and expected handling outcome, and record untested cases. The workbook itself is a totals setup, not an explicit scenario inventory.
2. Add independent expected-value tests for each applicable workbook total and its restrictions, signs, declarations, combinations, store/area applicability, and over/short behavior. Confirm ambiguous workbook mappings with business owners rather than treating implementation values as the oracle.
3. Investigate and rerun each failure above, with a real representative approved ontology release where applicable; distinguish obsolete tests, environment limitations and defects.
4. Run the complete backend suite after collection issues are resolved. Run lifecycle, concurrency and migration tests on disposable PostgreSQL, and exercise relevant UI and integration flows with appropriate non-production services and data.
5. Record per-scenario input, expected result, observed result, evidence, and handling through correction, sign-off, closure and export where applicable. Do not claim complete business validation until all applicable cases have evidence or an explicit disposition.

## Follow-up execution

The user identified the scenario files in this project as the inventory. The individual 73 POS and 29 cash outcomes are listed in `SALES_AUDIT_SCENARIO_INVENTORY_2026-11-25.md`.

A rerun of the same six-area suite with local mock-server access produced **800 passed, 12 failed, 12 skipped** in 56.78 seconds. The 11 earlier Shopify failures were all caused by sandbox denial of local socket binding; an additional targeted Shopify run passed all 39 tests. A targeted run of store-day agent, duplicate resolution, and currency-policy tests passed 26 tests. A separate agent-investigator run was interrupted after four passes while waiting; it does not establish the remaining agent cases.

The two missing-original post-void failures reproduce in isolation. `run_register_event_rules` explicitly checks for an original only if a number is supplied; the feed profile says a post-void without that number makes the rule unavailable. The existing tests instead expect a `VOID_NO_ORIG` case. This is a policy/test conflict: do not assume either the absence of a case or the test expectation is approved behavior without resolving the source contract.

The two RTLOG endpoint tests receive 422: one fixture lacks `FTAIL`, and one real sample has a merchandise transaction without usable item lines. The missing-transaction approval test receives 422 because its fixture omits the now-required `source_system`. The concurrent review tests produce zero successful decisions; their causes remain unverified. The other failures remain as described above. Each failure needs its own disposition and rerun; a fixture mismatch is not proof that the corresponding production workflow works.

## Total-change impact comparison defect

A focused check with an approved-release-shaped rule `LT 10` and a total changing from 15 to 2 showed the audit evaluator regards the rule as newly breached, while the change-impact preview reported it as cleared. The preview used `abs(value) > threshold` for every operator. It now calls the same comparison evaluator used by the actual store-day audit. Seven targeted checks exercise `ABS_GT`, `GT`, `GTE`, `LT`, `LTE`, `EQ` and `NE`, including signs and equality boundaries. Before the change six of seven failed; after the change, the focused impact and tolerance run passed 23 tests (one older local-rule test deselected). This fixes comparison semantics only; the earlier local-rule test still needs migration to an ontology release and overall rule applicability needs further verification.

A follow-up run of evidence checks (excluding the slow `provided_evidence` test) and store-day readiness/variance tests passed 19 tests, with one deselected. A separate combined evidence run was interrupted after five passes while waiting, so it cannot be counted as complete.

## Ontology rule applicability follow-up

The impact preview was additionally tested with a simulated approved release: a store-day total changes from 15 to 2, with matching and nonmatching location traits and rules before, during and after their effective dates. Before correction, the preview incorrectly included the future, expired and nonmatching rules. It now filters Store-level rules by date and store trait before calculating their change. The test includes matching traits and inclusive date boundaries. The preview only compares Store-level values; Register and Cashier rule impacts remain unreported and require separate support rather than treating their omission as a pass.

The two earlier wrong-level rule tests and the original impact test were updated to supply `TotalRule` objects through the approved-ontology test fixture instead of inserting unused local `ConfiguredAuditRule` records. They now exercise the current rule source. The combined run of configured-tax totals, total impact and comparison tests passed **28 tests**. These fixtures represent a release, not the actual deployed release.

## Workbook tender calculation follow-up

`test_workbook_tender_scenarios.py` ingests sixteen sales with the tender group and type IDs from the workbook's ReSA Calculated sheet, row 30. It independently checks the dollar amount of each corresponding total, then verifies a cash return reduces CASH and a split cash/Visa sale contributes only its own tender amounts. The combined cash-and-check total is checked as $13 (cash $7 plus check $6). This tests one positive example per listed category, not every restriction, error status, declaration, or downstream case. The group-only debit and check examples use representative IDs because the workbook restricts those by group.

The tender scenario, client totals, over/short and readiness/variance test files passed **30 tests**. These tests use SQLite and simulated ontology controls, and do not prove the deployed configuration or full workbook coverage.

## Declared tender and over/short follow-up

A new parameterized test executes 48 cases: balanced, $2 short and $2 over for each of AGNCYVOU, AMAZON, AMEX, BEALLSCC, CASH, CHECK, DISCOVER, EBAY, GIFTCRDU, MASTERCD, MCVISADBT, OUTLTCC, PAYPAL, VIRTGC, VISA and WALMART. Each ingests a $10 sale and a POS total event, then asserts the calculated total, its declared P total and its O total (calculated minus declared). **All 48 passed**. The test moves definition start dates to the sample business date for this isolated scenario and uses explicit client tender codes; it does not establish original effective-date handling, POS file parsing or that the individual O totals gate closure.

The existing combined-declaration and readiness/variance checks also passed in a separate 15-test run, including a POS-to-GL variance blocking closure until explained and signed off. Those generic closure checks are not proof that every workbook O total generates a closure blocker.

## Cash difference case and close gate

A new re-total test supplies a simulated approved `ABS_GT 0` USD ontology rule for `Configured: CASHO`. For a $10 cash sale, a $10 declaration creates no rule case; $8 and $12 declarations each produce the expected signed difference and an open configured-rule case with close impact Yes. The actual `close_blocker` rejects each case while it remains open. A combined run of these tests, the 48 tender/declaration cases and the tender input test passed **52 tests**. This verifies the case gate for the simulated rule, not that a deployed release contains such a rule or that all workbook totals are configured similarly. It does not exercise resolution, sign-off and actual closure after correction.

Code inspection shows that the built-in comparison close gate considers POS-to-GL and POS control variances, not the standalone `Over/Short` or `Configured: CASHO` values. Therefore a close-blocking case or another configured control is needed for these amounts to hold closure; the workbook alone does not establish that such a rule is active. Also, `close_blocker` currently counts all open cases, while readiness counts cases based on close impact. The resulting treatment of warning-only cases warrants a separate end-to-end check.

## Correction and warning-only case follow-up

The cash-rule test now adds a supplemental POS declaration to offset the original $2 short or over, then re-totals. Both scenarios calculate `CASHO = 0`, but the existing case remains open and the close gate continues to reject closure. Re-total does not automatically resolve that prior case. The test does not claim the supplemental event is the approved production correction method, and does not exercise a governed case decision or final close. The three cash-rule variants passed.

A separate reproducible test creates a case containing only an exception with `close_impact = No`. The readiness case count excludes it, but `close_blocker` returns `Cannot close: 1 case(s) remain open`. This is a confirmed inconsistency between readiness and the close gate; its expected business policy and any correction need explicit disposition. The test records the existing behavior and is not an assertion that the mismatch is desirable. The warning test and three cash-rule variants passed together (4 tests).

## Workbook tax routing follow-up

A new ingestion and re-total test uses the workbook's register restrictions: store 001 contributes $1 to `SLSTAXACC`, Amazon registers 892 and 897 contribute $7 less a $0.50 return to `SLSTXAMZN`, and eBay registers 895 and 896 contribute $7 to `SLSTXEBAY`. The persisted calculated totals matched those expected amounts. Together with client totals, configured-tax and Shopify-tax checks, **27 tests passed**.

The initial test supplied nonzero line tax amounts without a tax code; ingest accepted the records but the three tax totals were zero. Inspection shows derived tax summaries skip lines without a tax code. Adding a code made the scenario pass. Whether such input is permitted by the source contract, and whether reconciliation or an approved rule reliably alerts a user before export, still needs a dedicated test and disposition. This is not evidence that uncoded tax is handled safely.

## Uncoded line tax handling

A focused test ingests a balanced $11 cash sale with a $10 line, 10% rate, and $1 line tax but no tax code or explicit tax-summary record. The record is accepted, the $1 remains on the line, no transaction tax-summary row is created, `SLSTAXACC` re-totals to $0, and reconciliation raises no `TAX_MISMATCH`. This test checks that specific finding only: other reconciliation cases, production ontology rules, source controls, and export gates were not ruled out. Determine the feed contract and intended handling before choosing whether to reject, quarantine, flag, or otherwise account for an uncoded amount. The characterization test records existing behavior rather than approving it.

The workbook tax scenarios, audit-error, invalid-detail and configured-tax tests passed **38 tests** in the follow-up run.

## Missing-transaction correction follow-up

Three earlier correction test failures used Sale proposals without `source_system`; the current contract requires an explicit source because Sale can originate from POS or OMS. The fixtures now identify POS. The approval test verifies the created transaction's immutable source lineage, and both SQLite competing-review tests now pass with one decision and one conflict. Invalid-input cases now first validate their unchanged baseline, preventing a missing source from masking whether each invalid field is rejected. A run covering correction approval, effective amounts, reconciliation requeue and correction-to-re-export passed **46 tests**. SQLite results do not establish PostgreSQL concurrency; the optional PostgreSQL tests remain outstanding.

## RTLOG and scoped correction follow-up

The RTLOG endpoint tests now reflect the current complete-file contract. An empty file with a zero-count trailer is accepted; a missing trailer is rejected. Of the three supplied sample files, two complete, mappable files ingest and their repeat deliveries create no new transactions or events. The file containing a merchandise transaction without usable lines is rejected with HTTP 422; transaction and register-event counts remain unchanged. This is rejection of the entire file, not successful handling of that transaction.

The correction-scope test now seeds three store references and verifies a user scoped to S1 sees only S1 and cannot read or modify S2, including a properly sourced missing-transaction proposal. The prior failure asserted no reference-table query, which is no longer an implementation requirement; actual returned scope remains tested. The RTLOG parser, endpoint and correction-scope run passed **19 tests**. Production authorization and real feed acceptance remain outside this isolated SQLite run.

## Broad suite rerun after fixture updates

The six-area sales-audit suite was rerun with local mock-server access: **871 passed, 3 failed, 12 skipped**. The failures were the two previously identified post-void missing-reference expectations and the old oversized-metadata test. The suite began before the latter test was updated; a subsequent isolated request-contract run passed all 13 tests using valid individually bounded multibyte restrictions that exceed the aggregate 256 KiB limit. This does not turn the earlier broad run into a green run. The two post-void failures remain unresolved pending a decision on whether missing source reference should generate a case or be treated as unavailable.

## Disposable PostgreSQL follow-up

An isolated PostgreSQL 18 container was started without using the application's database and removed after testing. Eleven selected PostgreSQL test files covering store-day freshness, correction follow-up and concurrent review, export races and sinks, manual matching, cash ACH, voucher lifecycle and the store-day import-to-export lifecycle ran **17 tests successfully**. The store-day lifecycle covers both acknowledged and unacknowledged export paths. The initial run had 14 passes and three failures caused by missing `source_system` in older Sale fixtures; after those were corrected, one export integration fixture also needed an approved-ontology rule in place of a local rule and a source on its race proposal. The full selected set then passed.

These tests create schemas from ORM metadata and use simulated ontology controls. They do not prove migrations, browser workflows, the deployed release, or all remaining workbook cases. The earlier six-area suite's 12 skips were not all individually mapped by this run, although relevant PostgreSQL tests have now been exercised separately.

## Migrated totals and catalog consistency

Alembic upgraded a fresh disposable PostgreSQL 18 database to head and `alembic check` reported no new upgrade operations. All 200 client catalog IDs were installed. A field-by-field comparison initially found one difference: migration 0132 correctly restricts `MCRCASHED` using the item number's voucher classification, while the current in-code catalog used voucher metadata. The workbook's Sales sheet specifies the item number range. The catalog was corrected to match the workbook and migration, and the comparison then found zero differing catalog fields across the 200 installed IDs. A targeted regression assertion and client/tax/tender tests passed **18 tests**. The disposable database was removed afterward. This comparison does not independently validate the business correctness of every one of the 200 definitions.

## MCR cashed business calculation

A new ingestion-to-re-total scenario checks the workbook's inclusive item range (`100000000000`–`299999999999`) with voucher metadata deliberately pointing to a different range. It includes both boundary values, values immediately outside the range, a matching item with the wrong type, and a return. Only the two qualifying sales ($10 and $20) and the $3 return contribute, yielding `MCRCASHED = $27`. The voucher, client-total, voucher-lifecycle unit and RTLOG checks passed **35 tests**. This verifies the listed inputs in SQLite with simulated controls; declared MCR totals, case handling and other voucher workflows are not established by this one scenario.

## MCR declaration, difference and case gate

An ingestion-to-re-total scenario calculated `MCRCASHED = $27`, then supplied a POS `MCRCASHEDP` declaration of $25, $27 or $29. The resulting `MCRCASHEDO` values were respectively $2, $0 and -$2, matching the workbook formula. With a simulated approved USD `ABS_GT 0` rule on the O total, the two differences created open cases that the close gate rejected; the balanced case created none. A broader voucher, client-total and readiness run passed **32 tests**. The test does not establish that this rule is present in the deployed release, or that case resolution and final closure work for this particular MCR case.

## Store sales-clearing boundaries and exclusions

Six ingestion-to-re-total scenarios cover areas 1999, 2000, 2999, 3000, 3999 and 4000. A $100 merchandise sale less a $10 line discount and $20 return yields $70 for BSI areas 2000–2999 or outlet areas 3000–3999; the workbook combined formula produces +$70 for BSI and -$70 for outlet. The test also includes one listed non-merchandise item, a gift-card item and a remote-register sale, each excluded from these store-clearing totals. No matching amounts were persisted for areas outside both ranges. The system omits totals with no matching dimension rather than persisting zero rows; this is an observed representation, not proof that every downstream consumer treats absence as zero. The sales-clearing, client-total and voucher runs passed **23 tests**. This scenario does not exhaust the workbook's item lists, declarations, or downstream handling.

## Missing sales-clearing value and configured rules

For a BSI store with only a remote-register sale, no `SLSCLBSI` total row was persisted. A simulated Store-level `EQ 0` rule nevertheless created one open case, while a `GT 0` rule created none. A second re-total did not duplicate the open case, and the close gate refused closure while that case remained open. This verifies zero interpretation in the rule evaluator for this scenario; it does not verify how reports or export consumers interpret the missing row, or prove the deployed ontology contains either test rule. Sales-clearing, voucher, configured-tax and rule-comparison checks passed **23 tests**.

## Missing total in report and export selection

A two-store scenario re-totaled identical $10 sales: S1's local register produced `SLSCLBSI = $10`, while S2's remote register produced no SLSCLBSI row. With that definition assigned to Flash Totals for the test, the report included S1's $10 and omitted S2 rather than emitting zero. Audited-total export selection behaved the same after the test directly marked both days Closed to isolate selection. This does not prove that real closure, delivery, acknowledgment, or downstream systems interpret absence correctly, nor that the test report assignment matches deployment. The rule evaluator separately treated the absent value as zero, as recorded above.

The first wider report run exposed an outdated report-list assertion missing the two current Flash reports. After updating that assertion, the sales-clearing, reports and export-selection files passed **23 tests**.

## Remote merchandise and non-merchandise

Sixteen ingestion-to-re-total scenarios cover all eight workbook remote register codes for one BSI and one outlet area. In each, a $100 merchandise sale less a $10 line discount and $20 return yielded $70 in the appropriate `REMSLSBSI` or `REMSLSOUT` total. A listed non-merchandise item yielded $30 in the corresponding `REMNMSBSI` or `REMNMOUT`; a gift-card line and a local-register merchandise sale did not contribute to those expected totals. The remote, store-clearing and client-total test files passed **39 tests**. This samples one non-merchandise code per area and does not establish every list member, every area boundary, combined remote clearing, declaration or downstream case handling. The workbook's remote non-merchandise definitions do not themselves specify area restrictions; the test does not assume otherwise.

## Complete workbook non-merchandise lists

The 30 BSI and 30 outlet item codes were transcribed from the Sales sheet comments. The configured include/exclude sets for remote non-merchandise, remote merchandise and store merchandise match the respective workbook list exactly. Two further scenarios ingest each of the 30 items once on a local register and once on a remote register, plus a near-match item outside the list. For each area, the remote non-merchandise total was $30, while remote and local merchandise totals each included only the $1 near-match. The remote, sales-clearing and client-total files passed **42 tests**. This checks list membership and the described transactions; it does not establish all combinations of returns, corrections or downstream processing for every item.

## Combined remote clearing

The `REMSLSCL` graph was compared with the 26 negative components listed in the workbook Sales sheet comments; the workbook abbreviates `PAY_ADVPYBK` as `PAY_ADVPYB`, so that correspondence remains an interpretation. Giving each configured component $1 produced -$26. An ingestion-to-re-total scenario independently used a $10 pride expense, $5 postage expense and $7 Amazon shipping line and produced `REMSLSCL = -$22`, with each component amount checked. The remote, combined and client-total tests passed **38 tests**. This does not independently validate the raw input restrictions or declarations of all 26 components.

## Paid-in and paid-out item components

The item lists for 28 totals were transcribed from row 29 of the workbook's Paid In Paid Out sheet into a test fixture and compared to the corresponding catalog restrictions. One positive item per total was then ingested and re-totaled to $10, using the relevant store area and marketplace register where required. All 28 calculated as expected. The `DONATE` workbook text omits the word `in` before its parenthesized item list; treating it as membership is an interpretation, recorded in the fixture comparison. The paid-component, remote and combined runs passed **52 tests**. These checks do not independently test every item, exclusion, sign, declaration or downstream case for all 28 totals.

## Paid-component declarations and differences

The workbook's Over Short formulas were transcribed for all 28 paid components, including their differently named declared and difference totals. For each component, a $10 calculated amount was compared against POS declarations totaling $8, $10 and $12 across successive re-totals. All yielded the workbook's calculated-minus-declared differences of $2, $0 and -$2. Supplemental total events were used to vary declarations; the test does not establish that this is the approved production correction workflow. The `PAY_ADVPYB` workbook formula was mapped to the catalog's `PAY_ADVPYBK`, an interpretation already noted above. The paid-component, remote and combined runs passed **80 tests**. They do not prove deployed rules create or resolve cases for each difference.

## Paid-component area and register exclusions

Six re-total scenarios tested areas 1999, 2000, 2999, 3000, 3999 and 4000 with each of nine registers (001, 880, 881, 889, 893, 892, 897, 895 and 896) and three representative item codes. `CRTSYSTPRE` counted nine stamp-replenishment lines only in areas 2000–2999; `POSTAGEXP` counted them only in 3000–3999. Gift-wrap and postage each counted the local register plus 889 and 893; marketplace shipping counted the two appropriate marketplace registers and both matching item categories, yielding $4 per marketplace. The workbook excludes 880, 881, 892, 897, 895 and 896 from gift-wrap and postage but does not exclude 889 or 893. The paid-component and remote-sales runs passed **84 tests**. Other item variants and downstream cases are not established by these boundary scenarios.

## Paid-component returns

For each of the 28 paid components, a $10 sale and a $3 return of a transcribed item produced a $7 calculated total. Against an $8 POS declaration, the corresponding workbook over/short total was -$1. The paid-component, remote and combined test files passed **114 tests**. These are synthetic return records and do not establish original-sale linkage, reconciliation case resolution or the deployed rule configuration.

## Paid-component difference case gates

For each of the 28 paid-component difference totals, a simulated approved USD `ABS_GT 0` rule evaluated a $10 calculation against an $8 declaration. Each produced one open configured-rule exception for $2 with close impact Yes; an unchanged second re-total did not duplicate the case, and `close_blocker` refused closure. The paid-component, remote and combined runs passed **142 tests**. The tests do not establish that any such rule exists in the deployed release, how a changed amount updates an existing case, or how each case is resolved and the day ultimately closed.

## Changed configured-rule case amounts

The 28 paid-component rule scenarios were extended: after the initial $2 difference creates an open case, an additional $3 matching sale changes the latest calculated difference to $5. In every scenario the existing exception amount and case total remain $2 and `amount_needs_review` remains false. The characterization test deliberately records this discrepancy; its passing status does not mean the handling is correct. The rule-breach code returns immediately when it finds an open case, so it does not refresh the existing amount. This can present stale exposure to reviewers. Decide and implement the required update, evidence invalidation and audit behavior before treating this handling as complete. The run used a simulated rule release; audit-event history and downstream export behavior for the changed case were not verified. The paid-component, remote and combined files passed **142 tests**, including these characterizations.

## Changed case: recommendation and export safeguards

The 28 changed-difference scenarios now create a proposed $2 recommendation before the later sale raises the total difference to $5. After re-total, the recommendation remains Proposed, the case and exception retain $2, the amount-review flag remains false, and there is still only one `CONFIGURED_RULE_BREACH` event for the case. The store-day export-invalid predicate blocks an ERP destination while the open exception has no configured export-impact policy. These are characterization results, not approval of stale recommendations. They do not prove an approval or export actually occurs, or cover destination-specific policies and closed cases. The focused **28 scenarios passed** while documenting this unresolved behavior.
