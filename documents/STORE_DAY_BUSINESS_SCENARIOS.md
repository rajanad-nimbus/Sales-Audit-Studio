# Store-day business scenarios

These scenarios describe the local Docker test data for November 18, 2026. Each store has its own store day and can be reviewed in the Store Days and Cases workspaces.

## Perfect match and automatic closure

### Scenario

Store `NT006` has a $20 cash sale, a matching $20 cash declaration, a POS control total, and a $20 GL posting. It also has a paid $10 Shopify order and a matching Shopify day total, which satisfy the active order-presence rule and source controls.

### Steps and expected result

1. Load the POS sale, declaration, POS control, GL posting, Shopify order, and Shopify day total for the store and business date.
2. Run reconciliation. The source controls match and reconciliation raises no cases.
3. Re-total the store day. Ingestion shows **Complete**, the audit shows **Clean**, and there are no unexplained variances.
4. Run the store-day agent after the business date has passed in UTC. With automatic close enabled and no other closure blockers, the agent signs off and closes the day.

The local test produced **Closed**, with `store-day-agent` recorded as both sign-off and closing actor.

## Duplicate sale with different payment references

### Scenario

Store `NT007` has two $20 cash sales for the same store, business date, register, and transaction number. The item and tender details match. Their payment references differ. The POS declaration, control total, and GL posting originally include both sales, for $40.

### Steps and expected result

1. Load both sales and the supporting source controls and GL posting.
2. Run reconciliation. It raises a `DUPLICATE_SALES` case. The test also raises a `GL_VARIANCE` case.
3. Run the store-day agent. It compares the flagged sale with its single original, ignoring payment references while checking the other configured transaction, item, and tender fields.
4. When those fields match, the agent marks the copy as `Duplicate`, closes the duplicate case, records the original and copy payment references in the audit event, and re-totals the store day without the copy.
5. Review the remaining reconciliation results. The day closes automatically only after all remaining cases and unexplained variances are resolved and the closure requirements are met.

The local test closed the duplicate case and excluded one $20 sale. The store day remained **Auditing** with an open GL variance case and three unexplained variances. The original $40 supporting values still need investigation after the duplicate exclusion.

A differing item, amount, tender, or other checked field, or the absence of a single original, leaves the duplicate case for review. The duplicate test compares selected fields and does not establish that every source field is identical.
