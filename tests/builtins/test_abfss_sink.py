"""The ``abfss://`` sink, rolling files and atomic publish, over an in-memory fsspec filesystem."""

from __future__ import annotations

import datetime as dt
import sys
import threading
import time
import types
from typing import Any

import fsspec
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.builtins.sinks import AbfssSink, CsvSink, DeltaSink, JsonlSink, ParquetSink
from shape.builtins.sources import AbfssSource
from shape.io.store import FsspecStore, LocalStore, is_transient

URI = "abfss://landing@acct.dfs.core.windows.net/raw"


def batch(start: int, n: int) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {"id": list(range(start, start + n)), "v": [float(i) for i in range(start, start + n)]}
    )


@pytest.fixture
def fs() -> Any:
    memory = fsspec.filesystem("memory")
    memory.store.clear()
    memory.pseudo_dirs[:] = [""]
    return memory


def finals(fs: Any, root: str = "landing/raw") -> list[str]:
    return sorted(
        p.lstrip("/")
        for p in fs.find(root)
        if "/_shape_tmp/" not in p and not p.rsplit("/", 1)[-1].startswith("_")
    )


def test_default_layout_is_dated_hive_partitions(fs: Any) -> None:
    rows = AbfssSink().write(
        URI, "orders", [batch(0, 5), batch(5, 5)], filesystem=fs, batch_date="2026-10-02"
    )
    assert rows == 10
    assert finals(fs) == ["landing/raw/orders/ingest_date=2026-10-02/orders_20261002.parquet"]
    assert pq.read_table(fs.open(finals(fs)[0], "rb")).num_rows == 10
    assert not fs.exists("landing/raw/_shape_tmp") or not fs.ls("landing/raw/_shape_tmp")


def test_date_defaults_to_today_utc(fs: Any) -> None:
    AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=fs)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    assert f"ingest_date={today}" in finals(fs)[0]


def test_per_table_formats_and_template(fs: Any) -> None:
    sink = AbfssSink()
    opts: dict[str, Any] = {
        "filesystem": fs,
        "batch_date": "2026-10-02",
        "formats": {"customers": "csv"},
        "path_template": "{table}/{yyyy}/{mm}/{table}.{ext}",
    }
    sink.write(URI, "customers", [batch(0, 3)], **opts)
    sink.write(URI, "orders", [batch(0, 3)], **opts)
    assert finals(fs) == [
        "landing/raw/customers/2026/10/customers.csv",
        "landing/raw/orders/2026/10/orders.parquet",
    ]
    assert fs.cat("landing/raw/customers/2026/10/customers.csv").startswith(b'"id","v"')


@pytest.mark.parametrize("fmt", ["parquet", "csv", "jsonl", "ipc"])
def test_every_format_reads_back_through_the_source(fs: Any, fmt: str) -> None:
    AbfssSink().write(
        URI,
        "t",
        [batch(0, 4), batch(4, 4)],
        filesystem=fs,
        format=fmt,
        batch_date="2026-10-02",
        path_template="{table}/{table}.{ext}",
    )
    out = AbfssSource().read(f"{URI}/t", filesystem=fs)
    assert sum(b.num_rows for b in out) == 8


def test_rolling_by_rows_is_exact_and_deterministic(fs: Any) -> None:
    AbfssSink().write(
        URI,
        "t",
        [batch(0, 25), batch(25, 25)],
        filesystem=fs,
        roll_rows=20,
        batch_date="2026-10-02",
    )
    files = finals(fs)
    assert [f.rsplit("_", 1)[-1] for f in files] == [
        "00001.parquet",
        "00002.parquet",
        "00003.parquet",
    ]
    counts = [pq.read_table(fs.open(f, "rb")).num_rows for f in files]
    assert counts == [20, 20, 10]
    ids = [i for f in files for i in pq.read_table(fs.open(f, "rb")).column("id").to_pylist()]
    assert ids == list(range(50))


