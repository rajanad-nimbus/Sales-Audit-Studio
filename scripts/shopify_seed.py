#!/usr/bin/env python3
"""Create dummy orders (and refunds) in the Shopify dev store from .env settings.

Needs the custom app scopes write_orders (plus read_orders). Run from the repo root:
    python3 scripts/shopify_seed.py          # creates the orders
    python3 scripts/shopify_seed.py --dry    # prints what it would create
    python3 scripts/shopify_seed.py --start=4  # skip the first 4 cases
Tagged "zetsa-seed" so the orders are easy to find.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request


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
        sys.exit(f"{method} {path} failed {e.code}: {e.read().decode()[:400]}")


def line(title, price, qty=1):
    return {"title": title, "price": f"{price:.2f}", "quantity": qty, "taxable": True, "requires_shipping": False}


# (name, line items, discount $, tax rate, status, refund $ or "full" or None, fulfilled)
CASES = [
    ("plain paid", [line("Blue shirt", 40), line("Socks", 10, 2)], 0, 0.08, "paid", None, False),
    ("paid and fulfilled", [line("Jeans", 80)], 0, 0.08, "paid", None, True),
    ("discounted", [line("Jacket", 150)], 30, 0.08, "paid", None, False),
    ("unpaid", [line("Hat", 25)], 0, 0.08, "pending", None, False),
    ("partial refund", [line("Sneakers", 120)], 0, 0.08, "paid", 50, True),
    ("full refund", [line("Backpack", 60)], 0, 0.08, "paid", "full", False),
    ("multi-line big", [line("Watch", 300), line("Strap", 20, 3)], 20, 0.08, "paid", None, True),
    ("no tax", [line("Gift card", 50)], 0, 0.0, "paid", None, False),
]


def create(case):
    name, items, discount, rate, status, refund, fulfilled = case
    subtotal = sum(float(i["price"]) * i["quantity"] for i in items)
    tax = round((subtotal - discount) * rate, 2)
    total = round(subtotal - discount + tax, 2)
    order = {
        "line_items": items, "financial_status": status, "send_receipt": False,
        "send_fulfillment_receipt": False, "inventory_behaviour": "bypass",
        "tags": f"zetsa-seed, {name}", "currency": "USD", "note": f"ZeTSA seed: {name}",
        "email": "seed@example.com",
    }
    if rate:
        order["tax_lines"] = [{"title": "Sales tax", "rate": rate, "price": f"{tax:.2f}"}]
    if discount:
        order["total_discounts"] = f"{discount:.2f}"
        order["discount_codes"] = [{"code": "SEED", "amount": f"{discount:.2f}", "type": "fixed_amount"}]
    if status == "paid":
        order["transactions"] = [{"kind": "sale", "status": "success", "amount": f"{total:.2f}", "gateway": "manual"}]
    if fulfilled:
        order["fulfillment_status"] = "fulfilled"
    print(f"  {name:20} total ${total:.2f} tax ${tax:.2f}" + (f" refund {refund}" if refund else ""))
    if DRY:
        return
    o = call("POST", "/orders.json", {"order": order})["order"]
    if refund:
        txns = call("GET", f"/orders/{o['id']}/transactions.json")["transactions"]
        sale = next(t for t in txns if t["kind"] in ("sale", "capture"))
        amount = total if refund == "full" else float(refund)
        call("POST", f"/orders/{o['id']}/refunds.json", {"refund": {
            "note": f"ZeTSA seed refund: {name}", "notify": False,
            "transactions": [{"parent_id": sale["id"], "amount": f"{amount:.2f}", "kind": "refund", "gateway": "manual"}]}})
    print(f"    created order #{o['order_number']}")


print("Dry run" if DRY else f"Creating {len(CASES)} orders in {BASE.split('/admin')[0]}")
START = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--start=")), 0))  # resume after a failure
for c in CASES[START:]:
    create(c)
