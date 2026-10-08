"""The file-drop simulator: partitions, manifests, anomalies, and the defects it must not have."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shape_simulation.file_drop import FileDropConfig, FileDropSimulator


def cfg(tmp_path: Path, **kw: object) -> FileDropConfig:
    base = {
        "domain": "shop",
        "base_path": str(tmp_path),
        "date_range_start": "2024-01-01",
        "date_range_end": "2024-01-30",
        "lateness_enabled": False,
        "seed": 3,
    }
    return FileDropConfig(**{**base, **kw})  # type: ignore[arg-type]


def rows(paths: list[Path]) -> pa.Table:
    return pa.concat_tables([pq.read_table(p) for p in paths])


def test_rows_land_in_the_partition_of_their_day(tmp_path: Path, orders: pa.Table) -> None:
    res = FileDropSimulator({"orders": orders}, cfg(tmp_path)).run()
    assert res.stats["orders"]["rows_written"] == orders.num_rows
    for path in res.files_written:
        day = path.parent.name.removeprefix("dt=")
        assert path.name == f"shop_orders_{day}_00001.parquet"
        stamps = pq.read_table(path)["ordered_at"].to_pylist()
        assert {s.strftime("%Y-%m-%d") for s in stamps} == {day}
    assert sorted(rows(res.files_written)["order_id"].to_pylist()) == list(range(1, 601))


def test_manifest_and_done_flag_per_partition(tmp_path: Path, orders: pa.Table) -> None:
    res = FileDropSimulator({"orders": orders}, cfg(tmp_path)).run()
    assert len(res.manifest_paths) == len(res.done_flag_paths) == len(res.files_written)
    doc = json.loads(res.manifest_paths[0].read_text())
    assert doc["entity"] == "orders" and doc["domain"] == "shop" and doc["file_count"] == 1
    assert doc["files"] == [res.files_written[0].name] and doc["cadence"] == "daily"
    assert (res.manifest_paths[0].parent / "_done").exists()
    none = FileDropSimulator(
        {"orders": orders}, cfg(tmp_path / "b", manifest_enabled=False, done_flag_enabled=False)
    ).run()
    assert not none.manifest_paths and not none.done_flag_paths


def test_late_rows_are_moved_not_lost_even_when_slots_collide(
    tmp_path: Path, orders: pa.Table
) -> None:
    """Regression (FD-2): late rows of two slots landing in one partition used to replace each
    other's file. Every row must be written exactly once."""
    res = FileDropSimulator(
        {"orders": orders}, cfg(tmp_path, lateness_enabled=True, lateness_probability=0.5)
    ).run()
    assert len(res.files_written) == len(set(res.files_written))
    written = rows(res.files_written)["order_id"].to_pylist()
    assert sorted(written) == list(range(1, 601))
    late = [p for p in res.files_written if p.name.endswith("_00900.parquet")]
    assert late
    # a late file holds rows of earlier days than its partition
    for path in late:
        day = path.parent.name.removeprefix("dt=")
        assert all(
            s.strftime("%Y-%m-%d") < day for s in pq.read_table(path)["ordered_at"].to_pylist()
        )


def test_duplicates_repeat_rows(tmp_path: Path, orders: pa.Table) -> None:
    res = FileDropSimulator(
        {"orders": orders}, cfg(tmp_path, duplicates_enabled=True, duplicate_probability=0.2)
    ).run()
    ids = rows(res.files_written)["order_id"].to_pylist()
    assert len(ids) > 600 and set(ids) == set(range(1, 601))


def test_backfill_and_restatement_files(tmp_path: Path, orders: pa.Table) -> None:
    res = FileDropSimulator(
        {"orders": orders},
        cfg(
            tmp_path,
            backfill_enabled=True,
            max_days_back=5,
            restatement_enabled=True,
            restatement_probability=0.5,
            restatement_max_correction_pct=0.1,
        ),
    ).run()
    assert any(p.name.endswith("_00990.parquet") for p in res.files_written)
    restated = [p for p in res.files_written if p.name.endswith("_00980.parquet")]
    assert restated
    table = pq.read_table(restated[0])
    assert set(table["_restatement"].to_pylist()) == {True}
    assert table["_restated_at"][0].is_valid
    original = rows([p for p in res.files_written if p.name.endswith("_00001.parquet")])
    by_id = dict(zip(original["order_id"].to_pylist(), original["total"].to_pylist(), strict=True))
    for oid, total in zip(table["order_id"].to_pylist(), table["total"].to_pylist(), strict=True):
        assert abs(total / by_id[oid] - 1) <= 0.1 + 1e-9
    # ids and the first column are not corrected
    assert table["customer_id"].type == pa.int64() and table["order_id"].type == pa.int64()