def test_rolling_by_seconds_and_flush_make_rows_visible(fs: Any) -> None:
    now = [0.0]
    w = AbfssSink().open_table(
        URI, "t", filesystem=fs, roll_seconds=5, clock=lambda: now[0], batch_date="2026-10-02"
    )
    w.write_batch(batch(0, 3))
    assert finals(fs) == []  # nothing is visible before the file is complete
    now[0] = 6.0
    w.write_batch(batch(3, 3))  # the file is older than 5 s: rolled
    assert len(finals(fs)) == 1
    w.write_batch(batch(6, 2))
    w.flush()  # a checkpoint: what was written is visible
    assert len(finals(fs)) == 2
    assert w.close() == 8
    assert len(finals(fs)) == 2


def test_a_reader_never_sees_a_partial_file(fs: Any) -> None:
    """A poller reads every non-hidden file while a stream rolls files; each must be a whole,
    valid Parquet file."""
    seen: list[int] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            for path in finals(fs):
                try:
                    seen.append(pq.read_table(fs.open(path, "rb")).num_rows)
                except FileNotFoundError:
                    continue
                except BaseException as exc:  # a partial file would land here
                    errors.append(exc)
            time.sleep(0.001)

    t = threading.Thread(target=poll)
    t.start()
    try:
        w = AbfssSink().open_table(URI, "t", filesystem=fs, roll_rows=100, batch_date="2026-10-02")
        for i in range(30):
            w.write_batch(batch(i * 50, 50))
            time.sleep(0.002)
        w.close()
    finally:
        stop.set()
        t.join()
    assert errors == []
    assert seen and set(seen) == {100}
    assert len(finals(fs)) == 15


def test_modes(fs: Any) -> None:
    sink = AbfssSink()
    opts: dict[str, Any] = {"filesystem": fs, "batch_date": "2026-10-02", "roll_rows": 10}
    sink.write(URI, "t", [batch(0, 25)], **opts)
    assert len(finals(fs)) == 3
    with pytest.raises(FileExistsError):
        sink.write(URI, "t", [batch(0, 5)], mode="fail", **opts)
    sink.write(URI, "t", [batch(100, 15)], mode="append", **opts)
    names = [f.rsplit("_", 1)[-1] for f in finals(fs)]
    assert names == [f"0000{i}.parquet" for i in range(1, 6)]
    sink.write(URI, "t", [batch(0, 5)], mode="overwrite", **opts)  # replaces part 00001
    assert len(finals(fs)) == 5
    with pytest.raises(ValueError, match="part"):
        sink.write(URI, "t", [batch(0, 1)], mode="append", filesystem=fs, batch_date="2026-10-02")


def test_manifest_is_written_last(fs: Any) -> None:
    AbfssSink().write(
        URI,
        "t",
        [batch(0, 20)],
        filesystem=fs,
        roll_rows=10,
        manifest=True,
        batch_date="2026-10-02",
    )
    assert fs.exists("landing/raw/t/ingest_date=2026-10-02/_SUCCESS")


def test_empty_input_with_schema_writes_a_valid_empty_file(fs: Any) -> None:
    schema = batch(0, 1).schema
    assert (
        AbfssSink().write(URI, "t", [], filesystem=fs, schema=schema, batch_date="2026-10-02") == 0
    )
    (path,) = finals(fs)
    assert pq.read_table(fs.open(path, "rb")).schema.names == ["id", "v"]


def test_failure_leaves_no_partial_file(fs: Any) -> None:
    def broken() -> Any:
        yield batch(0, 5)
        raise RuntimeError("generator failed")

    with pytest.raises(RuntimeError):
        AbfssSink().write(URI, "t", broken(), filesystem=fs, batch_date="2026-10-02")
    assert finals(fs) == []


