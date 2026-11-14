"""Shape-generated fixtures; fixed seeds, authored evidence, no source datasets."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as csv

import shape


def categorical(**weights: float) -> dict:
    return {"kind": "text", "topk": list(map(list, weights.items()))}


def generate(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    evidence = {
        "columns": {
            "order_code": categorical(ORD_100=0.5, ORD_200=0.5),
            "status": categorical(shipped=0.8, pending=0.2),
            "order_total": {
                "kind": "numeric",
                "mean": 100,
                "variance_population": 400,
                "min": 40,
                "max": 160,
            },
            "order_date": categorical(**{"2026-12-04": 1.0}),
        }
    }
    cols, _ = shape.generate(evidence, n=240, seed=1204)
    cols["order_code"] = [x.replace("_", "-") for x in cols["order_code"]]
    cols["order_date"] = [dt.date.fromisoformat(x) for x in cols["order_date"]]
    day1 = pa.table(cols)
    csv.write_csv(day1, out / "orders_day1.csv")
    # Explicit fault injection after generation: dates, invalid code, new status, mean shift.
    day2 = day1.to_pydict()
    day2["order_date"] = [dt.date(2026, 12, 5)] * 240
    day2["order_date"][:12] = [dt.date(2026, 11, 25)] * 12
    day2["order_code"][0] = "BROKEN"
    day2["status"][:24] = ["returned"] * 24
    day2["order_total"] = [x * 1.4 for x in day2["order_total"]]
    csv.write_csv(pa.table(day2), out / "orders_day2.csv")
    customers, _ = shape.generate(
        {
            "columns": {
                "segment": categorical(consumer=0.7, business=0.3),
                "region": categorical(east=0.5, west=0.5),
            }
        },
        n=60,
        seed=1206,
    )
    csv.write_csv(pa.table(customers), out / "customers.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent)
    generate(parser.parse_args().out)
