#!/usr/bin/env python3
"""Create a matrix of dummy orders, returns, exchanges and voids in the Shopify dev store.

Scenarios mirror what Oracle Retail Sales Audit has to cope with: sales, returns, exchanges,
voids and cancellations across tenders, tax shapes, discounts, shipping and currencies.
Needs the custom app scopes write_orders (plus read_orders, read_locations). Run from the repo root:
    python3 scripts/shopify_seed.py              # creates every scenario
    python3 scripts/shopify_seed.py --dry        # prints what it would create
    python3 scripts/shopify_seed.py --only=split # only scenarios whose name contains "split"
    python3 scripts/shopify_seed.py --start=4    # skip the first 4 scenarios
Tax lines are always sent, but Shopify only adds them to the order total when the store collects tax
(Settings > Taxes and duties). The nimbus test store does not, so totals and payments here exclude tax;
set SHOPIFY_SEED_TAX_CHARGED=1 once the store charges it.
Orders are tagged "zetsa-seed" (plus a group tag) so they are easy to find.
Audit-error shapes (return over quantity, duplicates, bad totals) cannot be created in Shopify;
those are built as raw feed records in backend/apps/ingest/tests/test_shopify_audit_shapes.py.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


def load_env(path=".env"):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


load_env()
BASE = f"{os.environ['SHOPIFY_STORE_URL'].rstrip('/')}/admin/api/{os.environ.get('SHOPIFY_API_VERSION', '2025-10')}"
HEADERS = {"X-Shopify-Access-Token": os.environ["SHOPIFY_ACCESS_TOKEN"], "Content-Type": "application/json"}
DRY = "--dry" in sys.argv
TAX_CHARGED = os.environ.get("SHOPIFY_SEED_TAX_CHARGED") == "1"
ONLY = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")
START = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--start=")), 0))  # resume after a failure


class ApiError(Exception):
    pass


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=HEADERS,
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:  # dev stores cap order creation; wait and retry
            print("    rate limited, waiting 65s...", flush=True)
            time.sleep(65)
            return call(method, path, body)
        raise ApiError(f"{method} {path} failed {e.code}: {e.read().decode()[:400]}") from None


def now():
    return datetime.now(timezone.utc)  # noqa: UP017 (script runs on system python 3.9)


def money(x):
    return f"{x:.2f}"


def L(title, price, qty=1, taxable=True, discount=0, sku=None, ship=False):
    """A line item. `discount` is a line-level discount in currency units."""
    item = {"title": title, "price": money(price), "quantity": qty, "taxable": taxable,
            "requires_shipping": ship, "sku": title.upper().replace(" ", "-")[:40] if sku is None else sku}
    if discount:
        item["total_discount"] = money(discount)
    return item


SHIRT, JEANS, JACKET = L("Blue shirt", 40), L("Jeans", 80), L("Jacket", 150)
HAT, SNEAKERS, WATCH = L("Hat", 25), L("Sneakers", 120), L("Watch", 300)
GIFT_CARD = L("Gift card", 50, taxable=False)
GIFT_WRAP = L("Gift wrap", 5, taxable=True)
TIP = L("Tip", 6, taxable=False)
HANDLING = L("Handling fee", 4, taxable=True)

TAX_NONE, TAX_FLAT = [], [("Sales tax", 0.08)]
TAX_MULTI = [("State tax", 0.06), ("County tax", 0.01), ("City tax", 0.005)]
CARD, CASH, GIFT_TENDER, CREDIT = "manual", "cash", "gift_certificate", "store_credit"  # a real gift_card gateway needs a card id
SPLIT = [(CARD, 0.5), (GIFT_TENDER, "rest")]  # (gateway, share of total or "rest")


def scenario(name, group, items, **kw):
    spec = {"name": name, "group": group, "items": items, "taxes": TAX_FLAT, "tenders": [(CARD, "rest")],
            "status": "paid", "discounts": [], "shipping": 0, "ship_taxable": False, "inclusive": False,
            "currency": "USD", "days_ago": 0, "refunds": [], "fulfilled": False, "cancel": False,
            "exchange": None, "location": 0, "email": None}
    spec.update(kw)
    return spec


def full_refund(**kw):
    return dict({"lines": "all", "shipping": True}, **kw)


def line_refund(*idx, qty=None, **kw):
    return dict({"lines": [(i, (qty or {}).get(i, 1)) for i in idx]}, **kw)


S = []
# core: the original eight, unchanged
S += [
    scenario("plain paid", "core", [SHIRT, L("Socks", 10, 2)]),
    scenario("paid and fulfilled", "core", [JEANS], fulfilled=True),
    scenario("discounted", "core", [JACKET], discounts=[("SEED", "fixed", 30)]),
    scenario("unpaid", "core", [HAT], status="pending"),
    scenario("partial refund", "core", [SNEAKERS], fulfilled=True, refunds=[{"amount": 50}]),
    scenario("full refund", "core", [L("Backpack", 60)], refunds=[{"amount": "full"}]),
    scenario("multi-line big", "core", [WATCH, L("Strap", 20, 3)], fulfilled=True,
             discounts=[("SEED", "fixed", 20)]),
    scenario("no tax", "core", [GIFT_CARD], taxes=TAX_NONE),
]

# sale matrix: tender x tax shape
TENDERS = {"cash": [(CASH, "rest")], "card": [(CARD, "rest")], "giftcard": [(GIFT_TENDER, "rest")],
           "split": SPLIT}
TAXES = {"zero": TAX_NONE, "flat": TAX_FLAT, "multi": TAX_MULTI}
for t, tenders in TENDERS.items():
    for x, taxes in TAXES.items():
        S.append(scenario(f"sale {t} {x}", "sale", [SHIRT, L("Socks", 10, 2)], tenders=tenders, taxes=taxes))
    S.append(scenario(f"sale {t} inclusive", "sale", [SHIRT, L("Socks", 10, 2)], tenders=tenders,
                      taxes=[("VAT", 0.13)], inclusive=True))

# tax shapes
S += [
    scenario("tax mixed taxable and exempt lines", "tax", [SHIRT, L("Grocery bundle", 30, taxable=False)]),
    scenario("tax exempt customer", "tax", [JEANS], taxes=TAX_NONE, email="exempt@example.com"),
    scenario("tax on shipping", "tax", [SHIRT], shipping=8, ship_taxable=True),
    scenario("tax multi jurisdiction with shipping", "tax", [JEANS, HAT], taxes=TAX_MULTI, shipping=8,
             ship_taxable=True),
    scenario("tax inclusive with shipping", "tax", [JEANS], taxes=[("VAT", 0.2)], inclusive=True,
             shipping=6, ship_taxable=True),
    scenario("tax rounding many small lines", "tax", [L("Pen", 0.99, 7), L("Clip", 0.33, 9)],
             taxes=[("Sales tax", 0.0825)]),
]

# discounts
S += [
    scenario("discount percent", "discount", [JACKET], discounts=[("PCT10", "percent", 10)]),
    scenario("discount line level", "discount", [SHIRT, L("Belt", 35, discount=7)]),
    scenario("discount stacked", "discount", [JACKET, HAT],
             discounts=[("EMPLOYEE20", "percent", 20), ("COUPON5", "fixed", 5)]),
    scenario("discount free shipping", "discount", [JEANS], shipping=9, discounts=[("FREESHIP", "shipping", None)]),
    scenario("discount loyalty with shipping", "discount", [WATCH], shipping=9,
             discounts=[("LOYALTY15", "percent", 15)]),
]

# item types
S += [
    scenario("item shipping charge", "item", [SHIRT], shipping=7.5),
    scenario("item tip", "item", [L("Coffee", 4.5, 2), TIP], taxes=TAX_FLAT),
    scenario("item service fees", "item", [SHIRT, GIFT_WRAP, HANDLING]),
    scenario("item gift card sale", "item", [GIFT_CARD, GIFT_CARD], taxes=TAX_NONE),
    scenario("item gift card redeemed", "item", [JEANS], tenders=[(GIFT_TENDER, "rest")]),
    scenario("item missing sku", "item", [L("Mystery item", 19.99, sku="")]),
    scenario("item unknown sku", "item", [L("Unlisted item", 29.99, sku="UNKNOWN-SKU-000")]),
]

# tenders
S += [
    scenario("tender cod pending", "tender", [JEANS], status="pending", tenders=[("cash_on_delivery", "rest")]),
    scenario("tender three way split", "tender",
             [WATCH], tenders=[(CARD, 0.5), (GIFT_TENDER, 0.3), (CREDIT, "rest")]),
    scenario("tender overpaid", "tender", [HAT], tenders=[(CASH, "rest")], overpay=5),
    scenario("tender underpaid partially paid", "tender", [JACKET], status="partially_paid"),
    scenario("tender authorized only", "tender", [JEANS], status="authorized"),
]

# order states
S += [
    scenario("state voided authorization", "state", [JEANS], status="voided"),
    scenario("state cancelled unpaid", "state", [HAT], status="pending", cancel=True),
    scenario("state cancelled paid", "state", [SHIRT], cancel=True),
    scenario("state fulfilled and partially refunded", "state", [JEANS, HAT], fulfilled=True,
             refunds=[line_refund(0)]),
]

# returns
S += [
    scenario("return one line", "return", [JEANS, HAT], refunds=[line_refund(1)]),
    scenario("return partial quantity", "return", [L("Socks", 10, 4)], refunds=[line_refund(0, qty={0: 2})]),
    scenario("return full order with shipping", "return", [JACKET], shipping=8, ship_taxable=True,
             refunds=[full_refund()]),
    scenario("return after discount", "return", [JACKET, SHIRT], discounts=[("PCT10", "percent", 10)],
             refunds=[line_refund(0)]),
    scenario("return restocked", "return", [SNEAKERS], refunds=[line_refund(0, restock="return")]),
    scenario("return not restocked", "return", [SNEAKERS], refunds=[line_refund(0, restock="no_restock")]),
    scenario("return with restocking fee", "return", [JACKET], refunds=[line_refund(0, fee=15)]),
    scenario("return in two steps", "return", [JEANS, SHIRT, HAT],
             refunds=[line_refund(0), line_refund(1)]),
    scenario("return multi jurisdiction tax", "return", [JEANS], taxes=TAX_MULTI, refunds=[line_refund(0)]),
    scenario("return inclusive tax", "return", [JEANS], taxes=[("VAT", 0.13)], inclusive=True,
             refunds=[line_refund(0)]),
    scenario("return to gift card tender only", "return", [JEANS], tenders=SPLIT,
             refunds=[line_refund(0, gateway=GIFT_TENDER, keep=43.20)]),
    scenario("return days later", "return", [WATCH], days_ago=6, refunds=[line_refund(0)]),
    scenario("return weeks later", "return", [JACKET], days_ago=21, refunds=[full_refund(days_ago=0)]),
]

# exchanges: a return plus a replacement order linked by tag and note
S += [
    scenario("exchange even", "exchange", [JEANS], refunds=[line_refund(0)],
             exchange=scenario("exchange even replacement", "exchange", [L("Jeans (size up)", 80)],
                               tenders=[("exchange_credit", "rest")])),
    scenario("exchange customer pays more", "exchange", [SHIRT], refunds=[line_refund(0)],
             exchange=scenario("exchange pays more replacement", "exchange", [L("Premium shirt", 75)],
                               tenders=[("exchange_credit", 0.55), (CARD, "rest")])),
    scenario("exchange customer refunded difference", "exchange", [JACKET],
             refunds=[line_refund(0, keep=54)],
             exchange=scenario("exchange refund diff replacement", "exchange", [L("Light jacket", 50)],
                               tenders=[("exchange_credit", "rest")])),
    scenario("exchange across days", "exchange", [SNEAKERS], days_ago=4, refunds=[line_refund(0)],
             exchange=scenario("exchange across days replacement", "exchange", [L("Sneakers (other size)", 120)],
                               tenders=[("exchange_credit", "rest")])),
]

# currencies (needs multi-currency enabled on the store)
S += [
    scenario("currency cad sale", "currency", [JEANS], currency="CAD", taxes=[("GST", 0.05), ("PST", 0.07)]),
    scenario("currency eur inclusive", "currency", [JACKET], currency="EUR", taxes=[("VAT", 0.21)],
             inclusive=True),
    scenario("currency eur refund", "currency", [SHIRT], currency="EUR", refunds=[line_refund(0)]),
]

# timing and locations
S += [
    scenario("timing backdated sale", "timing", [JEANS], days_ago=3),
    scenario("timing last week sale", "timing", [HAT], days_ago=7),
    scenario("timing month old sale", "timing", [SHIRT], days_ago=30),
    scenario("location second", "location", [JEANS], location=1),
]


def totals(spec):
    """Order total and tax the way Shopify will see them, so tenders can match exactly."""
    items = spec["items"]
    gross = sum(float(i["price"]) * i["quantity"] for i in items)
    line_disc = sum(float(i.get("total_discount", 0)) for i in items)
    base = gross - line_disc
    disc, ship_disc, remaining = 0.0, 0.0, base
    for _, kind, value in spec["discounts"]:
        if kind == "shipping":
            ship_disc = spec["shipping"]
            continue
        amount = round(remaining * value / 100, 2) if kind == "percent" else value
        disc += amount
        remaining -= amount
    net = base - disc
    ship_net = spec["shipping"] - ship_disc
    taxable_sub = sum(float(i["price"]) * i["quantity"] - float(i.get("total_discount", 0))
                      for i in items if i["taxable"])
    taxable = taxable_sub * (net / base) if base else 0
    if spec["ship_taxable"]:
        taxable += ship_net
    tax_lines = []
    for title, rate in spec["taxes"]:
        price = round(taxable * rate / (1 + rate), 2) if spec["inclusive"] else round(taxable * rate, 2)
        if price:
            tax_lines.append({"title": title, "rate": rate, "price": money(price)})
    tax = sum(float(t["price"]) for t in tax_lines)
    total = round(net + ship_net + (0 if spec["inclusive"] or not TAX_CHARGED else tax), 2)
    return total, round(tax, 2), round(disc + ship_disc + line_disc, 2), tax_lines


def tender_amounts(spec, total):
    """Split `total` across tenders; the last tender takes the rounding remainder."""
    paid, out = 0.0, []
    for n, (gateway, share) in enumerate(spec["tenders"]):
        last = n == len(spec["tenders"]) - 1 or share == "rest"
        amount = round(total - paid, 2) if last else round(total * share, 2)
        out.append((gateway, amount))
        paid += amount
        if last:
            break
    if spec.get("overpay"):
        out[-1] = (out[-1][0], round(out[-1][1] + spec["overpay"], 2))
    return out


def order_payload(spec, total, tax_lines, location_id, locations):
    stamp = (now() - timedelta(days=spec["days_ago"])).isoformat(timespec="seconds")
    order = {
        "line_items": spec["items"], "financial_status": "pending", "send_receipt": False,
        "send_fulfillment_receipt": False, "inventory_behaviour": "bypass",
        "tags": f"zetsa-seed, zetsa-seed-{spec['group']}", "currency": spec["currency"],
        "note": f"ZeTSA seed: {spec['name']}", "email": spec["email"] or "seed@example.com",
        "created_at": stamp, "processed_at": stamp, "taxes_included": spec["inclusive"],
    }
    if tax_lines:
        order["tax_lines"] = tax_lines
    if spec["shipping"]:
        order["shipping_lines"] = [{"title": "Standard shipping", "price": money(spec["shipping"]), "code": "STD"}]
    if spec["discounts"]:
        codes = [{"code": c, "amount": money(v if v is not None else spec["shipping"]),
                  "type": {"fixed": "fixed_amount", "percent": "percentage", "shipping": "shipping"}[k]}
                 for c, k, v in spec["discounts"]]
        order["discount_codes"] = codes
    if locations:
        order["location_id"] = locations[min(location_id, len(locations) - 1)]
    status = spec["status"]
    amounts = tender_amounts(spec, total)
    if status in ("paid", "partially_paid", "authorized", "voided"):
        kind = "sale" if status in ("paid", "partially_paid") else "authorization"
        if status == "partially_paid":
            amounts = [(g, round(a * 0.5, 2)) for g, a in amounts]
        order["financial_status"] = {"voided": "authorized"}.get(status, status)
        order["transactions"] = [{"kind": kind, "status": "success", "amount": money(a), "gateway": g}
                                 for g, a in amounts]
    elif status == "pending" and spec["tenders"][0][0] == "cash_on_delivery":
        order["transactions"] = [{"kind": "sale", "status": "pending", "amount": money(total),
                                  "gateway": "cash_on_delivery"}]
    if spec["fulfilled"]:
        order["fulfillment_status"] = "fulfilled"
    return order


def refund_order(order, spec, refund, locations):
    """Refund by line items (Shopify computes tax) or by a plain amount."""
    when = (now() - timedelta(days=refund.get("days_ago", 0))).isoformat(timespec="seconds")
    sale = next(t for t in call("GET", f"/orders/{order['id']}/transactions.json")["transactions"]
                if t["kind"] in ("sale", "capture")
                and (not refund.get("gateway") or t["gateway"] == refund["gateway"]))
    body = {"note": f"ZeTSA seed refund: {spec['name']}", "notify": False, "processed_at": when}
    if "amount" in refund:
        total = float(order["total_price"])
        amount = total if refund["amount"] == "full" else float(refund["amount"])
        body["transactions"] = [{"parent_id": sale["id"], "amount": money(amount), "kind": "refund",
                                 "gateway": sale["gateway"]}]
        return call("POST", f"/orders/{order['id']}/refunds.json", {"refund": body})["refund"]
    lines = order["line_items"]
    picks = [(n, i["quantity"]) for n, i in enumerate(lines)] if refund["lines"] == "all" else refund["lines"]
    restock = refund.get("restock", "no_restock")
    items = [{"line_item_id": lines[n]["id"], "quantity": q,
              "restock_type": restock, **({"location_id": locations[0]} if restock == "return" and locations else {})}
             for n, q in picks]
    calc_body = {"refund_line_items": items}
    if refund.get("shipping"):
        calc_body["shipping"] = {"full_refund": True}
    calc = call("POST", f"/orders/{order['id']}/refunds/calculate.json", {"refund": calc_body})["refund"]
    suggested = sum(float(t["amount"]) for t in calc["transactions"])
    # "keep" is credit retained for an exchange replacement; "fee" is a restocking fee
    amount = round(suggested - float(refund.get("keep", 0)) - refund.get("fee", 0), 2)
    body.update(calc_body)
    body["transactions"] = [{"parent_id": sale["id"], "amount": money(amount), "kind": "refund",
                             "gateway": sale["gateway"]}]
    return call("POST", f"/orders/{order['id']}/refunds.json", {"refund": body})["refund"]


def create(spec, locations, parent=None):
    total, tax, discount, tax_lines = totals(spec)
    tender_text = "+".join(f"{g}:{a:.2f}" for g, a in tender_amounts(spec, total))
    print(f"  {spec['name']:42} total {total:>8.2f} tax {tax:>6.2f} disc {discount:>6.2f} "
          f"{spec['currency']} [{spec['status']}] {tender_text}"
          + (f" refunds={len(spec['refunds'])}" if spec["refunds"] else "")
          + (" +exchange" if spec["exchange"] else ""))
    if DRY:
        return
    payload = order_payload(spec, total, tax_lines, spec["location"], locations)
    if parent:
        payload["tags"] += f", exchange-of-{parent['order_number']}"
        payload["note"] += f" (exchange of order #{parent['order_number']})"
    order = call("POST", "/orders.json", {"order": payload})["order"]
    if spec["status"] == "voided":
        auth = call("GET", f"/orders/{order['id']}/transactions.json")["transactions"][0]
        call("POST", f"/orders/{order['id']}/transactions.json", {"transaction": {
            "kind": "void", "parent_id": auth["id"], "gateway": auth["gateway"]}})
    if spec["cancel"]:
        call("POST", f"/orders/{order['id']}/cancel.json", {"reason": "customer", "email": False, "restock": False})
    for refund in spec["refunds"]:
        refund_order(order, spec, refund, locations)
    if spec["exchange"]:
        create(spec["exchange"], locations, parent=order)
    print(f"    created order #{order['order_number']}")


def main():
    scenarios = [s for s in S if ONLY in s["name"]][START:]
    locations = [] if DRY else [loc["id"] for loc in call("GET", "/locations.json")["locations"]]
    print("Dry run" if DRY else f"Creating {len(scenarios)} scenarios in {BASE.split('/admin')[0]}"
          f" ({len(locations)} locations)")
    failed = []
    for n, s in enumerate(scenarios, START):
        if s["location"] and len(locations) < 2 and not DRY:
            print(f"  {s['name']:42} skipped: store has a single location")
            continue
        try:
            create(s, locations)
        except (ApiError, KeyError, StopIteration) as e:
            failed.append((n, s["name"], e))
            print(f"    FAILED: {e}")
    verb = "planned" if DRY else "created"
    print(f"\n{len(scenarios) - len(failed)} of {len(scenarios)} scenarios {verb}")
    for n, name, e in failed:
        print(f"  #{n} {name}: {e}")
    sys.exit(1 if failed else 0)


main()
