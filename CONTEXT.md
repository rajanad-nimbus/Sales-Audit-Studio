# Domain terms

- **Verified employee**: A human whose identity is established by the configured identity provider. ZeTSA authorizes this identity but does not create its credentials.
- **Store assignment**: The set of stores a store-scoped employee may access. An assignment can name stores directly, location traits, or both; the resulting store set is their union.
- **Location trait**: A reusable classification applied to active stores, such as a district or region. An assignment to a trait follows the active stores currently carrying it.
- **Store scope**: The effective store set used to limit a store-scoped employee's reads and writes. An explicit local assignment takes precedence over identity-provider store claims, including an empty assignment, which grants access to no stores.
- **Tax summary row**: A transaction-level tax total grouped by tax code and name, with its rate and taxable base.
- **Line tax**: The tax code, rate, and tax amount recorded for one item line. It is separate evidence from a transaction's tax summary row.