def test_transient_upload_failure_is_retried(fs: Any) -> None:
    class Flaky:
        def __init__(self, inner: Any) -> None:
            self.inner, self.fail = inner, 2

        def open(self, path: str, mode: str = "rb", **kw: Any) -> Any:
            if "wb" in mode and self.fail > 0:
                self.fail -= 1
                raise ConnectionError("reset")
            return self.inner.open(path, mode, **kw)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    sleeps: list[float] = []
    flaky = Flaky(fs)
    AbfssSink().write(
        URI, "t", [batch(0, 5)], filesystem=flaky, batch_date="2026-10-02", sleep=sleeps.append
    )
    assert len(finals(fs)) == 1
    assert sleeps == [0.5, 1.0]


def test_retries_are_bounded_and_permanent_errors_are_not_retried(fs: Any) -> None:
    class Down:
        def __init__(self, exc: Exception) -> None:
            self.exc, self.calls = exc, 0

        def open(self, *a: Any, **k: Any) -> Any:
            self.calls += 1
            raise self.exc

    down = Down(ConnectionError("down"))
    with pytest.raises(ConnectionError):
        AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=down, retries=2, sleep=lambda s: None)
    assert down.calls == 3
    denied = Down(PermissionError("no"))
    with pytest.raises(PermissionError):
        AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=denied, sleep=lambda s: None)
    assert denied.calls == 1


def test_authorization_and_missing_container_have_clear_messages() -> None:
    class Http(Exception):
        def __init__(self, status: int, text: str) -> None:
            super().__init__(text)
            self.status_code = status

    class Fs:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def open(self, *a: Any, **k: Any) -> Any:
            raise self.exc

    with pytest.raises(PermissionError, match="Storage Blob Data Contributor") as denied:
        AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=Fs(Http(403, "key=SECRET123")))
    assert "SECRET123" not in str(denied.value)
    with pytest.raises(FileNotFoundError, match="landing"):
        AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=Fs(Http(404, "ContainerNotFound")))


def test_secrets_are_not_in_errors(fs: Any) -> None:
    with pytest.raises(ValueError) as err:
        AbfssSink().write(
            URI, "t", [batch(0, 1)], filesystem=fs, account_key="TOPSECRETKEY", format="nope"
        )
    assert "TOPSECRETKEY" not in str(err.value)


def test_unsafe_names_are_refused(fs: Any) -> None:
    with pytest.raises(ValueError):
        AbfssSink().write(URI, "../x", [batch(0, 1)], filesystem=fs, batch_date="2026-10-02")
    with pytest.raises(ValueError):
        AbfssSink().write(URI, "t", [batch(0, 1)], filesystem=fs, path_template="../{table}.{ext}")


def test_the_abfss_sink_authenticates_like_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Adlfs:
        def __init__(self, **kw: Any) -> None:
            seen.update(kw)
            raise RuntimeError("stop")

    # a stand-in module: the sink's authentication is what is checked, so the Azure SDK is not
    # needed (and is not imported, which the no-cloud-SDK test of the credential resolver checks)
    fake = types.ModuleType("adlfs")
    fake.AzureBlobFileSystem = Adlfs  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adlfs", fake)
    with pytest.raises(RuntimeError):
        AbfssSink().write(URI, "t", [batch(0, 1)], account_key="K")
    assert seen == {"account_key": "K", "account_name": "acct"}
    seen.clear()
    with pytest.raises(RuntimeError):
        AbfssSink().write(
            "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/x",
            "t",
            [batch(0, 1)],
            sas_token="S",
        )
    assert seen["account_host"] == "onelake.blob.fabric.microsoft.com"


# ---- local rolling files ----------------------------------------------------------------------


@pytest.mark.parametrize("sink", [ParquetSink(), CsvSink(), JsonlSink()])
def test_local_sinks_roll_with_atomic_names(tmp_path: Any, sink: Any) -> None:
    rows = sink.write(str(tmp_path), "t", [batch(0, 25)], roll_rows=10)
    assert rows == 25
    ext = sink.extension
    names = sorted(p.name for p in (tmp_path / "t").iterdir())
    assert names == [f"t-0000{i}.{ext}" for i in (1, 2, 3)]  # no .tmp files remain


