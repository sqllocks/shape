"""Generate the Shape demo data: retail (day 1 + day 2) and the D2 profiling dataset.

    python demo/make_data.py [--out DIR] [--scale medium] [--seed 42]

Needs only numpy, pyarrow and Shape itself. Nothing is read from outside this repository.

Where each column comes from:

* **Shape's generator.** Every numeric measure (``unit_price``, ``shipping_cost``,
  ``loyalty_points``, ``refund_amount``) and every weighted category (``segment``,
  ``product_status``, ``channel``, ``payment_method``, ``status`` of orders and returns,
  return ``reason``) is produced by ``shape.generation.generate_from_shape`` from a hand-written
  Shape spec per table (the ``*_SHAPE`` dicts below). Foreign keys (``orders.customer_id``,
  ``products.category_id``, ``returns.order_id``, ``returns.product_id``) use
  ``shape.generation.relational.generate_fk_indices``.
* **numpy, seeded, in this file.** Shape's generator has no person-name, email, SKU, date or
  heavy-tailed-amount primitive, so ``first_name``, ``last_name``, ``email``, ``city``,
  ``state``, ``product_name``, ``sku``, all dates and ``orders.order_total`` are made here.
  Keys (``*_id``) are sequences. ``orders.order_total`` is log-normal; one bulk order is set to
  5135.63 so the day-1 maximum (and so the day-2 maximum, 7189.882) matches ``demo/TALK.md``.
  ``customers.email`` has exactly 4.97% nulls for the same reason.
* **``demo/d2_table.py``.** The 20-column D2 profiling table.

Output layout (uploaded by the owner to the lakehouse ``Files/demo/`` folder)::

    day1/customers.parquet  orders.parquet  products.parquet  returns.parquet  d2.parquet
    day2/customers.parquet  orders.parquet  products.parquet   (the drifted tables only)

The drift applied to day 2 is documented in ``demo/DRIFT.md`` and is deterministic for a seed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))

from d2_table import d2_table  # noqa: E402

from shape.generation import generate_from_shape  # noqa: E402
from shape.generation.relational import generate_fk_indices  # noqa: E402

DRIFTED = ("customers", "orders", "products")

# Row counts per scale: customers, products, orders, returns. Medium is the talk size.
SCALES = {
    "small": (5_000, 500, 50_000, 8_500),
    "medium": (50_000, 5_000, 500_000, 85_000),
    "large": (500_000, 50_000, 5_000_000, 850_000),
}
CATEGORIES = 50

# Day-1 parameters that the talk notes quote (keep in sync with demo/DRIFT.md, demo/TALK.md).
EMAIL_NULL_RATE_DAY1 = 0.0497
BULK_ORDER_TOTAL = 5135.63

# Day-2 drift parameters (keep in sync with demo/DRIFT.md).
EMAIL_NULL_RATE_DAY2 = 0.20
LOST_STATUS_FRACTION = 0.02
ORDER_TOTAL_FACTOR = 1.40
SKU_DUPLICATES = 50


def _num(mean: float, sd: float, lo: float, hi: float) -> dict:
    return {
        "kind": "numeric",
        "mean": mean,
        "variance_population": sd * sd,
        "min": lo,
        "max": hi,
    }


def _cat(**weights: float) -> dict:
    return {"kind": "text", "topk": [[k, w] for k, w in weights.items()]}


# Hand-written Shape specs: the marginals Shape's generator reproduces.
CUSTOMERS_SHAPE = {
    "columns": {
        "segment": _cat(consumer=0.62, small_business=0.25, enterprise=0.13),
        "loyalty_points": _num(1200, 900, 0, 10_000),
    }
}
PRODUCTS_SHAPE = {
    "columns": {
        "unit_price": _num(48, 35, 0.99, 499),
        "product_status": _cat(active=0.88, discontinued=0.12),
    }
}
ORDERS_SHAPE = {
    "columns": {
        "status": _cat(
            completed=0.62, shipped=0.12, cancelled=0.10, processing=0.08, returned=0.08
        ),
        "channel": _cat(web=0.55, mobile=0.30, store=0.15),
        "payment_method": _cat(card=0.64, paypal=0.21, gift_card=0.09, invoice=0.06),
        "shipping_cost": _num(6.5, 3.0, 0, 40),
    }
}
RETURNS_SHAPE = {
    "columns": {
        "reason": _cat(defective=0.30, wrong_size=0.27, not_as_described=0.20, changed_mind=0.23),
        "status": _cat(refunded=0.78, pending=0.15, rejected=0.07),
        "refund_amount": _num(52, 40, 1, 500),
    }
}

FIRST_NAMES = [
    "Olivia", "Liam", "Emma", "Noah", "Ava", "Elijah", "Sophia", "Lucas", "Isabella", "Mason",
    "Mia", "Logan", "Amelia", "Ethan", "Harper", "James", "Evelyn", "Aiden", "Abigail", "Jack",
    "Ella", "Owen", "Scarlett", "Henry", "Grace", "Leo", "Chloe", "Samuel", "Nora", "Daniel",
]  # fmt: skip
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Martinez",
    "Lopez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee",
    "Perez", "Thompson", "White", "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young",
    "Allen", "King", "Wright",
]  # fmt: skip
EMAIL_DOMAINS = ["example.com", "mail.org", "corp.net", "test.io", "inbox.dev", "post.co"]
CITIES = [
    ("Seattle", "WA"), ("Austin", "TX"), ("Denver", "CO"), ("Chicago", "IL"), ("Boston", "MA"),
    ("Atlanta", "GA"), ("Portland", "OR"), ("Phoenix", "AZ"), ("Miami", "FL"), ("Nashville", "TN"),
    ("Columbus", "OH"), ("Raleigh", "NC"), ("Madison", "WI"), ("Tucson", "AZ"), ("Omaha", "NE"),
]  # fmt: skip
ADJECTIVES = ["Classic", "Modern", "Compact", "Deluxe", "Essential", "Premium", "Everyday", "Pro"]
NOUNS = [
    "Backpack", "Lamp", "Kettle", "Headphones", "Notebook", "Bottle", "Jacket", "Speaker",
    "Blender", "Wallet", "Keyboard", "Mug", "Sneakers", "Watch", "Tripod", "Planner",
]  # fmt: skip
SKU_PREFIXES = ["APP", "ELC", "HOM", "KIT", "OUT", "SPT", "STA", "TRV", "BTY", "TOY"]


def _take(pool: list[str], idx: np.ndarray) -> pa.Array:
    return pc.take(pa.array(pool), pa.array(idx))


def _join(*parts: pa.Array | str) -> pa.Array:
    parts_ = [p if isinstance(p, (pa.Array, pa.ChunkedArray)) else pa.scalar(p) for p in parts]
    return pc.binary_join_element_wise(*parts_, "")


def _istr(a: np.ndarray, width: int = 0) -> pa.Array:
    s = pc.cast(pa.array(np.asarray(a, dtype=np.int64)), pa.string())
    return pc.utf8_lpad(s, width=width, padding="0") if width else s


def _dates(rng: np.random.Generator, n: int, start: str, days: int) -> pa.Array:
    d = np.datetime64(start, "D") + rng.integers(0, days, n).astype("timedelta64[D]")
    return pa.array(d.astype("datetime64[D]"), type=pa.date32())


def _shape(spec: dict, n: int, seed: int) -> dict[str, pa.Array]:
    """Columns from Shape's generator, typed for Parquet."""
    cols, _ = generate_from_shape(spec, n, seed)
    out: dict[str, pa.Array] = {}
    for name, col in cols.items():
        if spec["columns"][name]["kind"] == "numeric":
            out[name] = pa.array(np.round(np.asarray(col, dtype=np.float64), 2))
        else:
            out[name] = pa.array(col, type=pa.string())
    return out


