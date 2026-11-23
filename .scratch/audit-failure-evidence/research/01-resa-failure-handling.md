# ReSA 15.0.1: from failing audit to resolved

Source: documents/resa-1501-ug.pdf. Page numbers are PDF page indexes (printed chapter pages in brackets).
Context: documents/RESA_DIFFERENCES.md section 4 (its Schema-tagged claims about store/HQ override and required flags are not in the guide).

## Guide facts

1. Flow. Transactions load, are totaled, audited and reviewed. Errors from validation "must be corrected or overridden", then data is exported. p.49 [7-1]
2. Auditor job: "review all exception errors related to your stores and either correct or override these errors". Review points are the Store Day Summary Error List, misc totals, over/short, missing transactions and import/export log; also the Cashier/Register summary. p.49 [7-1]
3. Resolution rule: once all errors are corrected or overridden, ReSA sets the store/day audit status to Audited automatically, and it becomes available for export. p.50 [7-2]
4. Fixing: Store Day Audit > open store day in Edit mode > Error List tab > select error > Actions > Fix Error opens Transaction Detail > resolve > OK. p.66 [7-18]. Transaction Maintenance also has "Errors" (list the transaction's errors) and "Refresh Errors" (re-evaluate after edits). p.55 [7-7/7-8]
5. Override: only described conceptually. "Some errors cannot be corrected. For such errors, you need to acknowledge the error and then override these errors." p.71 (glossary). Rule definition FAQ: "the retailer defines if the error can be overridden as well as what systems the error impacts." p.38. The guide gives no override screen steps, no reason field for error override, and no approver.
6. Error impact: an error can block export of a transaction or total only to the systems named in its impact; other systems still receive it. Rules are tied to predefined errors, which must be defined (with impacts) before the rule. p.38, p.43, p.44 (Rule Errors field), p.71
7. Status values: Unaudited, Store Errors Pending, HQ Errors Pending, Re-Totalling/Auditing Required, Totaled, Audited. They are only listed as search filters. p.65 [7-17]. Status is described as showing whether "errors were identified at the store or headquarter for correction". p.15 or nearby intro
8. Audit trail. Revisions: before updating a transaction, the old version is moved to a revision table, giving "a complete audit trail". p.17. Transaction Audit Trail window lists all revisions, with item, tender and export revisions. p.55, p.61. Total Audit Trail exists too (p.62), and Over/Short has an Audit Trail option. p.66. Totals definitions are versioned by revision number. p.22 area. A previously exported revision is reversed on re-export. p.69
9. Comments: More Actions > Comments > Actions > Add > free text > OK. No author, attachment or link to a specific error is described. p.55 [7-7]
10. Price override info is a separate transaction field with an Override Reason (not error override). p.53

## Not found in the guide

- Any definition of "required" versus "optional" errors. Neither the term nor a required flag appears. (The related "Required by the system" appears only for totals, p.23.)
- A store-versus-HQ override permission, or how Store/HQ Errors Pending are set or cleared.
- Attachments, evidence capture, case/investigation workflow, assignments, approvals or two-person control for errors.

## Inference (not in the guide)

- Resolution is binary per error (fixed in the transaction or overridden) with the only automatic gate being "no unresolved errors" -> Audited.
- The only investigation support is the free-text transaction comment plus revision history of what changed; the "why" lives in comments, if anyone writes one.
- Store vs HQ and required vs override are likely table-driven (SA_ERROR_CODES flags per RESA_DIFFERENCES, schema-sourced) and not documented behaviour here.