def test_local_non_rolling_is_unchanged(tmp_path: Any) -> None:
    ParquetSink().write(str(tmp_path / "x.parquet"), "t", [batch(0, 3)])
    assert pq.read_table(tmp_path / "x.parquet").num_rows == 3


def test_local_store_publish_and_abort(tmp_path: Any) -> None:
    store = LocalStore(tmp_path)
    pending = store.create("a/b.bin")
    pending.handle.write(b"x")
    assert not (tmp_path / "a" / "b.bin").exists()
    pending.publish()
    assert (tmp_path / "a" / "b.bin").read_bytes() == b"x"
    other = store.create("a/c.bin")
    other.abort()
    assert store.names("a") == ["b.bin"]
    with pytest.raises(ValueError):
        store.create("../escape")


def test_fsspec_store_spools_and_publishes(fs: Any) -> None:
    store = FsspecStore(fs, "c/root", spool_bytes=4)
    p = store.create("x/y.bin")
    p.handle.write(b"0123456789")  # larger than the spool: goes to disk
    assert not fs.exists("c/root/x/y.bin")
    p.publish()
    assert fs.cat("c/root/x/y.bin") == b"0123456789"
    assert store.names("x") == ["y.bin"]
    assert store.names("missing") == []


def test_is_transient() -> None:
    assert is_transient(ConnectionError())
    assert is_transient(TimeoutError())
    assert not is_transient(PermissionError())
    assert not is_transient(ValueError())

    class E(Exception):
        status_code = 503

    assert is_transient(E())
    E.status_code = 400
    assert not is_transient(E())


# ---- Delta ----------------------------------------------------------------------------------


def test_delta_commits_per_micro_batch(tmp_path: Any) -> None:
    deltalake = pytest.importorskip("deltalake")
    writer = DeltaSink().open_table(str(tmp_path), "t", schema=batch(0, 1).schema, commit_rows=20)
    seen: list[int] = []
    for i in range(6):
        writer.write_batch(batch(i * 10, 10))
        if (tmp_path / "t" / "_delta_log").exists():
            seen.append(deltalake.DeltaTable(str(tmp_path / "t")).to_pyarrow_table().num_rows)
    assert writer.close() == 60
    assert seen == [20, 20, 40, 40, 60]  # rows are readable while the stream runs
    table = deltalake.DeltaTable(str(tmp_path / "t"))
    assert table.version() == 2 and table.to_pyarrow_table().num_rows == 60


def test_delta_write_with_commit_rows_and_mode_overwrite(tmp_path: Any) -> None:
    deltalake = pytest.importorskip("deltalake")
    sink = DeltaSink()
    sink.write(str(tmp_path), "t", [batch(0, 30)], commit_rows=10)
    sink.write(str(tmp_path), "t", [batch(0, 5)], commit_rows=10)  # overwrite: first commit
    assert deltalake.DeltaTable(str(tmp_path / "t")).to_pyarrow_table().num_rows == 5


def test_delta_plain_write_unchanged(tmp_path: Any) -> None:
    deltalake = pytest.importorskip("deltalake")
    assert DeltaSink().write(str(tmp_path), "t", [batch(0, 7)]) == 7
    assert deltalake.DeltaTable(str(tmp_path / "t")).version() == 0


