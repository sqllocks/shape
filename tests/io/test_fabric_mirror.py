"""W2-02 contract tests: the ``fabric-mirror`` sink writes an open mirroring landing zone.

Local landing zones are real folders; ``abfss://`` targets use a recording stand-in for the
filesystem, so no network is needed. The format rules come from Microsoft Learn, "Open Mirroring
Landing Zone Requirements and Formats" (see docs/FABRIC_MIRROR.md for the citation).
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.builtins.sinks import FabricMirrorSink
from shape.plugins.host import default_host

pytestmark = pytest.mark.contract

NAME = re.compile(r"^\d{20}\.(parquet|csv)$")


def batches(rows: dict[str, list[Any]]) -> list[pa.RecordBatch]:
    return pa.table(rows).to_batches()


def sink_write(root: Any, table: str, data: Any, **options: Any) -> int:
    return FabricMirrorSink().write(str(root), table, data, **options)


def files(root: Path, table: str) -> list[str]:
    return sorted(p.name for p in (root / table).iterdir())


def read(root: Path, table: str, n: int) -> pa.Table:
    return pq.read_table(root / table / f"{n:020d}.parquet")


ROWS = {"id": [1, 2, 3], "name": ["a", "b", "c"]}


def test_registered_as_a_sink_plugin() -> None:
    sink = default_host().get("shape.sinks", "fabric-mirror")
    assert isinstance(sink, FabricMirrorSink)


def test_insert_mode_marker_is_last_column_and_zero(tmp_path: Path) -> None:
    n = sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    assert n == 3
    t = read(tmp_path, "T", 1)
    assert t.column_names == ["id", "name", "__rowMarker__"]
    assert t.column("__rowMarker__").to_pylist() == [0, 0, 0]
    assert t.column("id").to_pylist() == [1, 2, 3]  # row order preserved


@pytest.mark.parametrize(
    ("marker", "value"), [("insert", 0), ("update", 1), ("delete", 2), ("upsert", 4), (4, 4)]
)
def test_constant_markers(tmp_path: Path, marker: Any, value: int) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"], row_marker=marker)
    assert read(tmp_path, "T", 1).column("__rowMarker__").to_pylist() == [value] * 3


@pytest.mark.parametrize("bad", [3, 5, 20, "merge", -1])
def test_invalid_marker_rejected_and_nothing_published(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ValueError, match="row marker"):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"], row_marker=bad)
    assert not (tmp_path / "T").exists() or files(tmp_path, "T") == ["_metadata.json"]


def test_change_stream_delta_types_map_to_markers(tmp_path: Path) -> None:
    data = batches(
        {
            "id": [1, 2, 3, 4],
            "v": ["a", "b", "c", "d"],
            "_shape_delta_type": ["INSERT", "UPDATE", "DELETE", "UPSERT"],
            "_shape_delta_timestamp": ["t"] * 4,
        }
    )
    sink_write(tmp_path, "T", data, key_columns=["id"])
    t = read(tmp_path, "T", 1)
    assert t.column_names == ["id", "v", "_shape_delta_timestamp", "__rowMarker__"]
    assert t.column("__rowMarker__").to_pylist() == [0, 1, 2, 4]


def test_unknown_delta_type_rejected(tmp_path: Path) -> None:
    data = batches({"id": [1], "_shape_delta_type": ["MERGE"]})
    with pytest.raises(ValueError, match="delta type"):
        sink_write(tmp_path, "T", data, key_columns=["id"])
    assert not any(p.name.startswith("0") for p in (tmp_path / "T").glob("*"))


def test_existing_marker_column_is_moved_last_and_validated(tmp_path: Path) -> None:
    data = batches({"__rowMarker__": [0, 2], "id": [1, 2]})
    sink_write(tmp_path, "T", data, key_columns=["id"])
    t = read(tmp_path, "T", 1)
    assert t.column_names == ["id", "__rowMarker__"]
    assert t.column("__rowMarker__").to_pylist() == [0, 2]
    with pytest.raises(ValueError, match="row marker"):
        sink_write(tmp_path, "U", batches({"__rowMarker__": [3], "id": [1]}), key_columns=["id"])


def test_update_delete_upsert_need_key_columns(tmp_path: Path) -> None:
    for marker in ("update", "delete", "upsert"):
        with pytest.raises(ValueError, match="key_columns"):
            sink_write(tmp_path, "T", batches(ROWS), row_marker=marker)


def test_key_columns_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nope"):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["nope"])


def test_file_names_are_20_digit_and_continue(tmp_path: Path) -> None:
    for _ in range(3):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    data_files = [f for f in files(tmp_path, "T") if not f.startswith("_")]
    assert data_files == [f"{i:020d}.parquet" for i in (1, 2, 3)]
    assert all(NAME.match(f) for f in data_files)


def test_continues_from_the_last_file_present_after_service_cleanup(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    # the mirroring service removes processed files but leaves the last one
    folder = tmp_path / "T"
    (folder / f"{1:020d}.parquet").rename(folder / f"{41:020d}.parquet")
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    assert f"{42:020d}.parquet" in files(tmp_path, "T")


def test_files_with_other_names_do_not_count_for_the_sequence(tmp_path: Path) -> None:
    folder = tmp_path / "T"
    folder.mkdir()
    for junk in ("_00000000000000000099.parquet", "notes.txt", "99.parquet"):
        (folder / junk).write_bytes(b"x")
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    assert f"{1:020d}.parquet" in files(tmp_path, "T")


def test_published_files_are_immutable(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    path = tmp_path / "T" / f"{1:020d}.parquet"
    before = path.read_bytes()
    sink_write(tmp_path, "T", batches({"id": [9], "name": ["z"]}), key_columns=["id"])
    assert path.read_bytes() == before
    assert (tmp_path / "T" / f"{2:020d}.parquet").exists()


def test_never_overwrites_a_file_that_appears_while_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shape.builtins.sinks.fabric_mirror as fm

    folder = tmp_path / "T"
    real = fm._next_sequence

    def racing(names: Any) -> int:
        n = real(names)
        (folder / f"{n:020d}.parquet").write_bytes(b"theirs")  # another publisher wins the race
        return n

    monkeypatch.setattr(fm, "_next_sequence", racing)
    folder.mkdir()
    with pytest.raises(FileExistsError):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    assert (folder / f"{1:020d}.parquet").read_bytes() == b"theirs"
    assert not [f for f in files(tmp_path, "T") if f.startswith("_0")]  # temp file cleaned up


def test_publish_is_a_rename_from_an_underscore_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    seen: list[tuple[str, str, bool]] = []
    real = os.replace

    def spy(src: Any, dst: Any) -> None:
        seen.append((Path(src).name, Path(dst).name, Path(src).exists()))
        real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    data = [s for s in seen if NAME.match(s[1])]
    assert data == [(f"_{1:020d}.parquet", f"{1:020d}.parquet", True)]
    assert not [f for f in files(tmp_path, "T") if f.startswith("_0")]


def test_a_failed_write_publishes_nothing(tmp_path: Path) -> None:
    def broken() -> Any:
        yield from batches(ROWS)
        raise RuntimeError("source died")

    with pytest.raises(RuntimeError, match="source died"):
        sink_write(tmp_path, "T", broken(), key_columns=["id"])
    assert [f for f in files(tmp_path, "T") if f != "_metadata.json"] == []


def test_no_rows_publishes_no_file(tmp_path: Path) -> None:
    n = sink_write(tmp_path, "T", [], schema=pa.schema([("id", pa.int64())]), key_columns=["id"])
    assert n == 0
    assert files(tmp_path, "T") == ["_metadata.json"]


def test_metadata_json_declares_key_columns(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id", "name"])
    meta = json.loads((tmp_path / "T" / "_metadata.json").read_text())
    assert meta["keyColumns"] == ["id", "name"]


def test_metadata_is_not_rewritten_and_key_columns_cannot_change(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    path = tmp_path / "T" / "_metadata.json"
    before = path.read_bytes()
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="cannot be changed"):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["name"])
    assert path.read_bytes() == before
    assert len([f for f in files(tmp_path, "T") if NAME.match(f)]) == 2


def test_insert_only_without_keys_writes_no_metadata(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS))
    assert files(tmp_path, "T") == [f"{1:020d}.parquet"]


def test_csv_format_header_marker_last_and_metadata(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"], format="csv", row_marker="upsert")
    t = pacsv.read_csv(tmp_path / "T" / f"{1:020d}.csv")
    assert t.column_names == ["id", "name", "__rowMarker__"]
    assert t.column("__rowMarker__").to_pylist() == [4, 4, 4]
    meta = json.loads((tmp_path / "T" / "_metadata.json").read_text())
    assert meta["keyColumns"] == ["id"]
    assert meta["FileFormat"] == "CSV"
    assert meta["FileExtension"] == "csv"
    assert meta["FileFormatTypeProperties"]["FirstRowAsHeader"] is True
    cols = meta["SchemaDefinition"]["Columns"]
    assert [c["Name"] for c in cols] == ["id", "name", "__rowMarker__"]
    assert [c["DataType"] for c in cols] == ["Int64", "String", "Int32"]


def test_csv_rejects_types_the_landing_zone_cannot_declare(tmp_path: Path) -> None:
    import decimal

    data = pa.table({"id": [1], "d": pa.array([decimal.Decimal("1.50")], pa.decimal128(5, 2))})
    with pytest.raises(ValueError, match="parquet"):
        sink_write(tmp_path, "T", data.to_batches(), key_columns=["id"], format="csv")


def test_unknown_format_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="format"):
        sink_write(tmp_path, "T", batches(ROWS), format="jsonl")


def test_mixed_formats_in_one_table_rejected(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"])
    with pytest.raises(ValueError, match="parquet"):
        sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"], format="csv")


def test_schema_folder_layout(tmp_path: Path) -> None:
    sink_write(tmp_path, "T", batches(ROWS), key_columns=["id"], schema_name="dbo")
    assert (tmp_path / "dbo.schema" / "T" / f"{1:020d}.parquet").exists()
    assert (tmp_path / "dbo.schema" / "T" / "_metadata.json").exists()


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "", "a\\b"])
def test_table_and_schema_names_cannot_escape(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        sink_write(tmp_path / "root", bad, batches(ROWS), key_columns=["id"])
    with pytest.raises(ValueError, match="unsafe"):
        sink_write(tmp_path / "root", "T", batches(ROWS), key_columns=["id"], schema_name=bad)


def test_file_uri_and_multiple_batches(tmp_path: Path) -> None:
    data = batches({"id": list(range(5)), "name": list("abcde")})
    stream = [data[0].slice(0, 2), data[0].slice(2)]
    n = FabricMirrorSink().write(tmp_path.as_uri(), "T", stream, key_columns=["id"])
    assert n == 5
    assert read(tmp_path, "T", 1).num_rows == 5


# --- abfss:// through a recording filesystem -------------------------------------------------


class RecordingFS:
    """The slice of an fsspec filesystem the sink uses, over a dict, recording every call."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.calls: list[tuple[str, ...]] = []

    def makedirs(self, path: str, exist_ok: bool = False) -> None:
        self.calls.append(("makedirs", path))

    def ls(self, path: str, detail: bool = False) -> list[str]:
        self.calls.append(("ls", path))
        prefix = path.rstrip("/") + "/"
        return sorted(p for p in self.files if p.startswith(prefix) and "/" not in p[len(prefix) :])

    def exists(self, path: str) -> bool:
        return path in self.files

    def open(self, path: str, mode: str = "rb") -> Any:
        assert mode == "wb"
        self.calls.append(("open", path))
        fs = self
        buf = io.BytesIO()
        real_close = buf.close

        def close() -> None:
            fs.files[path] = buf.getvalue()
            real_close()

        buf.close = close  # type: ignore[method-assign]
        return buf

    def cat_file(self, path: str) -> bytes:
        return self.files[path]

    def mv(self, src: str, dst: str) -> None:
        self.calls.append(("mv", src, dst))
        self.files[dst] = self.files.pop(src)

    def rm(self, path: str) -> None:
        self.calls.append(("rm", path))
        self.files.pop(path, None)


