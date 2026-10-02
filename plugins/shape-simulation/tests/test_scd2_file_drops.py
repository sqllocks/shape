"""The SCD2 simulator: a snapshot, then deltas whose versions chain without gaps."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_simulation.scd2_file_drops import DELTA_TYPE, SCD2FileDropConfig, SCD2FileDropSimulator


def run(tmp_path: Path, table: pa.Table, **kw: object) -> tuple[pa.Table, list[pa.Table], object]:
    cfg = SCD2FileDropConfig(
        domain="shop",
        base_path=str(tmp_path),
        business_key_column="order_id",
        scd2_columns=["status", "total", "promo_id", "is_gift"],
        num_delta_days=8,
        daily_change_rate=0.1,
        daily_new_rate=0.05,
        seed=9,
        **kw,  # type: ignore[arg-type]
    )
    res = SCD2FileDropSimulator({"orders": table}, cfg).run()
    return (
        pq.read_table(res.initial_load_path),
        [pq.read_table(p) for p in res.delta_paths if p.suffix == ".parquet"],
        res,
    )


def test_snapshot_marks_every_row_current_from_the_initial_date(
    tmp_path: Path, orders: pa.Table
) -> None:
    snap, _, res = run(tmp_path, orders)
    assert snap.column_names == [*orders.column_names, "valid_from", "valid_to", "is_current"]
    assert set(snap["valid_from"].to_pylist()) == {dt.datetime(2024, 1, 1)}
    assert snap["valid_to"].null_count == snap.num_rows
    assert set(snap["is_current"].to_pylist()) == {True}
    assert res.stats["initial_rows"] == 600 and res.stats["days_simulated"] == 8  # type: ignore[attr-defined]


def test_deltas_have_the_snapshot_columns_in_the_snapshot_order(
    tmp_path: Path, orders: pa.Table
) -> None:
    """Regression (SCD-3): the column order of a delta depended on its first row."""
    snap, deltas, _ = run(tmp_path, orders)
    assert deltas
    for delta in deltas:
        assert delta.column_names == [*snap.column_names, DELTA_TYPE]


def test_versions_chain_without_gaps_and_one_current_version_per_entity(
    tmp_path: Path, orders: pa.Table
) -> None:
    """Regressions (SCD-1, SCD-2): an expired row lost its valid_from and an inserted entity had
    no version columns at all."""
    snap, deltas, res = run(tmp_path, orders)
    versions: dict[object, dict[object, dict[str, object]]] = {}
    for table in [snap, *deltas]:  # a later record of the same version replaces an earlier one
        for row in table.to_pylist():
            versions.setdefault(row["order_id"], {})[row["valid_from"]] = row
    assert len(versions) == 600 + res.stats["total_new"]  # type: ignore[attr-defined]
    for key, by_start in versions.items():
        chain = [by_start[k] for k in sorted(by_start)]
        assert all(v["valid_from"] is not None and v["is_current"] is not None for v in chain), key
        assert [v["is_current"] for v in chain] == [False] * (len(chain) - 1) + [True]
        assert chain[-1]["valid_to"] is None
        for before, after in zip(chain, chain[1:], strict=False):
            assert before["valid_to"] == after["valid_from"]


def test_inserts_and_update_pairs(tmp_path: Path, orders: pa.Table) -> None:
    _, deltas, res = run(tmp_path, orders)
    kinds = [k for d in deltas for k in d[DELTA_TYPE].to_pylist()]
    assert kinds.count("insert") == res.stats["total_new"]  # type: ignore[attr-defined]
    assert kinds.count("update") == 2 * res.stats["total_updates"]  # type: ignore[attr-defined]
    day2 = deltas[1]
    expired = [r for r in day2.to_pylist() if r["is_current"] is False]
    assert {r["valid_to"] for r in expired} == {dt.datetime(2024, 1, 3)}


def test_changes_follow_the_value_type(tmp_path: Path, orders: pa.Table) -> None:
    snap, deltas, _ = run(tmp_path, orders)
    before = {r["order_id"]: r for r in snap.to_pylist()}
    changed = 0
    for row in deltas[0].to_pylist():
        if row[DELTA_TYPE] == "update" and row["is_current"]:
            old = before[row["order_id"]]
            assert row["status"].endswith("_v2") and row["status"][:-3] == old["status"]
            assert row["is_gift"] == (not old["is_gift"])
            assert row["customer_id"] == old["customer_id"]  # not tracked: unchanged
            if old["total"]:
                assert 0.8 <= row["total"] / old["total"] <= 1.2
            changed += 1
    assert changed


def test_an_integer_column_stays_an_integer(tmp_path: Path, orders: pa.Table) -> None:
    """Regression (SCD-4): a changed integer was left with a fraction."""
    _, deltas, _ = run(tmp_path, orders)
    for delta in deltas:
        assert delta["promo_id"].type == pa.int64()
        assert all(isinstance(v, int) or v is None for v in delta["promo_id"].to_pylist())


def test_a_text_business_key_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """Regression (SCD-5): the baseline failed after writing the snapshot."""
    table = pa.table({"id": [f"k{i}" for i in range(20)], "n": list(range(20))})
    cfg = SCD2FileDropConfig(base_path=str(tmp_path), business_key_column="id", scd2_columns=["n"])
    with pytest.raises(ValueError, match="business key column 'id'"):
        SCD2FileDropSimulator({"t": table}, cfg).run()
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_key_and_empty_table_are_refused(tmp_path: Path, orders: pa.Table) -> None:
    cfg = SCD2FileDropConfig(base_path=str(tmp_path), business_key_column="nope")
    with pytest.raises(ValueError, match="no business key column"):
        SCD2FileDropSimulator({"orders": orders}, cfg).run()
    cfg = SCD2FileDropConfig(base_path=str(tmp_path), business_key_column="order_id")
    with pytest.raises(ValueError, match="no rows"):
        SCD2FileDropSimulator({"orders": orders.slice(0, 0)}, cfg).run()


def test_same_seed_same_run_and_formats_and_manifests(tmp_path: Path, orders: pa.Table) -> None:
    a = run(tmp_path / "a", orders, formats=["parquet", "csv", "jsonl"])[2]
    b = run(tmp_path / "b", orders, formats=["parquet", "csv", "jsonl"])[2]
    assert a.stats == b.stats  # type: ignore[attr-defined]
    for x, y in zip(a.delta_paths, b.delta_paths, strict=True):  # type: ignore[attr-defined]
        assert x.read_bytes() == y.read_bytes()
    assert {p.suffix for p in a.delta_paths} == {".parquet", ".csv", ".jsonl"}  # type: ignore[attr-defined]
    assert len(a.manifest_paths) == 1 + 8  # type: ignore[attr-defined]
