# Previewing the impact of a total-definition change

Status: built. The backend includes migrations 0074 and 0075, and the UI is on Settings → Controls → Totals & rules (`components/settings/TotalDefinitions.tsx`).

## Problem

Before this change, saving a total definition created a revision that was live immediately, whatever its `status`. The save also marked every calculated store day in the date range stale and superseded their pending closure requests. Nobody could see what a change would do before it changed audit results.

## How it works now

### Drafts are not effective

`Worksheet` means draft. `POST /api/total-definitions` and `PUT /api/total-definitions/{id}` only save drafts: any other `status` is refused with 422. A draft is versioned and audited like any revision. However, `effective_definitions` skips it, and saving it leaves every store day and closure request untouched.

Migration 0074 moved every existing `Worksheet` row to `Active`, because all of them were live already. The column default is now `Active`.

### Impact preview

`POST /api/total-definitions/{id}/impact` takes a draft. It returns one row for each store day in the widest date range of the draft and the live revision it replaces. Each row shows:

- **The total's value under the live revision and under the draft.** Both are recalculated from the same canonical transactions, so the difference comes only from the definition change.
- **`changed_totals`**: other configured totals whose values move, such as combined totals.
- **`current_error` and `proposed_error`**: a calculation failure on either side. This includes a combined total that pins the revision being replaced.
- **Side effects**: whether the store day would go stale, and how many pending closure requests would be superseded.
- **`rule_changes`**: enabled audit rules on a `Configured:` total whose result flips, either `Newly breaches` (the next re-total raises a case) or `Clears`. Currency-scoped rules apply only to store days in their currency.

Reconciliation variances (POS to GL, Processor to Bank, POS control) come from the source totals, not from configured totals, so a definition change cannot move them.

The summary counts store days, changed store days, net and absolute change, closed store days hit, store days that would go stale, superseded closure requests, errors, and rules newly breached or cleared.

At most 500 store days are covered (`IMPACT_STORE_DAY_LIMIT`). Above that the report sets `truncated: true` rather than failing. Store-scoped users see only their own stores.

### Submission and a second approver

A draft goes `Worksheet` → `Pending Approval` → `Active`. `Pending Approval` revisions are not effective either.

`POST /api/total-definitions/{id}/submit` takes `{ "impact_fingerprint": "<sha256 from the preview>" }`. It records `submitted_by`, `submitted_at` and the fingerprint. `POST /api/total-definitions/{id}/activate` takes the same body. It must come from a different user from both the editor (`updated_by`) and the submitter. `POST /api/total-definitions/{id}/return` takes `{ "rationale": "..." }` and sends a submission back to `Worksheet`.

Submit and activate both recalculate the impact under the configuration lock and are refused when:

| Response | When |
|---|---|
| 403 | The user is store-scoped. Activation changes every store. |
| 403 | Activate only: the approver edited or submitted the revision. |
| 409 | The revision is not the newest revision of that total. |
| 409 | The recalculated impact no longer matches the fingerprint, for example because transactions arrived. On activate, the fingerprint must also be the one that was submitted; otherwise the reviewer returns it. |
| 422 | The draft would cause a calculation error on a store day that currently calculates cleanly. |

On activation the revision becomes `Active`, `approved_by` and `approved_at` are set, and the existing invalidation runs over its date range. `TOTAL_DEFINITION_SUBMITTED`, `TOTAL_DEFINITION_RETURNED` and `TOTAL_DEFINITION_ACTIVATED` audit events record each step. The activation event names the editor, the submitter, the fingerprint, the summary, whether the preview was truncated, and the revision it replaced.

## Not built yet

- **Editing combined totals' operands in the UI.** The form carries a combined total's operands and operator over unchanged.
- **Previewing an unsaved body.** The preview needs a saved draft.
- **Over/short effect.** ZeTSA stores `over_short_group` and `over_short_operator` but does not calculate over/short yet. Until it does, the preview reports value changes only.
- **Repinning combined totals.** A combined total that pins the replaced revision blocks activation (422). Moving both totals together needs one activation that covers several drafts.
