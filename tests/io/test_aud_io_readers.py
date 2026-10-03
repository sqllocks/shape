"""AUD-io: regression tests for the readers (issues filed by the io audit)."""

from __future__ import annotations

import pytest

from shape.io import expand_paths, iter_rows, read_table


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_489_an_existing_file_with_glob_characters_in_its_name_is_read(tmp_path):
    f = _write(tmp_path / "data[1].csv", "a,b\n1,2\n")
    assert expand_paths(f) == [f]
    assert expand_paths(str(f)) == [f]
    assert read_table(str(f)).to_pylist() == [{"a": 1, "b": 2}]
    # a pattern that is not an existing file is still a glob
    _write(tmp_path / "x1.csv", "a\n1\n")
    assert [p.name for p in expand_paths(str(tmp_path / "x[0-9].csv"))] == ["x1.csv"]
    with pytest.raises(FileNotFoundError, match="no files match"):
        expand_paths(str(tmp_path / "nothing[0-9].csv"))


def test_492_a_key_first_seen_after_the_first_batch_is_kept():
    rows = [{"a": 1}, {"a": 2, "b": 3}, {"a": 4, "b": 5}]
    t = read_table(iter(rows), batch_size=1)
    assert t.column_names == ["a", "b"]
    assert t.to_pylist() == [{"a": 1, "b": None}, {"a": 2, "b": 3}, {"a": 4, "b": 5}]
    assert str(t.schema.field("b").type) == "int64"
    assert list(iter_rows(iter(rows), batch_size=1)) == [
        {"a": 1},
        {"a": 2, "b": 3},
        {"a": 4, "b": 5},
    ]
    # a column that is empty in the first batch takes the type of its first values
    t = read_table(iter([{"x": None}, {"x": 1}, {"x": 2}]), batch_size=1)
    assert str(t.schema.field("x").type) == "int64" and t["x"].to_pylist() == [None, 1, 2]