def test_multi_file_splits_a_partition_with_checksums(tmp_path: Path, orders: pa.Table) -> None:
    """Regression (FD-1): the baseline cannot make a multi-file drop; here each partition is
    split into near-equal files and the manifest carries their checksums."""
    res = FileDropSimulator(
        {"orders": orders}, cfg(tmp_path, multi_file_enabled=True, multi_file_chunks=4)
    ).run()
    by_dir: dict[Path, list[Path]] = {}
    for path in res.files_written:
        by_dir.setdefault(path.parent, []).append(path)
    for directory, files in by_dir.items():
        sizes = [pq.read_table(f).num_rows for f in files]
        assert len(files) in (1, 4) and max(sizes) - min(sizes) <= 1
        details = json.loads((directory / "_manifest.json").read_text())["file_details"]
        for f in files:
            entry = next(d for d in details if d["name"] == f.name)
            assert entry["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
            assert entry["size_bytes"] == f.stat().st_size
    assert sorted(rows(res.files_written)["order_id"].to_pylist()) == list(range(1, 601))


def test_table_without_a_time_column_is_dealt_out_round_robin(
    tmp_path: Path, products: pa.Table
) -> None:
    """Regression (FD-3): an integer column named like a date is not the time column."""
    res = FileDropSimulator(
        {"products": products},
        cfg(tmp_path, date_range_end="2024-01-10", lateness_enabled=False),
    ).run()
    sizes = [pq.read_table(p).num_rows for p in sorted(res.files_written)]
    assert sum(sizes) == 100 and len(sizes) == 10 and set(sizes) == {10}


def test_hourly_and_quarter_hour_partitions(tmp_path: Path, orders: pa.Table) -> None:
    res = FileDropSimulator(
        {"orders": orders},
        cfg(tmp_path, cadence="hourly", date_range_end="2024-01-02", formats=["csv", "jsonl"]),
    ).run()
    first = res.files_written[0]
    assert "/hr=" in first.as_posix() and first.name.startswith("shop_orders_2024-01-0")
    assert {p.suffix for p in res.files_written} <= {".csv", ".jsonl"}
    quarter = FileDropSimulator(
        {"orders": orders},
        cfg(tmp_path / "q", cadence="every_15m", date_range_end="2024-01-01"),
    ).run()
    assert all("/m=" in p.as_posix() for p in quarter.files_written)


def test_same_seed_same_drop(tmp_path: Path, orders: pa.Table) -> None:
    kw = {
        "lateness_enabled": True,
        "duplicates_enabled": True,
        "backfill_enabled": True,
        "max_days_back": 4,
    }
    a = FileDropSimulator({"orders": orders}, cfg(tmp_path / "a", **kw)).run()
    b = FileDropSimulator({"orders": orders}, cfg(tmp_path / "b", **kw)).run()
    assert [p.relative_to(tmp_path / "a") for p in a.files_written] == [
        p.relative_to(tmp_path / "b") for p in b.files_written
    ]
    for x, y in zip(a.files_written, b.files_written, strict=True):
        assert pq.read_table(x).equals(pq.read_table(y))


@pytest.mark.parametrize(
    ("kw", "text"),
    [
        ({"cadence": "weekly"}, "cadence"),
        ({"formats": ["json"]}, "Unsupported file format"),
        ({"formats": []}, "at least one"),
        ({"lateness_enabled": True, "max_days_late": 0}, "max_days_late"),
    ],
)
def test_bad_settings_are_refused_when_the_config_is_made(
    tmp_path: Path, kw: dict[str, object], text: str
) -> None:
    with pytest.raises(ValueError, match=text):
        cfg(tmp_path, **kw)


def test_bad_dates_are_refused(tmp_path: Path, orders: pa.Table) -> None:
    with pytest.raises(ValueError, match="before"):
        FileDropSimulator({"o": orders}, cfg(tmp_path, date_range_start="2024-02-01")).run()
    with pytest.raises(ValueError, match="date_range_start"):
        FileDropSimulator({"o": orders}, cfg(tmp_path, date_range_start="")).run()


def test_entities_unknown_names_are_skipped_and_pandas_frames_are_accepted(
    tmp_path: Path, orders: pa.Table
) -> None:
    pd = pytest.importorskip("pandas")
    res = FileDropSimulator(
        {"orders": orders.to_pandas(), "other": orders},
        cfg(tmp_path, entities=["orders", "missing"]),
    ).run()
    assert list(res.stats) == ["orders"]
    assert res.stats["orders"]["rows_written"] == len(pd.DataFrame(orders.to_pandas()))
