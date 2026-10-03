"""Shared data for the joint-distribution tests (#47): the issue's city and ZIP example, built from
the GeoNames US postal reference the retail domain ships."""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

REFERENCE = (
    Path(__file__).resolve().parents[2]
    / "plugins/shape-domains/src/shape_domains/data/retail/reference/us_zip_locations.arrow"
)


def read_reference() -> pa.Table:
    with pa.memory_map(str(REFERENCE)) as f:
        try:
            return ipc.open_file(f).read_all()
        except pa.ArrowInvalid:
            return ipc.open_stream(f).read_all()


@pytest.fixture(scope="session")
def reference() -> pa.Table:
    return read_reference()


def _write(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["city", "state", "zip"])
        w.writerows(rows)


@pytest.fixture(scope="session")
def city_zip(tmp_path_factory: pytest.TempPathFactory, reference: pa.Table) -> dict[str, Path]:
    """4,000 real (city, state, ZIP) rows, and a copy where 8% of the ZIPs are ``00000`` and 5% are
    the real ZIP of a different place (13% of rows wrong)."""
    rng = random.Random(47)
    rows = reference.to_pylist()
    good = [(r["city"], r["state"], r["zip"]) for r in rng.sample(rows, 4000)]
    bad = [list(r) for r in good]
    order = list(range(len(good)))
    rng.shuffle(order)
    for i in order[:320]:
        bad[i][2] = "00000"
    for i in order[320:520]:
        while True:
            other = rng.choice(good)
            if (other[0], other[1]) != (bad[i][0], bad[i][1]):
                bad[i][2] = other[2]
                break
    out = tmp_path_factory.mktemp("cityzip")
    _write(out / "good.csv", good)
    _write(out / "bad.csv", [(a, b, c) for a, b, c in bad])
    return {"good": out / "good.csv", "bad": out / "bad.csv"}