ONELAKE = "abfss://ws@onelake.dfs.fabric.microsoft.com/mirror.MirroredDatabase/Files/LandingZone"
BASE = "ws/mirror.MirroredDatabase/Files/LandingZone"


def test_onelake_landing_zone_publish_sequence_is_upload_then_rename() -> None:
    fs = RecordingFS()
    n = FabricMirrorSink().write(ONELAKE, "T", batches(ROWS), key_columns=["id"], filesystem=fs)
    assert n == 3
    final = f"{BASE}/T/{1:020d}.parquet"
    temp = f"{BASE}/T/_{1:020d}.parquet"
    ops = [c for c in fs.calls if c[0] in ("open", "mv")]
    assert ops == [("open", f"{BASE}/T/_metadata.json"), ("open", temp), ("mv", temp, final)]
    assert sorted(fs.files) == sorted([final, f"{BASE}/T/_metadata.json"])
    t = pq.read_table(io.BytesIO(fs.files[final]))
    assert t.column_names == ["id", "name", "__rowMarker__"]
    assert json.loads(fs.files[f"{BASE}/T/_metadata.json"])["keyColumns"] == ["id"]


def test_onelake_continues_numbering_and_refuses_to_overwrite() -> None:
    fs = RecordingFS(
        {
            f"{BASE}/T/{7:020d}.parquet": b"old",
            f"{BASE}/T/_metadata.json": json.dumps({"keyColumns": ["id"]}).encode(),
        }
    )
    FabricMirrorSink().write(ONELAKE, "T", batches(ROWS), key_columns=["id"], filesystem=fs)
    assert f"{BASE}/T/{8:020d}.parquet" in fs.files
    assert fs.files[f"{BASE}/T/{7:020d}.parquet"] == b"old"
    assert ("open", f"{BASE}/T/_metadata.json") not in fs.calls  # metadata is not rewritten