def test_delta_cloud_uri_builds_storage_options(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("deltalake")
    calls: list[tuple[str, dict[str, Any]]] = []

    import deltalake

    def fake(location: str, data: Any, **kw: Any) -> None:
        for _ in data:
            pass
        calls.append((location, kw))

    monkeypatch.setattr(deltalake, "write_deltalake", fake)
    DeltaSink().write(
        "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables",
        "orders",
        [batch(0, 2)],
        account_key="K",
    )
    location, kw = calls[0]
    assert location == "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/orders"
    assert kw["storage_options"]["azure_storage_account_key"] == "K"
    assert kw["storage_options"]["use_fabric_endpoint"] == "true"
    assert kw["mode"] == "overwrite"


def test_tsv_is_written_tab_separated(fs: Any) -> None:
    AbfssSink().write(
        URI, "t", [batch(0, 2)], filesystem=fs, format="tsv", path_template="{table}.{ext}"
    )
    assert fs.cat("landing/raw/t.tsv").splitlines()[1] == b"0\t0"


# --- OneLake targets: the pre-write check (W7-02) -------------------------------------------

OL = "onelake.dfs.fabric.microsoft.com"
WS_GUID = "11111111-2222-3333-4444-555555555555"
ITEM_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LH_FILES = f"abfss://ws@{OL}/lh.Lakehouse/Files/landing"
IDS_FORM = f"abfss://<workspace-id>@{OL}/<item-id>/Files/<folder>"
FILES_ONLY = (
    "OneLake only accepts files below <item>/Files/ (or Delta tables below <item>/Tables/ "
    "with the delta sink)"
)
WAREHOUSE_MSG = (
    "Warehouse tables are written through T-SQL, not OneLake storage: use the Fabric "
    "warehouse writer (the shape-fabric warehouse target)"
)
TABLES_MSG = (
    "files under Tables/ are not tables: write Delta with delta+abfss://... (the delta sink), "
    "or write files below Files/"
)


class RecordingFs:
    """Wraps a filesystem and records every method called on it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):

            def call(*a: Any, **k: Any) -> Any:
                self.calls.append(name)
                return attr(*a, **k)

            return call
        return attr


@pytest.fixture
def lake(fs: Any) -> Any:
    """An in-memory OneLake: workspace ``ws`` with ``lh.Lakehouse`` and ``wh.Warehouse``, and a
    workspace by GUID holding an item by GUID."""
    for path in (
        "ws/lh.Lakehouse/Files",
        "ws/lh.Lakehouse/Tables",
        "ws/wh.Warehouse/Tables",
        f"{WS_GUID}/{ITEM_GUID}/Files",
    ):
        fs.mkdir(path, create_parents=True)
    return fs


def everything(fs: Any) -> list[str]:
    return sorted(fs.find(""))


def one_line(exc: BaseException) -> str:
    text = str(exc)
    assert "\n" not in text
    return text


@pytest.mark.parametrize(
    ("uri", "message"),
    [
        (
            f"abfss://my ws@{OL}/lh.Lakehouse/Files/x",
            f'OneLake workspace or item name "my ws" contains a space; use the workspace and '
            f"item IDs instead: {IDS_FORM}",
        ),
        (
            f"abfss://my%20ws@{OL}/lh.Lakehouse/Files/x",
            f'OneLake workspace or item name "my ws" contains a space; use the workspace and '
            f"item IDs instead: {IDS_FORM}",
        ),
        (
            f"abfss://ws@{OL}/my lh.Lakehouse/Files/x",
            f'OneLake workspace or item name "my lh.Lakehouse" contains a space; use the '
            f"workspace and item IDs instead: {IDS_FORM}",
        ),
        (
            f"abfss://ws@{OL}/my%20lh.Lakehouse/Files/x",
            f'OneLake workspace or item name "my lh.Lakehouse" contains a space; use the '
            f"workspace and item IDs instead: {IDS_FORM}",
        ),
    ],
)
def test_onelake_names_with_spaces_are_refused(lake: Any, uri: str, message: str) -> None:
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(uri, "t", [batch(0, 2)], filesystem=lake)
    assert one_line(caught.value) == message
    assert lake.find("ws") == []


@pytest.mark.parametrize("item", ["lh", "lh.NotAType", "lakehouse", ".Lakehouse", "lh."])
def test_onelake_item_must_be_name_dot_type_or_a_guid(lake: Any, item: str) -> None:
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(f"abfss://ws@{OL}/{item}/Files/x", "t", [batch(0, 1)], filesystem=lake)
    assert one_line(caught.value) == (
        f'OneLake item "{item}" is not <name>.<ItemType> (for example lh.Lakehouse) or an '
        f"item ID; use abfss://<workspace>@{OL}/<name>.Lakehouse/Files/<folder>"
    )
    assert lake.find("ws") == []


def test_onelake_target_without_an_item_is_refused(lake: Any) -> None:
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(f"abfss://ws@{OL}", "t", [batch(0, 1)], filesystem=lake)
    assert one_line(caught.value) == (
        f"OneLake target has no item; use abfss://<workspace>@{OL}/<name>.Lakehouse/Files/<folder>"
    )


@pytest.mark.parametrize(
    "path", ["lh.Lakehouse", "lh.Lakehouse/Other/x", "lh.Lakehouse/files/x", f"{ITEM_GUID}/x"]
)
def test_onelake_only_files_or_tables_areas(lake: Any, path: str) -> None:
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(f"abfss://ws@{OL}/{path}", "t", [batch(0, 1)], filesystem=lake)
    assert one_line(caught.value) == FILES_ONLY
    assert [p for p in lake.find("ws") if "/_shape_tmp/" in p or p.endswith(".parquet")] == []


def test_onelake_missing_item_cannot_be_created_by_storage(lake: Any) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        AbfssSink().write(
            f"abfss://ws@{OL}/new.Lakehouse/Files/x", "t", [batch(0, 1)], filesystem=lake
        )
    assert one_line(caught.value) == (
        'OneLake item "new.Lakehouse" does not exist in workspace "ws"; the storage API cannot '
        "create Fabric items: create the Lakehouse in Fabric first (or with shape fabric setup)"
    )
    assert not lake.exists("ws/new.Lakehouse")
    assert not any(p.endswith(".parquet") or "_shape_tmp" in p for p in lake.find("ws"))


def test_onelake_missing_item_by_guid_is_refused_too(lake: Any) -> None:
    other = "99999999-8888-7777-6666-555555555555"
    with pytest.raises(FileNotFoundError, match=f'OneLake item "{other}" does not exist'):
        AbfssSink().write(
            f"abfss://{WS_GUID}@{OL}/{other}/Files/x", "t", [batch(0, 1)], filesystem=lake
        )


@pytest.mark.parametrize("area", ["Files/x", "Tables/x", "Tables", "Other/x"])
def test_onelake_warehouse_is_written_through_tsql(lake: Any, area: str) -> None:
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(
            f"abfss://ws@{OL}/wh.Warehouse/{area}", "t", [batch(0, 1)], filesystem=lake
        )
    assert one_line(caught.value) == WAREHOUSE_MSG
    assert not any(p.endswith(".parquet") or "_shape_tmp" in p for p in lake.find("ws"))


@pytest.mark.parametrize(
    "path", ["lh.Lakehouse/Tables", "lh.Lakehouse/Tables/t", f"{ITEM_GUID}/Tables"]
)
def test_onelake_plain_files_under_tables_are_not_tables(lake: Any, path: str) -> None:
    ws = WS_GUID if path.startswith(ITEM_GUID) else "ws"
    with pytest.raises(ValueError) as caught:
        AbfssSink().write(f"abfss://{ws}@{OL}/{path}", "t", [batch(0, 1)], filesystem=lake)
    assert one_line(caught.value) == TABLES_MSG
    assert not any(p.endswith(".parquet") or "_shape_tmp" in p for p in lake.find(ws))


def test_every_refusal_leaves_the_filesystem_untouched(lake: Any) -> None:
    before = everything(lake)
    for path in ("lh/Files/x", "lh.Lakehouse", "wh.Warehouse/Files/x", "lh.Lakehouse/Tables/x"):
        with pytest.raises((ValueError, FileNotFoundError)):
            AbfssSink().write(f"abfss://ws@{OL}/{path}", "t", [batch(0, 1)], filesystem=lake)
    assert everything(lake) == before


@pytest.mark.parametrize(
    ("uri", "root"),
    [
        (f"abfss://ws@{OL}/lh.Lakehouse/Files/landing", "ws/lh.Lakehouse/Files/landing"),
        (f"abfss://ws@{OL}/lh.Lakehouse/Files", "ws/lh.Lakehouse/Files"),
        (
            f"abfss://{WS_GUID}@{OL}/{ITEM_GUID}/Files/landing",
            f"{WS_GUID}/{ITEM_GUID}/Files/landing",
        ),
        (f"abfss://ws@westus-{OL}/lh.Lakehouse/Files/landing", "ws/lh.Lakehouse/Files/landing"),
        (f"abfss://ws@{OL}/lh.Lakehouse/Files/my%20folder", "ws/lh.Lakehouse/Files/my folder"),
    ],
)
def test_accepted_onelake_forms_write_what_the_sink_wrote_before(
    lake: Any, monkeypatch: pytest.MonkeyPatch, uri: str, root: str
) -> None:
    opts: dict[str, Any] = {"filesystem": lake, "batch_date": "2026-10-02"}
    assert AbfssSink().write(uri, "t", [batch(0, 5)], **opts) == 5
    written = {p: lake.cat(p) for p in finals(lake, root)}
    assert len(written) == 1
    assert not any("/_shape_tmp/" in p and not p.endswith("/") for p in lake.find(root))

    # the same write with the check switched off (the earlier behavior): identical files
    for path in lake.find(root):
        lake.rm(path)
    monkeypatch.setattr(AbfssSink, "_check_onelake", lambda self, loc, fs: None)
    AbfssSink().write(uri, "t", [batch(0, 5)], **opts)
    assert {p: lake.cat(p) for p in finals(lake, root)} == written


def test_the_item_is_checked_once_per_uri(lake: Any) -> None:
    rec = RecordingFs(lake)
    sink = AbfssSink()
    for table in ("a", "b", "c"):
        sink.write(LH_FILES, table, [batch(0, 1)], filesystem=rec)
    item_checks = [c for c in rec.calls if c == "isdir"]
    assert len(item_checks) == 1


def test_adls_host_is_not_checked_and_makes_no_extra_call(fs: Any) -> None:
    """Paths that a OneLake check would refuse are written as before on an ADLS Gen2 host, and
    the filesystem sees exactly the calls it saw before the check existed."""
    uri = "abfss://my ws@acct.dfs.core.windows.net/no%20type/Other/x"
    rec = RecordingFs(fs)
    AbfssSink().write(uri, "t", [batch(0, 3)], filesystem=rec, batch_date="2026-10-02")
    assert any(p.endswith(".parquet") for p in fs.find(""))

    for path in fs.find(""):
        fs.rm(path)
    baseline = RecordingFs(fs)
    import shape.builtins.sinks.azure as azure_sink

    original = azure_sink.AbfssSink._check_onelake
    azure_sink.AbfssSink._check_onelake = lambda self, loc, filesystem: None  # type: ignore[method-assign]
    try:
        AbfssSink().write(uri, "t", [batch(0, 3)], filesystem=baseline, batch_date="2026-10-02")
    finally:
        azure_sink.AbfssSink._check_onelake = original  # type: ignore[method-assign]
    assert rec.calls == baseline.calls


def test_adls_and_a_non_onelake_host_keep_the_old_paths(fs: Any) -> None:
    for uri in (
        "abfss://landing@acct.dfs.core.windows.net/lh.Warehouse/Tables/x",
        "abfss://landing@onelake.example.com/lh/Files/x",
    ):
        AbfssSink().write(uri, "t", [batch(0, 1)], filesystem=fs, batch_date="2026-10-02")
    assert len([p for p in fs.find("landing") if p.endswith(".parquet")]) == 2


def test_onelake_refusals_never_contain_a_credential(lake: Any) -> None:
    secret = "sv=2024&sig=TOPSECRETSIG"
    for path in ("lh/Files/x", "wh.Warehouse/Files/x", "lh.Lakehouse/Tables/x", "lh.Lakehouse"):
        with pytest.raises(ValueError) as caught:
            AbfssSink().write(
                f"abfss://ws@{OL}/{path}", "t", [batch(0, 1)], filesystem=lake, sas_token=secret
            )
        assert secret not in str(caught.value) and "TOPSECRET" not in str(caught.value)
    with pytest.raises(FileNotFoundError) as missing:
        AbfssSink().write(
            f"abfss://ws@{OL}/new.Lakehouse/Files/x",
            "t",
            [batch(0, 1)],
            filesystem=lake,
            sas_token=secret,
        )
    assert "TOPSECRET" not in str(missing.value)


class _Forbidden(Exception):
    status_code = 403


def test_a_failing_item_check_is_translated_like_any_storage_error(lake: Any) -> None:
    class Denied(RecordingFs):
        def isdir(self, *a: Any, **k: Any) -> Any:
            raise _Forbidden("AuthorizationFailure sig=TOPSECRET")

        def exists(self, *a: Any, **k: Any) -> Any:
            raise _Forbidden("AuthorizationFailure sig=TOPSECRET")

        def info(self, *a: Any, **k: Any) -> Any:
            raise _Forbidden("AuthorizationFailure sig=TOPSECRET")

    with pytest.raises(PermissionError) as caught:
        AbfssSink().write(LH_FILES, "t", [batch(0, 1)], filesystem=Denied(lake))
    assert "not authorized to write to abfss://ws@" + OL in str(caught.value)
    assert "TOPSECRET" not in str(caught.value)


def test_the_error_translation_is_unchanged() -> None:
    from shape.builtins.sinks.azure import translate_error

    class E(Exception):
        status_code = 404

    err = translate_error(E("x"), "abfss://c@h")
    assert isinstance(err, FileNotFoundError)
    assert str(err) == (
        "abfss://c@h was not found: check the container (or OneLake workspace and lakehouse) "
        "name, and that the storage account exists"
    )


# --- the same refusals through the command line ----------------------------------------------


@pytest.fixture
def cli_lake(lake: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from shape.builtins.sources import azure

    monkeypatch.setattr(azure, "_filesystem", lambda loc, options: lake)
    return lake


CLI_CASES = [
    (
        f"abfss://my%20ws@{OL}/lh.Lakehouse/Files/x",
        f'OneLake workspace or item name "my ws" contains a space; use the workspace and item '
        f"IDs instead: {IDS_FORM}",
    ),
    (f"abfss://ws@{OL}/lh.Lakehouse/Other/x", FILES_ONLY),
    (f"abfss://ws@{OL}/wh.Warehouse/Files/x", WAREHOUSE_MSG),
    (f"abfss://ws@{OL}/lh.Lakehouse/Tables/x", TABLES_MSG),
    (
        f"abfss://ws@{OL}/new.Lakehouse/Files/x",
        'OneLake item "new.Lakehouse" does not exist in workspace "ws"; the storage API cannot '
        "create Fabric items: create the Lakehouse in Fabric first (or with shape fabric setup)",
    ),
]


# The missing-item refusal is a FileNotFoundError, which the emit runtime retries and wraps
# ("delivery failed after N retries: ..."); `stream` and `emit` for that case are blocked on a
# change outside this package (docs/plans/lane_status/W7-02.md).
CLI_RUNS = [
    (command, uri, message)
    for command in ("generate", "stream", "emit")
    for uri, message in CLI_CASES
    if command == "generate" or "does not exist" not in message
]


@pytest.mark.parametrize(("command", "uri", "message"), CLI_RUNS)
def test_cli_exits_2_before_any_file_appears(
    cli_lake: Any, capsys: Any, tmp_path: Any, command: str, uri: str, message: str
) -> None:
    import json
    import sys as _sys
    from pathlib import Path

    from shape.cli.main import main

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
    from scale_schemas import plain_doc

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(plain_doc({"customer": 40})))
    before = everything(cli_lake)
    if command == "generate":
        argv = ["generate", str(schema), "--seed", "3", "--to", uri]
    else:
        argv = [command, str(schema), "--seed", "3", "--table", "customer"]
        argv += ["--max-events", "50", "--to", uri]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert f"shape: error: {message}" in err
    assert everything(cli_lake) == before
