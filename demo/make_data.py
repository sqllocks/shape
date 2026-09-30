"""Generate the Shape demo data: retail (day 1 + day 2) and the D2 profiling dataset.

    source scripts/env.sh && python demo/make_data.py [--out DIR] [--scale medium]

Needs the pinned Spindle checkout (plan section 1.2, ``$SPINDLE_ROOT``): the committed retail
port reads Spindle's schema and reference data at run time. The checkout is only read.

Output layout (uploaded by the owner to the lakehouse ``Files/demo/`` folder)::

    day1/customers.parquet  orders.parquet  products.parquet  <the other retail tables>
    day1/d2.parquet
    day2/customers.parquet  orders.parquet  products.parquet   (the drifted tables only)

The drift applied to day 2 is documented in ``demo/DRIFT.md`` and is deterministic.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]

# Retail table name -> file/table name. "order" and "return" are SQL keywords.
FILE_NAMES = {
    "customer": "customers",
    "order": "orders",
    "product": "products",
    "return": "returns",
}
DRIFTED = ("customers", "orders", "products")

# Day-2 drift parameters (keep in sync with demo/DRIFT.md).
EMAIL_NULL_RATE_DAY2 = 0.20
LOST_STATUS_FRACTION = 0.02
ORDER_TOTAL_FACTOR = 1.40
SKU_DUPLICATES = 50


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def default_spindle_root() -> Path:
    return Path(os.environ.get("SPINDLE_ROOT", Path.home() / "spindle"))


def build_day1(
    spindle_root: Path, scale: str = "medium", seed: int = 42, d2_rows: int = 1_000_000
) -> dict[str, pa.Table]:
    """Day-1 tables keyed by file name (without ``.parquet``)."""
    retail = _load("_demo_retail_port", REPO / "benchmarks" / "retail_1to1" / "port.py")
    raw = retail.generate(scale, seed, str(spindle_root))
    tables = {FILE_NAMES.get(k, k): v for k, v in raw.items()}
    # The retail schema has no product SKU; the demo derives one (unique, stable per product).
    products = tables["products"]
    sku = pc.binary_join_element_wise(
        pa.scalar("SKU-"),
        pc.utf8_lpad(pc.cast(products["product_id"], pa.string()), width=6, padding="0"),
        "",
    )
    tables["products"] = products.append_column("sku", sku)
    datasets = _load("_demo_profile_datasets", REPO / "benchmarks" / "profile_1to1" / "datasets.py")
    tables["d2"] = datasets._d2_table(d2_rows)
    return tables


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

    # 2. orders.status gains 'lost' (2% of rows) and 3. orders.order_total mean +40%
    orders = day1["orders"]
    n = orders.num_rows
    lost = pa.array(rng.random(n) < LOST_STATUS_FRACTION)
    status = pc.if_else(lost, pa.scalar("lost"), orders["status"])
    total = pc.multiply(orders["order_total"], ORDER_TOTAL_FACTOR)
    orders = orders.set_column(orders.schema.get_field_index("status"), "status", status)
    out["orders"] = orders.set_column(
        orders.schema.get_field_index("order_total"), "order_total", total
    )

    # 4. products.sku loses uniqueness: 50 new products reuse the sku of existing ones.
    #    New product_ids keep product_id itself unique, so only sku changes.
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
    ap.add_argument("--scale", default="medium")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--spindle-root", type=Path, default=default_spindle_root())
    a = ap.parse_args(argv)
    if not (a.spindle_root / "sqllocks_spindle").is_dir():
        print(
            f"Spindle checkout not found at {a.spindle_root} (see plan section 1.2)",
            file=sys.stderr,
        )
        return 2
    day1 = build_day1(a.spindle_root, a.scale, a.seed)
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