def _customers(n: int, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    ids = np.arange(1, n + 1, dtype=np.int64)
    first = rng.integers(0, len(FIRST_NAMES), n)
    last = rng.integers(0, len(LAST_NAMES), n)
    email = _join(
        pc.utf8_lower(_take(FIRST_NAMES, first)),
        ".",
        pc.utf8_lower(_take(LAST_NAMES, last)),
        _istr(ids),
        "@",
        _take(EMAIL_DOMAINS, rng.integers(0, len(EMAIL_DOMAINS), n)),
    )
    null_rows = rng.choice(n, size=round(EMAIL_NULL_RATE_DAY1 * n), replace=False)
    mask = np.zeros(n, dtype=bool)
    mask[null_rows] = True
    email = pc.if_else(pa.array(mask), pa.scalar(None, pa.string()), email)
    city = rng.integers(0, len(CITIES), n)
    sh = _shape(CUSTOMERS_SHAPE, n, seed + 1)
    return pa.table(
        {
            "customer_id": pa.array(ids),
            "first_name": _take(FIRST_NAMES, first),
            "last_name": _take(LAST_NAMES, last),
            "email": email,
            "city": _take([c for c, _ in CITIES], city),
            "state": _take([s for _, s in CITIES], city),
            "signup_date": _dates(rng, n, "2019-01-01", 2000),
            "segment": sh["segment"],
            "loyalty_points": pa.array(np.round(sh["loyalty_points"].to_numpy()).astype(np.int64)),
        }
    )


def _products(n: int, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    ids = np.arange(1, n + 1, dtype=np.int64)
    category = generate_fk_indices(CATEGORIES, n, seed + 2) + 1
    sh = _shape(PRODUCTS_SHAPE, n, seed + 1)
    price = sh["unit_price"].to_numpy()
    name = _join(
        _take(ADJECTIVES, rng.integers(0, len(ADJECTIVES), n)),
        " ",
        _take(NOUNS, rng.integers(0, len(NOUNS), n)),
    )
    # SKU: a category prefix plus the zero-padded product id, so it is unique per product.
    sku = _join(_take(SKU_PREFIXES, (category - 1) % len(SKU_PREFIXES)), "-", _istr(ids, 6))
    return pa.table(
        {
            "product_id": pa.array(ids),
            "sku": sku,
            "product_name": name,
            "category_id": pa.array(category.astype(np.int64)),
            "unit_price": pa.array(price),
            "cost": pa.array(np.round(price * 0.55, 2)),
            "product_status": sh["product_status"],
        }
    )


def _orders(n: int, customer_ids: np.ndarray, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    # Log-normal basket value (mean about 110), capped just under the bulk order below.
    total = np.round(np.minimum(rng.lognormal(4.3, 0.85, n), 4999.99), 2)
    total[rng.integers(0, n)] = BULK_ORDER_TOTAL
    sh = _shape(ORDERS_SHAPE, n, seed + 1)
    fk = generate_fk_indices(len(customer_ids), n, seed + 2)
    return pa.table(
        {
            "order_id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "customer_id": pa.array(customer_ids[fk]),
            "order_date": _dates(rng, n, "2023-01-01", 730),
            "status": sh["status"],
            "channel": sh["channel"],
            "payment_method": sh["payment_method"],
            "shipping_cost": sh["shipping_cost"],
            "order_total": pa.array(total),
        }
    )


def _returns(n: int, order_ids: np.ndarray, product_ids: np.ndarray, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    sh = _shape(RETURNS_SHAPE, n, seed + 1)
    return pa.table(
        {
            "return_id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "order_id": pa.array(order_ids[generate_fk_indices(len(order_ids), n, seed + 2)]),
            "product_id": pa.array(product_ids[generate_fk_indices(len(product_ids), n, seed + 3)]),
            "return_date": _dates(rng, n, "2023-01-15", 730),
            "reason": sh["reason"],
            "status": sh["status"],
            "refund_amount": sh["refund_amount"],
        }
    )


def build_day1(
    scale: str = "medium", seed: int = 42, d2_rows: int = 1_000_000
) -> dict[str, pa.Table]:
    """Day-1 tables keyed by file name (without ``.parquet``)."""
    n_cust, n_prod, n_ord, n_ret = SCALES[scale]
    customers = _customers(n_cust, seed)
    products = _products(n_prod, seed + 100)
    orders = _orders(n_ord, customers["customer_id"].to_numpy(), seed + 200)
    returns = _returns(
        n_ret, orders["order_id"].to_numpy(), products["product_id"].to_numpy(), seed + 300
    )
    return {
        "customers": customers,
        "orders": orders,
        "products": products,
        "returns": returns,
        "d2": d2_table(d2_rows),
    }


def apply_day2(day1: dict[str, pa.Table], seed: int = 42) -> dict[str, pa.Table]:
    """The four documented drifts. Returns only the drifted tables."""
    rng = np.random.default_rng(seed + 1)
    out: dict[str, pa.Table] = {}

    # 1. customers.email null rate ~5% -> 20% (exactly 20%, extra nulls at random rows)
    cust = day1["customers"]
    n = cust.num_rows
    mask = np.asarray(pc.is_null(cust["email"]).to_numpy(zero_copy_only=False), dtype=bool)
    extra = round(EMAIL_NULL_RATE_DAY2 * n) - int(mask.sum())
    candidates = np.flatnonzero(~mask)
    mask[rng.choice(candidates, size=extra, replace=False)] = True
    email = pc.if_else(pa.array(mask), pa.scalar(None, pa.string()), cust["email"])
    out["customers"] = cust.set_column(cust.schema.get_field_index("email"), "email", email)

    # 2. orders.status gains 'lost' (exactly 2% of rows) and 3. orders.order_total x1.40
    orders = day1["orders"]
    n = orders.num_rows
    lost_mask = np.zeros(n, dtype=bool)
    lost_mask[rng.choice(n, size=round(LOST_STATUS_FRACTION * n), replace=False)] = True
    status = pc.if_else(pa.array(lost_mask), pa.scalar("lost"), orders["status"])
    total = pc.multiply(orders["order_total"], ORDER_TOTAL_FACTOR)
    orders = orders.set_column(orders.schema.get_field_index("status"), "status", status)
    out["orders"] = orders.set_column(
        orders.schema.get_field_index("order_total"), "order_total", total
    )

    # 4. products.sku loses uniqueness: 50 new products reuse the sku of existing ones.
    #    New product_ids keep product_id itself unique, so only sku loses uniqueness.
    prod = day1["products"]
    src = rng.choice(prod.num_rows, size=SKU_DUPLICATES, replace=False)
    dup = prod.take(pa.array(src))
    first_new = pc.max(prod["product_id"]).as_py() + 1
    ids = pa.array(np.arange(first_new, first_new + SKU_DUPLICATES, dtype=np.int64))
    dup = dup.set_column(dup.schema.get_field_index("product_id"), "product_id", ids)
    out["products"] = pa.concat_tables([prod, dup])
    return out


def write_all(
    day1: dict[str, pa.Table], day2: dict[str, pa.Table], out_dir: Path
) -> list[tuple[Path, int]]:
    written: list[tuple[Path, int]] = []
    for day, tables in (("day1", day1), ("day2", day2)):
        d = out_dir / day
        d.mkdir(parents=True, exist_ok=True)
        for name, t in tables.items():
            p = d / f"{name}.parquet"
            pq.write_table(t, p, compression="snappy")
            written.append((p, t.num_rows))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_out = Path(os.environ.get("BENCH_DATA_DIR", Path.home() / "bench-data")) / "demo"
    ap.add_argument("--out", type=Path, default=default_out)
    ap.add_argument("--scale", default="medium", choices=sorted(SCALES))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    day1 = build_day1(a.scale, a.seed)
    day2 = apply_day2(day1, a.seed)
    files = write_all(day1, day2, a.out)
    total = 0
    for p, rows in files:
        size = p.stat().st_size
        total += size
        print(f"{p.relative_to(a.out)!s:28s} {rows:>10,} rows {size / 1e6:>8.2f} MB")
    print(f"total {total / 1e6:.1f} MB in {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
