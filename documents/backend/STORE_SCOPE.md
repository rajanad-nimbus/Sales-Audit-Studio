# Store-level (row-level) security

A **store manager** sees and changes only the stores assigned to them. Nimbus enforces this on every store-level
screen and API call. The assignment itself is **maintained in Ontology Studio** and arrives with the user's identity.

## What Ontology Studio must provide

Nimbus authenticates by calling Ontology Studio's auth endpoint (`NIMBUS_ONTOLOGY_AUTH_URL`, `GET /api/v1/auth/me`) and
reads the `user` object it returns. Add the user's stores to that object:

```json
{ "user": { "email": "a@store.example", "role": "store_manager", "stores": ["STORE-007", "STORE-014"] }, "is_admin": false }
```

- `stores` is a list of store codes, the same codes Nimbus uses for `store_id` (the `Store` entries in foundation data).
  A comma-separated string is also accepted.
- The key is configurable: `NIMBUS_ONTOLOGY_STORE_CLAIM` is a dotted path into `user`, for example `attributes.stores`.
- The Ontology role must map to `store_manager` (`NIMBUS_ONTOLOGY_ROLE_MAP`, which now includes `store_manager:store_manager`).

Ontology Studio does not hold this today: its `users` table has `id, openId, name, email, username, role, ...` and
`/auth/me` returns only those. It needs somewhere to maintain the assignment (a per-user store list, or a
user-to-store relationship) and to return it in `/auth/me`.

## What Nimbus does with it

- **Fail closed.** A store manager with no stores, a missing claim, or a malformed one sees nothing.
- Store Days: the list and the store selector show only assigned stores; opening, re-totaling or closing another
  store's day is refused (403), both in the API middleware and in each handler.
- Cash Office: lists are filtered; declaring cash or recording a deposit for another store is refused.
- Only roles that are scoped are affected. Finance, IT, analysts, auditors and admins are unrestricted.
- A store manager cannot widen their scope. The `X-Impersonate-Stores` header is honoured only for a real administrator
  who is acting as a store manager, to simulate an assignment before Ontology provides one.

## Testing before Ontology provides it

Sign in as an administrator, open the user menu, choose **Impersonate -> Store Manager**, and enter the stores to
simulate. Entering none shows the fail-closed behaviour.
