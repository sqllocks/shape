"""#280: the abfss/OneLake JSONL source runs the JSON depth check before pyarrow's reader."""

from __future__ import annotations

import gzip

import fsspec
import pytest

from shape.builtins.sources import azure


def test_jsonl_source_has_a_depth_guard() -> None:
    fs = fsspec.filesystem("memory")
    n = 200_000
    fs.pipe("/c/deep.jsonl", ('{"a":' + "[" * n + "]" * n + "}\n").encode())
    with pytest.raises(ValueError, match="nested deeper than"):
        azure._Opened(fs, "/c/deep.jsonl", 1000)


def test_jsonl_source_depth_boundary() -> None:
    fs = fsspec.filesystem("memory")
    ok = 120
    fs.pipe("/c/ok.jsonl", ('{"a":' + "[" * ok + "]" * ok + "}\n").encode())
    opened = azure._Opened(fs, "/c/ok.jsonl", 1000)
    try:
        assert sum(b.num_rows for b in opened.batches()) == 1
    finally:
        opened.close()
    over = 200
    fs.pipe("/c/over.jsonl", ('{"a":' + "[" * over + "]" * over + "}\n").encode())
    with pytest.raises(ValueError, match="nested deeper than"):
        azure._Opened(fs, "/c/over.jsonl", 1000)


def test_gzipped_jsonl_source_has_a_depth_guard() -> None:

    fs = fsspec.filesystem("memory")
    n = 5000
    fs.pipe("/c/deep.jsonl.gz", gzip.compress(('{"a":' + "[" * n + "]" * n + "}\n").encode()))
    with pytest.raises(ValueError, match="nested deeper than"):
        azure._Opened(fs, "/c/deep.jsonl.gz", 1000)
