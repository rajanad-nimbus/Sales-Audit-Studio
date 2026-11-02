"""Sales Audit rules over stored line detail: unknown item, return over sold, tax mismatch."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, tuple_

from apps.controls.foundation.models import FoundationReference
from apps.transactions.models import CanonicalTransaction, TransactionDiscount, TransactionLine

CHUNK = 400
NON_MERCHANDISE = {"SHIPPING"}
CENT = Decimal("0.01")


def chunks(items, size=CHUNK):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def load_lines(db, transaction_ids) -> dict[str, list]:
    lines = defaultdict(list)
    for batch in chunks(transaction_ids):
        rows = await db.execute(
            select(TransactionLine).where(TransactionLine.transaction_id.in_(batch))
        )
        for line in rows.scalars():
            lines[line.transaction_id].append(line)
    return lines


async def run_detail_rules(db, transactions, flag):
    """Raise item, return-quantity and tax findings for the transactions that carry line detail."""
    lines = await load_lines(db, [t.id for t in transactions])
    audited = [t for t in transactions if lines.get(t.id)]
    await flag_unknown_items(db, audited, lines, flag)
    await flag_tax_mismatches(db, audited, lines, flag)
    await flag_returns_over_sold(db, audited, lines, flag)


async def flag_unknown_items(db, transactions, lines, flag):
    """An empty item code always counts; a code missing from the item master counts once it is synced."""
    catalog = set(
        (
            await db.execute(
                select(FoundationReference.code).where(
                    FoundationReference.category == "Item", FoundationReference.active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    for t in transactions:
        for line in lines[t.id]:
            code = line.item_code
            if code in NON_MERCHANDISE:
                continue
            if code == "NO-SKU" or (catalog and code not in catalog):
                flag("SKU_NOT_FOUND", abs(t.signed_amount), t)
                break


async def flag_tax_mismatches(db, transactions, lines, flag):
    """Tax outside what the line rates allow, after discounts and a cent of rounding per line."""
    discounts = defaultdict(lambda: Decimal("0"))
    for batch in chunks([t.id for t in transactions]):
        rows = await db.execute(
            select(TransactionDiscount.transaction_id, TransactionDiscount.amount).where(
                TransactionDiscount.transaction_id.in_(batch)
            )
        )
        for transaction_id, amount in rows:
            discounts[transaction_id] += abs(amount)
    for t in transactions:
        rows = lines[t.id]
        if any(line.tax_amount and not line.tax_rate for line in rows):
            continue  # a tax with no rate cannot be checked
        rated = [line for line in rows if line.tax_rate]
        if not rated:
            continue
        top = sum((abs(line.gross_amount) * line.tax_rate for line in rated), Decimal("0"))
        tolerance = CENT * (len(rows) + 1)
        highest_rate = max(line.tax_rate for line in rated)
        floor = max(top - highest_rate * discounts[t.id], Decimal("0")) - tolerance
        tax = sum((abs(line.tax_amount) for line in rows), Decimal("0"))
        if tax > top + tolerance:
            flag("TAX_MISMATCH", tax - top, t)
        elif tax < floor:
            flag("TAX_MISMATCH", floor - tax, t)


async def flag_returns_over_sold(db, transactions, lines, flag):
    """Quantity returned or refunded for an item, across all returns, above what the sale carried."""
    returns = [t for t in transactions if t.transaction_type in ("Return", "Refund")]
    if not returns:
        return
    originals = await original_sales(db, returns)
    if not originals:
        return
    sale_ids = {sale.id for sale in originals.values()}
    sold = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for sale_id, sale_lines in (await load_lines(db, sale_ids)).items():
        for line in sale_lines:
            sold[sale_id][line.item_code] += abs(line.quantity)

    sales = {sale.id: sale for sale in originals.values()}
    related = await returns_against(db, sales.values())
    related_lines = await load_lines(db, list(related))
    returned = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for return_id, sale_id in related.items():
        for line in related_lines.get(return_id, []):
            returned[sale_id][line.item_code] += abs(line.quantity)

    for t in returns:
        sale = originals.get(t.id)
        if not sale or not sold[sale.id]:
            continue
        for line in lines[t.id]:
            code = line.item_code
            if code not in NON_MERCHANDISE and returned[sale.id][code] > sold[sale.id][code]:
                flag("RETURN_OVER_SOLD", abs(t.signed_amount), t)
                break


async def original_sales(db, returns) -> dict[str, CanonicalTransaction]:
    """The sale each return or refund points at: a linked POS sale, or a Shopify order by store and id."""
    found: dict[str, CanonicalTransaction] = {}
    linked = {t.id: t.original_transaction_id for t in returns if t.original_transaction_id}
    for batch in chunks(set(linked.values())):
        rows = await db.execute(
            select(CanonicalTransaction).where(CanonicalTransaction.id.in_(batch))
        )
        by_id = {sale.id: sale for sale in rows.scalars()}
        found.update({rid: by_id[sid] for rid, sid in linked.items() if sid in by_id})
    refs = {
        (t.store_id, t.original_reference): t
        for t in returns
        if t.id not in linked and t.original_reference
    }
    for batch in chunks(refs):
        rows = await db.execute(
            select(CanonicalTransaction).where(
                CanonicalTransaction.transaction_type == "Order",
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.payment_reference).in_(
                    batch
                ),
            )
        )
        orders = {(o.store_id, o.payment_reference): o for o in rows.scalars()}
        for key in batch:
            if key in orders:
                found[refs[key].id] = orders[key]
    return found


async def returns_against(db, sales) -> dict[str, str]:
    """Every stored return or refund of these sales (not only this run's), as return id -> sale id."""
    sales = list(sales)
    by_key = {
        (s.store_id, s.payment_reference): s.id for s in sales if s.transaction_type == "Order"
    }
    related: dict[str, str] = {}
    for batch in chunks([s.id for s in sales]):
        rows = await db.execute(
            select(CanonicalTransaction.id, CanonicalTransaction.original_transaction_id).where(
                CanonicalTransaction.original_transaction_id.in_(batch)
            )
        )
        related.update({rid: sid for rid, sid in rows})
    for batch in chunks(by_key):
        rows = await db.execute(
            select(
                CanonicalTransaction.id,
                CanonicalTransaction.store_id,
                CanonicalTransaction.original_reference,
            ).where(
                CanonicalTransaction.transaction_type == "Refund",
                tuple_(CanonicalTransaction.store_id, CanonicalTransaction.original_reference).in_(
                    batch
                ),
            )
        )
        related.update({rid: by_key[(store, ref)] for rid, store, ref in rows})
    return related
