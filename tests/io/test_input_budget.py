"""#286: an optional input budget, checked against a Parquet file's footer before it is read.

A Parquet file of a few kilobytes can hold 100M constant rows (dictionary/RLE plus zstd) and
take gigabytes once read. ``SHAPE_MAX_INPUT_ROWS`` and ``SHAPE_MAX_INPUT_BYTES`` cap the rows
and the decoded bytes the footer declares; a file over either is refused before any data page
is read. Unset (the default), nothing is checked.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.io import open_source
from shape.profile.engine import profile_table
from shape.profile.reference import sources
from shape.profile.reference.table import profile_dataset, profile_parquet

ROWS = "SHAPE_MAX_INPUT_ROWS"
BYTES = "SHAPE_MAX_INPUT_BYTES"
N = 2_000_000  # constant int64: 16 MB decoded, a few KB on disk


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch):
    monkeypatch.delenv(ROWS, raising=False)
    monkeypatch.delenv(BYTES, raising=False)


@pytest.fixture(scope="module")
def bomb(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("bomb") / "bomb.parquet"
    pq.write_table(pa.table({"x": pa.array([7] * N, pa.int64())}), path, compression="zstd")
    assert path.stat().st_size < 64_000
    return path


@pytest.fixture(scope="module")
def small(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("small") / "small.parquet"
    pq.write_table(pa.table({"x": pa.array(range(1000), pa.int64())}), path)
    return path


@pytest.fixture
def no_data_read(monkeypatch):
    """Fail the test if any Parquet data is read (the footer may be)."""

    def refuse(*args, **kwargs):
        raise AssertionError("Parquet data was read before the budget was checked")

    monkeypatch.setattr(pq, "read_table", refuse)
    monkeypatch.setattr(pq.ParquetFile, "iter_batches", refuse)
    monkeypatch.setattr(pq.ParquetFile, "read", refuse)


def _readers(path: Path) -> dict[str, object]:
    return {
        "shape.profile": lambda: shape.profile(str(path)),
        "engine": lambda: profile_table(str(path)),
        "open_source": lambda: open_source(str(path)),
        "profile_parquet": lambda: profile_parquet(path),
        "profile_dataset": lambda: profile_dataset({"t": str(path)}),
        "load_columns": lambda: sources.load_columns(str(path)),
    }


READERS = list(_readers(Path("x")))


@pytest.mark.parametrize("reader", READERS)
def test_a_file_over_the_row_budget_is_refused_before_it_is_read(
    reader, bomb, monkeypatch, no_data_read
):
    monkeypatch.setenv(ROWS, "1000000")
    with pytest.raises(ValueError, match=rf"bomb\.parquet.*2,000,000 rows.*{ROWS}.*1,000,000"):
        _readers(bomb)[reader]()  # type: ignore[operator]


@pytest.mark.parametrize("reader", READERS)
def test_a_file_over_the_byte_budget_is_refused_before_it_is_read(
    reader, bomb, monkeypatch, no_data_read
):
    # 2M int64 values decode to 16 MB although the file is a few KB
    monkeypatch.setenv(BYTES, "8000000")
    with pytest.raises(ValueError, match=rf"bomb\.parquet.*16,000,000 bytes.*{BYTES}"):
        _readers(bomb)[reader]()  # type: ignore[operator]


@pytest.mark.parametrize("reader", READERS)
def test_a_file_at_the_budget_is_read(reader, small, monkeypatch):
    monkeypatch.setenv(ROWS, "1000")  # exactly the file's rows
    monkeypatch.setenv(BYTES, "8000")  # exactly its decoded int64 bytes
    _readers(small)[reader]()  # type: ignore[operator]


@pytest.mark.parametrize("reader", READERS)
def test_one_row_or_byte_under_is_refused(reader, small, monkeypatch):
    monkeypatch.setenv(ROWS, "999")
    with pytest.raises(ValueError, match=ROWS):
        _readers(small)[reader]()  # type: ignore[operator]
    monkeypatch.delenv(ROWS)
    monkeypatch.setenv(BYTES, "7999")
    with pytest.raises(ValueError, match=BYTES):
        _readers(small)[reader]()  # type: ignore[operator]


def test_several_files_count_together(tmp_path, monkeypatch, no_data_read):
    for i in range(3):
        pq.write_table(
            pa.table({"x": pa.array(range(400), pa.int64())}), tmp_path / f"p{i}.parquet"
        )
    monkeypatch.setenv(ROWS, "1000")  # each file is under, the three together are not
    with pytest.raises(ValueError, match=r"1,200 rows"):
        shape.profile(str(tmp_path))
    with pytest.raises(ValueError, match=r"1,200 rows"):
        open_source(str(tmp_path / "*.parquet"))


def test_without_a_budget_the_footer_is_not_consulted(small, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("footer read for a budget nobody set")

    from shape.io import budget

    monkeypatch.setattr(budget, "parquet_footprint", refuse)
    assert shape.profile(str(small)).to_dict()


@pytest.mark.parametrize("var", [ROWS, BYTES])
@pytest.mark.parametrize("bad", ["abc", "-1", "0", "1.5", "1e6"])
def test_a_budget_that_is_not_a_positive_integer_is_refused_by_name(var, bad, small, monkeypatch):
    monkeypatch.setenv(var, bad)
    with pytest.raises(ValueError, match=rf"{var} must be a positive integer"):
        shape.profile(str(small))


def test_a_blank_budget_means_none(small, monkeypatch):
    monkeypatch.setenv(ROWS, "  ")
    monkeypatch.setenv(BYTES, "")
    assert shape.profile(str(small)).to_dict()


def test_strings_count_their_offsets_and_stored_bytes(tmp_path, monkeypatch):
    path = tmp_path / "s.parquet"
    pq.write_table(pa.table({"s": pa.array(["v"] * 10_000)}), path)
    from shape.io.budget import parquet_footprint

    rows, size = parquet_footprint(path)
    assert rows == 10_000
    assert size >= 4 * 10_000  # at least the int32 offsets the decoded column needs


def test_the_cli_refuses_the_bomb_with_the_budget_named(bomb, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(ROWS, "1000000")
    code = main(["profile", str(bomb), "-o", str(tmp_path / "x.shape")])
    assert code != 0
    assert ROWS in capsys.readouterr().err
    assert not (tmp_path / "x.shape").exists()
