"""Small single-table inputs: CSV block size, order of work, lazy datetime check.

None of these changes may alter a profile; they only change how the work is scheduled."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import shape
from shape.profile.reference import column, dtparse, readers, table
from shape.profile.reference.readers import _Col

_MIB = 1 << 20


def test_block_size_is_small_for_small_files_and_unchanged_for_large_ones(tmp_path: Path) -> None:
    small = tmp_path / "small.csv"
    small.write_bytes(b"a\n" * (4 * _MIB // 2))  # 4 MiB
    assert readers._block_size(small, 4) == _MIB  # 4 MiB / 8 is under the 1 MiB floor
    assert readers._block_size(small, 1) == 1 << 24  # a single-threaded read keeps 16 MiB
    mid = tmp_path / "mid.csv"
    mid.write_bytes(b"a\n" * (40 * _MIB // 2))  # 40 MiB -> 5 MiB blocks
    assert readers._block_size(mid, 4) == 5 * _MIB
    assert readers._block_size(tmp_path / "missing.csv", 4) == 1 << 24
    big = 1 << 24
    assert min(big, max(_MIB, -(-(200 * _MIB) // 8))) == big  # a 200 MiB file: unchanged


def _csv_with_late_type_changes(path: Path) -> None:
    """Rows are many blocks long, and some columns only change type in a late block."""
    rows = ["ints_then_float,ints_then_text,all_int,s"]
    for i in range(120_000):
        late_float = f"{i}.5" if i > 110_000 else str(i)
        late_text = f"x{i}" if i > 115_000 else str(i)
        rows.append(f"{late_float},{late_text},{i},v{i % 7}")
    path.write_text("\n".join(rows) + "\n")


def test_block_size_does_not_change_what_a_csv_reads_as(tmp_path: Path) -> None:
    path = tmp_path / "late.csv"
    _csv_with_late_type_changes(path)
    assert path.stat().st_size > 2 * _MIB
    chunked = readers.read_csv(path, 4)  # several blocks
    assert chunked.column(0).num_chunks > 1
    single = readers.read_csv(path, 1)  # one 16 MiB block
    assert single.column(0).num_chunks == 1
    assert chunked.schema == single.schema
    assert chunked.equals(single)
    assert [c.kind for c in readers._csv_cols(chunked)] == [
        c.kind for c in readers._csv_cols(single)
    ]


def test_profile_of_a_multi_block_csv_equals_the_single_threaded_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "late.csv"
    _csv_with_late_type_changes(path)
    many = shape.profile(str(path)).to_dict()
    monkeypatch.setenv("PROFILE_THREADS", "1")
    one = shape.profile(str(path)).to_dict()
    assert many == one


def test_all_parse_datetime_stops_at_the_first_value_that_does_not_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    real = dtparse.parse_mixed

    def spy(value: str) -> object:
        seen.append(value)
        return real(value)

    monkeypatch.setattr(dtparse, "parse_mixed", spy)
    words = pa.array([f"u{i}@x.com" for i in range(5_000)])
    assert column._all_parse_datetime(words) is False
    assert seen == ["u0@x.com"]  # not one per distinct value


@pytest.mark.parametrize("n", [1, 255, 256, 257, 1000])
def test_all_parse_datetime_checks_every_value_across_slices(n: int) -> None:
    # "03/04/2020" is not an ISO form (Arrow's cast refuses it), so dtparse decides, per value
    dates = pa.array([f"{1 + i % 12:02d}/{1 + i % 28:02d}/2020" for i in range(n)])
    assert column._all_parse_datetime(dates) is True
    last_bad = pa.array([*dates.to_pylist(), "not a date"])
    assert column._all_parse_datetime(last_bad) is False


def test_single_table_thread_pool_starts_the_most_expensive_column_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []
    real = column._profile_column

    def spy(c: _Col, row_count: int, *a: object, **k: object) -> object:
        started.append(c.name)
        return real(c, row_count, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr("shape.profile.reference.table._profile_column", spy)
    n = 50
    tbl = pa.table(
        {
            "i1": pa.array(range(n), pa.int64()),
            "i2": pa.array(range(n), pa.int64()),
            "s": pa.array([f"t{i}" for i in range(n)]),
            "f": pa.array([i / 3 for i in range(n)]),
        }
    )
    cols = readers._arrow_cols(tbl)
    table._profile_cols(cols, n, 2)  # two workers over four columns: the thread pool
    assert set(started[:2]) == {"f", "s"}  # float and text first, the integers after
    started.clear()
    table._profile_cols(cols, n, 1)  # serial: column order
    assert started == ["i1", "i2", "s", "f"]
