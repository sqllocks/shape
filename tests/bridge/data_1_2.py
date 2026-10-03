"""Datasets of the bridge 1.2 tests."""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv

#: Values that appear nowhere in a result unless the data leaked into it.
REAL_NAMES = [f"Zephyrina Quillfeather{i:03d}" for i in range(120)]
REAL_EMAILS = [f"zq{i:03d}.real@secret-corp.example" for i in range(120)]
REAL_AMOUNTS = [round(7000.17 + i * 13.37, 2) for i in range(120)]
REAL_IDS = [5_550_000 + i * 7 for i in range(120)]


def real_tables() -> dict[str, pa.Table]:
    return {
        "customers": pa.table(
            {
                "customer_id": REAL_IDS,
                "full_name": REAL_NAMES,
                "email": REAL_EMAILS,
                "amount": REAL_AMOUNTS,
                "tier": [("gold", "silver", "bronze")[i % 3] for i in range(120)],
            }
        )
    }


def synthetic_tables(seed: int = 3) -> dict[str, pa.Table]:
    rng = random.Random(seed)
    return {
        "customers": pa.table(
            {
                "customer_id": [100 + i for i in range(120)],
                "full_name": [f"Synth Person {rng.randrange(10**6)}" for _ in range(120)],
                "email": [f"s{rng.randrange(10**6)}@synth.example" for _ in range(120)],
                "amount": [round(rng.uniform(6900, 8700), 2) for _ in range(120)],
                "tier": [rng.choice(["gold", "silver", "bronze"]) for _ in range(120)],
            }
        )
    }


def write_tables(folder: Path, tables: dict[str, pa.Table]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        pacsv.write_csv(table, folder / f"{name}.csv")
    return folder


def real_values() -> list[str]:
    """Every value of the real fixture, as the text a response would hold it as."""
    out: list[str] = []
    for table in real_tables().values():
        for column in table.column_names:
            out.extend(str(v) for v in table[column].to_pylist())
    return sorted(set(out))


# ---- registries ----------------------------------------------------------------------------

import datetime as _dt  # noqa: E402
import json as _json  # noqa: E402
from typing import Any  # noqa: E402

START = _dt.date(2026, 3, 1)


def day_table(day: int, *, null_from: int = 99, wide_from: int = 99, rows: int = 400) -> pa.Table:
    """One day of a feed: ``note`` gets nulls from day ``null_from`` and ``amount`` a new range from
    day ``wide_from``. Values are distinctive (``FEED-`` ids and a 4 digit amount range)."""
    rng = random.Random(100 + day)
    note_null = 0.45 if day >= null_from else 0.01
    top = 90000 if day >= wide_from else 9000
    return pa.table(
        {
            "order_id": [f"FEED-{day:02d}-{i:04d}" for i in range(rows)],
            "status": [rng.choice(["new", "paid", "shipped"]) for _ in range(rows)],
            "amount": [round(rng.uniform(1000, top), 2) for _ in range(rows)],
            "note": [
                None if rng.random() < note_null else rng.choice(["a", "b", "c"])
                for _ in range(rows)
            ],
        }
    )


def feed_profile(day: int, **kw: Any) -> Any:
    import shape

    return shape.profile(day_table(day, **kw), name="orders", sketches=True)


def safe_bytes(profile: Any, **cfg: Any) -> bytes:
    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    return to_safe_profile(profile, SafeConfig(**cfg)).to_json().encode("utf-8")


def make_registry(
    root: Path,
    name: str = "orders",
    days: int = 8,
    *,
    form: str = "raw",
    null_from: int = 99,
    wide_from: int = 99,
) -> Path:
    """A registry with one version a day of ``name``: ``form`` is ``raw`` (``.shape``), ``safe``
    (the share-safe JSON) or ``mixed`` (raw, then safe)."""
    import tempfile

    import shape
    from shape.registry import LocalRegistry

    reg = LocalRegistry(root)
    for day in range(days):
        prof = feed_profile(day, null_from=null_from, wide_from=wide_from)
        meta = {"business_date": (START + _dt.timedelta(days=day)).isoformat()}
        as_safe = form == "safe" or (form == "mixed" and day >= days // 2)
        if as_safe:
            reg.commit(name, safe_bytes(prof), {**meta, "profile_form": "safe"})
        else:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "p.shape"
                shape.save(prof, str(path))
                reg.commit(name, path.read_bytes(), {**meta, "profile_form": "raw"}, allow_raw=True)
    return root


def write_contract(path: Path, doc: dict[str, Any] | None = None) -> Path:
    doc = doc or {
        "columns": {
            "note": {"max_null_rate": 0.1},
            "status": {"allowed_values": ["new", "paid", "shipped"]},
            "amount": {"max": 12000},
        },
        "row_count": {"min": 100},
    }
    path.write_text(_json.dumps(doc, indent=2))
    return path
