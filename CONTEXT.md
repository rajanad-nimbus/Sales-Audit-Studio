# Domain terms

- **Verified employee**: A human whose identity is established by the configured identity provider. ZeTSA authorizes this identity but does not create its credentials.
- **Store assignment**: The set of stores a Sales Auditor or store-scoped employee may access. An assignment can name stores directly, location traits, or both; the resulting store set is their union.
- **Location trait**: A reusable classification applied to active stores, such as a district or region. An assignment to a trait follows the active stores currently carrying it.
- **Store scope**: The effective store set used to limit a Sales Auditor's or store-scoped employee's reads and writes. Sales Auditors require a local assignment; an empty assignment grants no store access. Store managers and staff can use identity-provider claims when no local assignment exists, while an explicit local assignment takes precedence.
- **Data status**: A store day's derived feed-completeness state, based on received transactions, source control totals, and unresolved feed gaps.
- **Audit status**: A store day's derived audit state, distinguishing required re-totalling, pending errors, totals awaiting sign-off, and signed-off days.
- **Guided task**: A sequence of linked screens that helps an employee complete a common retail audit procedure; each employee sees only steps they are authorized to open.
- **Tax summary row**: A transaction-level tax total grouped by tax code and name, with its rate and taxable base.
- **Line tax**: The tax code, rate, and tax amount recorded for one item line. It is separate evidence from a transaction's tax summary row.
- **Report usage**: A report a total definition contributes to after calculation, independent of the export destinations that consume the same total. The initial report usages are Flash Totals and Flash Sales Year to Date.
- **Total realm**: A category of transaction data available to a raw total definition: the transaction header or one of its line, tax, tender, or discount details.
- **Total realm join**: The approved relationship connecting one selected detail realm to its transaction header. A total can select several detail realms, whose matches are evaluated independently so their records do not multiply one another.
