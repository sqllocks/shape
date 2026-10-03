"""W1-17: ``_shape_provenance.json`` (format, compatibility, hashing, verification)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.io import provenance as prov

#: A version-1 sidecar, frozen: this Shape must keep reading it (the compatibility test).
FROZEN_V1 = {
    "format": "shape-provenance",
    "version": 1,
    "shape_version": "0.9.0",
    "seed": 42,
    "domain": "retail",
    "scale": "small",
    "spec_hash": None,
    "files": [{"path": "orders.csv", "sha256": "0" * 64, "rows": 3}],
}


def _csv(folder: Path, name: str = "orders.csv", text: str = "a,b\n1,2\n3,4\n") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_write_records_format_version_seed_and_files(tmp_path: Path) -> None:
    f = _csv(tmp_path)
    side = prov.write_provenance(tmp_path, [(f, 2)], seed=7, domain="retail", scale="small")
    assert side.name == "_shape_provenance.json"
    doc = json.loads(side.read_text())
    assert doc["format"] == "shape-provenance" and doc["version"] == 1
    assert doc["seed"] == 7 and doc["domain"] == "retail" and doc["scale"] == "small"
    assert doc["spec_hash"] is None and doc["shape_version"]
    assert doc["files"] == [{"path": "orders.csv", "sha256": prov.sha256_file(f), "rows": 2}]


def test_the_table_file_bytes_are_unchanged(tmp_path: Path) -> None:
    f = _csv(tmp_path)
    before = f.read_bytes()
    prov.write_provenance(tmp_path, [(f, 2)], seed=1)
    assert f.read_bytes() == before


def test_a_second_write_adds_and_replaces_entries(tmp_path: Path) -> None:
    a, b = _csv(tmp_path, "a.csv"), _csv(tmp_path, "b.csv")
    prov.write_provenance(tmp_path, [(a, 2)], seed=1)
    a.write_text("a\n9\n")
    prov.write_provenance(tmp_path, [(a, 1), (b, 2)], seed=1)
    doc = prov.read_provenance(tmp_path)
    assert doc is not None
    assert [e["path"] for e in doc["files"]] == ["a.csv", "b.csv"]
    assert doc["files"][0]["sha256"] == prov.sha256_file(a)


def test_directories_missing_files_outside_files_and_the_sidecar_are_left_out(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    f = _csv(out)
    outside = _csv(tmp_path / "elsewhere", "x.csv")
    (out / "delta_dir").mkdir()
    prov.write_provenance(
        out, [(f, 2), (out / "delta_dir", None), (out / "gone.csv", 1), (outside, 1)], seed=1
    )
    prov.write_provenance(out, [(out / prov.PROVENANCE_FILE, None)], seed=1)
    doc = prov.read_provenance(out)
    assert doc is not None and [e["path"] for e in doc["files"]] == ["orders.csv"]


def test_rows_may_be_null_and_paths_use_forward_slashes(tmp_path: Path) -> None:
    f = _csv(tmp_path / "sub")
    prov.write_provenance(tmp_path, [(f, None)], seed=None)
    doc = prov.read_provenance(tmp_path)
    assert doc is not None
    assert doc["files"][0]["path"] == "sub/orders.csv" and doc["files"][0]["rows"] is None


def test_no_sidecar_reads_as_none(tmp_path: Path) -> None:
    assert prov.read_provenance(tmp_path) is None


def test_compat_a_frozen_version_1_file_still_reads(tmp_path: Path) -> None:
    (tmp_path / prov.PROVENANCE_FILE).write_text(json.dumps(FROZEN_V1))
    doc = prov.read_provenance(tmp_path)
    assert doc is not None and doc["files"][0]["path"] == "orders.csv"


def test_compat_a_newer_version_is_refused_with_a_clear_message(tmp_path: Path) -> None:
    (tmp_path / prov.PROVENANCE_FILE).write_text(json.dumps({**FROZEN_V1, "version": 2}))
    with pytest.raises(ValueError, match="version 2, written by a newer Shape"):
        prov.read_provenance(tmp_path)


@pytest.mark.parametrize(
    "doc",
    [
        {**FROZEN_V1, "format": "something-else"},
        {**FROZEN_V1, "version": "1"},
        {**FROZEN_V1, "version": True},
        {k: v for k, v in FROZEN_V1.items() if k != "files"},
        [1, 2],
    ],
)
def test_a_file_that_is_not_provenance_is_refused(tmp_path: Path, doc: object) -> None:
    (tmp_path / prov.PROVENANCE_FILE).write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        prov.read_provenance(tmp_path)


def test_invalid_json_is_a_value_error(tmp_path: Path) -> None:
    (tmp_path / prov.PROVENANCE_FILE).write_text("{not json")
    with pytest.raises(ValueError, match="not readable provenance"):
        prov.read_provenance(tmp_path)


def test_verify_listed_changed_and_unmarked(tmp_path: Path) -> None:
    f = _csv(tmp_path)
    other = _csv(tmp_path, "real.csv")
    prov.write_provenance(tmp_path, [(f, 2)], seed=1)
    assert prov.verify_file(f) == prov.VERIFIED
    assert prov.verify_file(other) == prov.UNMARKED
    f.write_text("a,b\n1,2\n3,5\n")
    assert prov.verify_file(f) == prov.CHANGED


def test_verify_a_parquet_file_with_the_synthetic_marker(tmp_path: Path) -> None:
    table = pa.table({"x": [1, 2]}).replace_schema_metadata({"shape_synthetic": "true"})
    marked = tmp_path / "m.parquet"
    pq.write_table(table, marked)
    plain = tmp_path / "p.parquet"
    pq.write_table(pa.table({"x": [1]}), plain)
    assert prov.verify_file(marked) == prov.VERIFIED
    assert prov.verify_file(plain) == prov.UNMARKED
    assert not prov.has_synthetic_marker(tmp_path / "x.csv")


def test_a_corrupt_parquet_file_is_not_marked(tmp_path: Path) -> None:
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet")
    assert prov.verify_file(bad) == prov.UNMARKED


def test_ignored_names() -> None:
    assert prov.is_ignored("x/_shape_provenance.json") and prov.is_ignored("_SUCCESS")
    assert not prov.is_ignored("orders.csv")