def test_onelake_failed_upload_leaves_only_the_underscore_name_removed() -> None:
    fs = RecordingFS()

    def broken() -> Any:
        yield from batches(ROWS)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        FabricMirrorSink().write(ONELAKE, "T", broken(), key_columns=["id"], filesystem=fs)
    assert not [p for p in fs.files if re.search(r"/\d{20}\.", p)]
    assert not [p for p in fs.files if "/_0" in p]


def test_abfss_without_adlfs_or_filesystem_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real = builtins.__import__

    def no_adlfs(name: str, *a: Any, **k: Any) -> Any:
        if name == "adlfs":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_adlfs)
    with pytest.raises(ImportError, match="azure"):
        FabricMirrorSink().write(ONELAKE, "T", batches(ROWS), key_columns=["id"], token="t")


@pytest.mark.live
def test_live_onelake_landing_zone(tmp_path: Path) -> None:
    import os

    uri = os.environ.get("SHAPE_FABRIC_MIRROR_LANDING_ZONE")
    if not uri:
        pytest.skip("SHAPE_FABRIC_MIRROR_LANDING_ZONE (abfss:// landing zone URI) is not set")
    table = f"shape_w202_{os.getpid()}"
    n = FabricMirrorSink().write(uri, table, batches(ROWS), key_columns=["id"])
    assert n == 3
    n = FabricMirrorSink().write(
        uri, table, batches({"id": [1], "name": ["z"]}), key_columns=["id"], row_marker="update"
    )
    assert n == 1
