# Store-level (row-level) security

A **store manager** sees and changes only the stores assigned to them. Store staff have read-only Store Day access.
Nimbus enforces this on every store-level
screen and API call. Administrators can maintain an explicit assignment in Configuration → Roles & access; that
assignment overrides the store list in the verified identity. When no local assignment exists, Nimbus uses the
identity's store claim.

## What Ontology Studio must provide

Nimbus authenticates by calling Ontology Studio's auth endpoint (`NIMBUS_ONTOLOGY_AUTH_URL`, `GET /api/v1/auth/me`) and
reads the `user` object it returns. Add the user's stores to that object:

```json
{
  "user": {
    "email": "a@store.example",
    "role": "store_manager",
    "stores": ["STORE-007", "STORE-014"]
  },
  "is_admin": false
}
```

- `stores` is a list of store codes, the same codes Nimbus uses for `store_id` (the `Store` entries in foundation data).
  A comma-separated string is also accepted.
- The key is configurable: `NIMBUS_ONTOLOGY_STORE_CLAIM` is a dotted path into `user`, for example `attributes.stores`.
- The Ontology role must map to `store_manager` or `store_staff` (`NIMBUS_ONTOLOGY_ROLE_MAP`, which includes `store_manager:store_manager` and `store_staff:store_staff`). Store Sales Staff are scoped the same way but can only read Store Days & Totals; a Store Manager can also read Transactions and work Cash Office.

Ontology Studio may also supply store assignments through the identity claim. Nimbus stores local overrides in
`store_assignments`, keyed by the case-insensitive email or username in the verified identity. A saved empty list is
an explicit denial and does not fall back to the identity claim. Administrators can replace an assignment through
`PUT /api/access/store-assignments`; each change is recorded in the immutable audit trail.

## What Nimbus does with it

- **Fail closed.** A store manager with no local assignment and no stores in the identity claim sees nothing. An
  explicit empty local assignment also sees nothing.
- Store Days: the list and the store selector show only assigned stores; opening, re-totaling or closing another
  store's day is refused (403), both in the API middleware and in each handler.
- Cash Office: lists are filtered; declaring cash or recording a deposit for another store is refused.
- Only roles that are scoped are affected. Finance, IT, analysts, auditors and admins are unrestricted.
- Local assignments can use active Foundation stores or store codes already present in transaction or store-day data;
  unknown codes are rejected when an assignment is saved.
- Assignments can select explicit store codes and/or active Foundation location traits. The two sets are combined;
  trait membership is indexed and follows Foundation changes as stores gain, lose, or deactivate traits.
- A store manager cannot widen their scope. The `X-Impersonate-Stores` header is honoured only for a real administrator
  who is acting as a store manager, to simulate an assignment before Ontology provides one.

## Testing before Ontology provides it

Sign in as an administrator, open the user menu, choose **Impersonate -> Store Manager**, and enter the stores to
simulate. Entering none shows the fail-closed behaviour.
