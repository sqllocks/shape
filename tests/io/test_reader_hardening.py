"""AUD-security2: readers refuse hostile inputs instead of crashing or exhausting memory."""

from __future__ import annotations

import bz2
import gzip
import subprocess
import sys
import textwrap

import pytest

DEPTH = 100_000


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a child: a reader that segfaults must not take the test run with it."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=120
    )


@pytest.mark.parametrize(
    ("suffix", "compress"),
    [
        (".jsonl.gz", gzip.compress),
        (".jsonl.bz2", bz2.compress),
    ],
)
def test_compressed_jsonl_is_depth_checked_after_decompression(tmp_path, suffix, compress):
    """#274: the depth guard ran over the compressed bytes, so pyarrow segfaulted."""
    path = tmp_path / f"deep{suffix}"
    path.write_bytes(compress(('{"a":' + "[" * DEPTH + "]" * DEPTH + "}\n").encode()))
    r = _run(
        f"""
        from shape.io.readers import ReaderError, read_table
        try:
            read_table({str(path)!r})
        except ReaderError as exc:
            print("REFUSED", exc)
        """
    )
    assert r.returncode == 0, f"reader crashed (rc={r.returncode}): {r.stderr[-500:]}"
    assert "REFUSED" in r.stdout and "nested deeper" in r.stdout


def test_compressed_jsonl_still_reads(tmp_path):
    path = tmp_path / "ok.jsonl.gz"
    path.write_bytes(gzip.compress(b'{"a": [1, 2], "b": "x"}\n{"a": [3], "b": "y"}\n'))
    from shape.io.readers import read_table

    table = read_table(str(path))
    assert table.num_rows == 2
    assert table.column("b").to_pylist() == ["x", "y"]
