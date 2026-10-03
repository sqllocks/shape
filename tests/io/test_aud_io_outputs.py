"""AUD-io: regression tests for the output side of shape.io (multi_store, landing, store)."""

from __future__ import annotations

import datetime as dt

import pytest

from shape.io.multi_store import MultiStoreWriter


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name

    def write_all(self, tables, **kwargs):
        return self.name


def test_501_labels_stay_unique_when_a_writer_is_named_like_a_generated_label():
    writers = [_Named("a"), _Named("a#2"), _Named("a")]
    result = MultiStoreWriter(writers).write_all({})
    assert len(result.results) == 3
    assert sorted(result.results.values()) == ["a", "a", "a#2"]
    assert MultiStoreWriter([_Named("x"), _Named("x")]).write_all({}).results == {
        "x": "x",
        "x#2": "x",
    }


def test_502_render_path_refuses_an_extension_or_table_that_leaves_the_directory():
    from shape.io.landing import render_path

    for ext in ("..", ".", "/../../x", "a/b", "a\\b"):
        with pytest.raises(ValueError, match="extension"):
            render_path("{table}/{ext}", "t", ext, None)
    with pytest.raises(ValueError, match="cannot be used in a path"):
        render_path("{table}.{ext}", "C:evil", "csv", None)
    assert render_path("{table}.{ext}", "t", "csv.gz", None) == "t.csv.gz"


def test_502_hhmmss_is_utc_for_an_aware_time():
    from shape.io.landing import render_path

    plus5 = dt.timezone(dt.timedelta(hours=5))
    now = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=plus5)
    assert render_path("{table}_{hhmmss}.{ext}", "t", "csv", None, now=now) == "t_070000.csv"
    naive = dt.datetime(2026, 1, 1, 12, 0, 0)
    assert render_path("{table}_{hhmmss}.{ext}", "t", "csv", None, now=naive) == "t_120000.csv"


def test_503_a_store_file_needs_a_name_below_the_root(tmp_path):
    import fsspec

    from shape.io.store import FsspecStore, LocalStore

    stores = [LocalStore(tmp_path / "r"), FsspecStore(fsspec.filesystem("memory"), "/aud503")]
    for store in stores:
        for rel in ("", ".", "./", "a/.."):
            with pytest.raises(ValueError):
                store.create(rel)


def test_503_a_long_but_valid_file_name_is_written(tmp_path):
    from shape.io.store import LocalStore

    store = LocalStore(tmp_path)
    name = "x" * 250 + ".csv"  # 254 bytes: a valid file name
    store.put_bytes(f"d/{name}", b"1")
    assert (tmp_path / "d" / name).read_bytes() == b"1"
    assert store.names("d") == [name]


def test_505_landing_doc_lists_every_token_and_the_cloud_target():
    from pathlib import Path

    from shape.io.landing import TOKENS

    text = (Path(__file__).resolve().parents[2] / "docs" / "LANDING.md").read_text("utf-8")
    assert [t for t in TOKENS if "{" + t + "}" not in text] == []
    assert "--to abfss://" in text
