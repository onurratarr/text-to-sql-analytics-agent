"""Deterministic synthetic raw data for a fictional online outdoor-gear retailer, "Juniper Supply Co.".

The raw layer is intentionally a little messy (inconsistent casing, status spellings, cents, duplicate payment
loads) so the staging models have real work to do. Nothing here is derived from any real company's data.
"""
from __future__ import annotations

import csv
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

SEED = 7
FIRST = ["Ada", "Ben", "Chloe", "Dev", "Elif", "Farah", "Gus", "Hana", "Ivan", "Jade", "Kai", "Lena", "Milo",
         "Nora", "Omar", "Pia", "Quinn", "Rosa", "Sami", "Tara", "Uma", "Vik", "Wren", "Yara", "Zeke"]
LAST = ["Arden", "Brook", "Cole", "Dune", "Ellis", "Frost", "Gale", "Hart", "Isles", "Jory", "Kemp", "Lowe",
        "Marsh", "North", "Oakes", "Pike", "Reed", "Stone", "Thorn", "Vale", "West", "York"]
COUNTRIES = ["US", "US", "US", "CA", "CA", "GB", "DE", "DE", "FR", "NL", "TR", "AU"]
SEGMENTS = ["consumer", "consumer", "consumer", "pro", "business"]
CATALOG = {
    "tents": [("Ridge 2P Tent", 249.0), ("Summit 4P Tent", 429.0), ("Ultralight Bivy", 179.0)],
    "sleeping": [("Down Bag -5C", 289.0), ("Synthetic Bag 5C", 129.0), ("Foam Pad", 39.0), ("Air Pad", 99.0)],
    "packs": [("Daypack 22L", 79.0), ("Trek Pack 55L", 219.0), ("Hydration Vest", 119.0)],
    "apparel": [("Rain Shell", 189.0), ("Fleece Mid", 89.0), ("Merino Tee", 59.0), ("Trail Pants", 95.0)],
    "footwear": [("Trail Runner", 139.0), ("Hiking Boot", 199.0), ("Camp Sandal", 49.0)],
    "cooking": [("Canister Stove", 69.0), ("Titanium Pot", 45.0), ("Water Filter", 49.0), ("Spork Set", 12.0)],
}
STATUS_SPELLINGS = {
    "completed": ["completed", "Completed", "COMPLETE", "complete "],
    "shipped": ["shipped", "Shipped"],
    "cancelled": ["cancelled", "canceled", "CANCELLED"],
    "returned": ["returned", "Returned"],
    "pending": ["pending"],
}
STATUS_WEIGHTS = [("completed", 70), ("shipped", 10), ("cancelled", 9), ("returned", 6), ("pending", 5)]
START, END = date(2025, 1, 1), date(2026, 6, 30)


def _pick_status(r):
    x, acc = r.uniform(0, 100), 0
    for s, w in STATUS_WEIGHTS:
        acc += w
        if x <= acc:
            return s
    return "completed"


def generate(n_customers=300, n_orders=2500, seed=SEED):
    r = random.Random(seed)
    products, pid = [], 100
    for cat, items in CATALOG.items():
        for name, price in items:
            pid += 1
            products.append((pid, name, cat, int(price * 100), pid % 17 != 0))
    customers = []
    span = (END - START).days
    for cid in range(1, n_customers + 1):
        fn, ln = r.choice(FIRST), r.choice(LAST)
        email = f"{fn}.{ln}{cid}@example.com"
        email = email.upper() if cid % 11 == 0 else (f"  {email} " if cid % 13 == 0 else email)
        country = r.choice(COUNTRIES)
        country = None if cid % 37 == 0 else (country.lower() if cid % 9 == 0 else country)
        signup = START + timedelta(days=int(r.betavariate(1.2, 1.6) * span))
        customers.append((cid, f" {fn} {ln}" if cid % 15 == 0 else f"{fn} {ln}", email, country,
                          signup.isoformat(), r.choice(SEGMENTS)))
    orders, items, payments = [], [], []
    pay_id = 9000
    for oid in range(10001, 10001 + n_orders):
        c = r.choice(customers)
        signup = date.fromisoformat(c[4])
        days_left = (END - signup).days
        od = signup + timedelta(days=r.randint(0, max(days_left, 0)))
        ts = datetime(od.year, od.month, od.day, r.randint(6, 23), r.randint(0, 59), r.randint(0, 59))
        status = _pick_status(r)
        channel = r.choice(["web", "web", "web", "mobile", "mobile", "Web", "marketplace"])
        orders.append((oid, c[0], ts.isoformat(sep=" "), r.choice(STATUS_SPELLINGS[status]), channel))
        net_cents = 0
        for p in r.sample(products, r.choice([1, 1, 1, 2, 2, 3])):
            qty = r.choice([1, 1, 1, 2, 3])
            disc = r.choice([0, 0, 0, 0, 10, 15, 20])
            items.append((oid, p[0], qty, p[3], disc))
            net_cents += round(qty * p[3] * (100 - disc) / 100)
        if status in ("completed", "shipped", "returned"):
            pay_id += 1
            paid = ts + timedelta(minutes=r.randint(1, 30))
            method = r.choice(["card", "card", "card", "paypal", "gift_card"])
            payments.append((pay_id, oid, method, net_cents, paid.isoformat(sep=" "), paid.isoformat(sep=" ")))
            if pay_id % 25 == 0:  # the same payment loaded twice by the ingestion job
                payments.append((pay_id, oid, method, net_cents, paid.isoformat(sep=" "),
                                 (paid + timedelta(hours=6)).isoformat(sep=" ")))
            if status == "returned":
                pay_id += 1
                ref = paid + timedelta(days=r.randint(3, 20))
                payments.append((pay_id, oid, "refund", -net_cents, ref.isoformat(sep=" "), ref.isoformat(sep=" ")))
    return {"customers": customers, "products": products, "orders": orders, "order_items": items,
            "payments": payments}


DDL = {
    "raw_customers": "id INTEGER, full_name VARCHAR, email VARCHAR, country VARCHAR, signup_date VARCHAR, segment VARCHAR",
    "raw_products": "id INTEGER, name VARCHAR, category VARCHAR, unit_price_cents INTEGER, is_active BOOLEAN",
    "raw_orders": "id INTEGER, customer_id INTEGER, ordered_at VARCHAR, status VARCHAR, channel VARCHAR",
    "raw_order_items": "order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price_cents INTEGER, discount_pct INTEGER",
    "raw_payments": "id INTEGER, order_id INTEGER, method VARCHAR, amount_cents INTEGER, paid_at VARCHAR, _loaded_at VARCHAR",
}


def load(con: duckdb.DuckDBPyConnection, data=None) -> dict:
    data = data or generate()
    counts = {}
    for table, cols in DDL.items():
        rows = data[table.removeprefix("raw_")]
        con.execute(f"CREATE OR REPLACE TABLE {table} ({cols})")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", delete=False) as f:
            csv.writer(f).writerows(rows)  # bulk load via CSV; None becomes an empty (NULL) field
        con.execute(f"COPY {table} FROM '{f.name}' (HEADER false)")
        Path(f.name).unlink()
        counts[table] = len(rows)
    return counts
