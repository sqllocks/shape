"""PF-01 contract tests: the abfss:// and Delta sources, with no network.

The Azure SDK is replaced by recorded interactions: a fake ``adlfs`` module that records how it
was constructed and serves files from memory, fake ``notebookutils`` / ``azure.identity``
modules for the authentication order, and real local Delta tables written with ``deltalake``.
Azurite end-to-end tests are in ``test_cloud_sources_azurite.py`` (marker ``emulator``).
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import subprocess
import sys
import types
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

import shape
from shape.builtins.sources import AbfssSource, DeltaSource
from shape.builtins.sources import _azure_auth as auth
from shape.builtins.sources import azure as azure_mod
from shape.builtins.sources import delta as delta_mod
from shape.plugins import kit
from shape.plugins.host import default_host, reset_default_host

pytestmark = pytest.mark.contract

TABLE = pa.table({"id": [1, 2, 3, 4], "name": ["a", "b", "c", "d"], "x": [1.5, 2.5, 3.5, 4.5]})
ONELAKE = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files"
ADLS = "abfss://data@acct.dfs.core.windows.net"


class FakeFS:
    """The slice of an fsspec filesystem the source uses, over a dict of bytes."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.opened: list[str] = []

    def isfile(self, path: str) -> bool:
        return path in self.files

    def isdir(self, path: str) -> bool:
        return any(p.startswith(path.rstrip("/") + "/") for p in self.files)

    def find(self, path: str) -> list[str]:
        return sorted(p for p in self.files if p.startswith(path.rstrip("/") + "/"))

    def glob(self, pattern: str) -> list[str]:
        import fnmatch

        return sorted(p for p in self.files if fnmatch.fnmatch(p, pattern))

    def open(self, path: str, mode: str = "rb") -> io.BytesIO:
        self.opened.append(path)
        return io.BytesIO(self.files[path])


def _parquet(table: pa.Table, row_group_size: int | None = None) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink, row_group_size=row_group_size)
    return sink.getvalue()


def _csv(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    pacsv.write_csv(table, sink)
    return sink.getvalue()


def _ipc(table: pa.Table) -> bytes:
    sink = io.BytesIO()
    with pa.ipc.new_file(sink, table.schema) as w:
        w.write_table(table)
    return sink.getvalue()


def _jsonl(table: pa.Table) -> bytes:
    rows = table.to_pylist()
    return "\n".join(json.dumps(r) for r in rows).encode() + b"\n"


def _read(source: Any, uri: str, **options: Any) -> pa.Table:
    schema = source.schema(uri, **options)
    return pa.Table.from_batches(list(source.read(uri, **options)), schema=schema)


# --- reading through an injected filesystem ----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "blob"),
    [
        ("t.parquet", _parquet(TABLE)),
        ("t.csv", _csv(TABLE)),
        ("t.csv.gz", gzip.compress(_csv(TABLE))),
        ("t.jsonl", _jsonl(TABLE)),
        ("t.arrow", _ipc(TABLE)),
    ],
)
def test_every_file_kind_reads_through_the_filesystem(name, blob):
    fs = FakeFS({f"ws/lh.Lakehouse/Files/{name}": blob})
    got = _read(AbfssSource(), f"{ONELAKE}/{name}", filesystem=fs)
    assert got.column_names == ["id", "name", "x"]
    assert got.column("id").to_pylist() == [1, 2, 3, 4]
    assert got.column("name").to_pylist() == ["a", "b", "c", "d"]


def test_ipc_stream_format_is_read_too():
    sink = io.BytesIO()
    with pa.ipc.new_stream(sink, TABLE.schema) as w:
        w.write_table(TABLE)
    fs = FakeFS({"ws/p/t.ipc": sink.getvalue()})
    assert _read(AbfssSource(), "abfss://ws@h.dfs.core.windows.net/p/t.ipc", filesystem=fs).equals(
        TABLE
    )


def test_batches_follow_batch_rows_for_parquet():
    fs = FakeFS({"ws/p/t.parquet": _parquet(TABLE)})
    batches = list(
        AbfssSource().read(
            "abfss://ws@h.dfs.core.windows.net/p/t.parquet", filesystem=fs, batch_rows=3
        )
    )
    assert [b.num_rows for b in batches] == [3, 1]


def test_directory_and_glob_read_every_file_in_order_and_skip_markers():
    part = lambda lo: _parquet(TABLE.slice(lo, 2))  # noqa: E731
    fs = FakeFS(
        {
            "ws/d/part-0.parquet": part(0),
            "ws/d/part-1.parquet": part(2),
            "ws/d/_SUCCESS": b"",
            "ws/d/.hidden.parquet": b"not parquet",
            "ws/d/readme.txt": b"hi",
        }
    )
    src = AbfssSource()
    assert _read(src, "abfss://ws@h.dfs.core.windows.net/d", filesystem=fs).equals(TABLE)
    assert _read(src, "abfss://ws@h.dfs.core.windows.net/d/", filesystem=fs).equals(TABLE)
    assert _read(src, "abfss://ws@h.dfs.core.windows.net/d/part-*.parquet", filesystem=fs).equals(
        TABLE
    )


def test_later_files_are_conformed_to_the_first_files_schema():
    wide = TABLE.append_column("extra", pa.array([0, 0, 0, 0]))
    reordered = wide.select(["x", "id", "name", "extra"]).cast(
        pa.schema(
            [("x", pa.float32()), ("id", pa.int64()), ("name", pa.string()), ("extra", pa.int8())]
        )
    )
    fs = FakeFS({"ws/d/a.parquet": _parquet(TABLE), "ws/d/b.parquet": _parquet(reordered)})
    got = _read(AbfssSource(), "abfss://ws@h.dfs.core.windows.net/d", filesystem=fs)
    assert got.schema.equals(TABLE.schema) and got.num_rows == 8


def test_a_file_missing_a_column_is_an_error_naming_it():
    fs = FakeFS({"ws/d/a.parquet": _parquet(TABLE), "ws/d/b.parquet": _parquet(TABLE.drop(["x"]))})
    with pytest.raises(ValueError, match=r"b\.parquet lacks columns \['x'\]"):
        _read(AbfssSource(), "abfss://ws@h.dfs.core.windows.net/d", filesystem=fs)


def test_mixed_kinds_missing_paths_and_empty_directories_are_clear_errors():
    src = AbfssSource()
    mixed = FakeFS({"ws/d/a.parquet": _parquet(TABLE), "ws/d/b.csv": _csv(TABLE)})
    with pytest.raises(ValueError, match="mixed types"):
        src.schema("abfss://ws@h.dfs.core.windows.net/d", filesystem=mixed)
    with pytest.raises(FileNotFoundError, match="not found"):
        src.schema("abfss://ws@h.dfs.core.windows.net/nope", filesystem=FakeFS({}))
    with pytest.raises(FileNotFoundError, match="no CSV, Parquet"):
        src.schema(
            "abfss://ws@h.dfs.core.windows.net/d", filesystem=FakeFS({"ws/d/notes.txt": b"x"})
        )


def test_files_are_closed_after_reading():
    closed: list[str] = []

    class Tracking(io.BytesIO):
        def __init__(self, path: str, data: bytes) -> None:
            super().__init__(data)
            self.path = path

        def close(self) -> None:
            closed.append(self.path)
            super().close()

    class TrackingFS(FakeFS):
        def open(self, path: str, mode: str = "rb") -> Tracking:  # type: ignore[override]
            return Tracking(path, self.files[path])

    fs = TrackingFS({"ws/d/a.parquet": _parquet(TABLE), "ws/d/b.parquet": _parquet(TABLE)})
    AbfssSource().schema("abfss://ws@h.dfs.core.windows.net/d", filesystem=fs)
    assert closed == ["ws/d/a.parquet"]
    _read(AbfssSource(), "abfss://ws@h.dfs.core.windows.net/d", filesystem=fs)
    assert closed.count("ws/d/a.parquet") >= 2 and "ws/d/b.parquet" in closed


def test_uri_parsing_and_can_open():
    loc = azure_mod.parse(
        "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/a%20b.csv"
    )
    assert (loc.container, loc.account, loc.path) == ("ws", "onelake", "lh.Lakehouse/Files/a b.csv")
    assert loc.fs_path == "ws/lh.Lakehouse/Files/a b.csv"
    short = azure_mod.parse("abfss://data/x/y.parquet")
    assert (short.container, short.host, short.path) == ("data", None, "x/y.parquet")
    with pytest.raises(ValueError, match="not an abfss"):
        azure_mod.parse("s3://b/k")
    with pytest.raises(ValueError, match="no container"):
        azure_mod.parse("abfss:///k")
    src = AbfssSource()
    assert src.can_open("abfss://c@a.dfs.core.windows.net/x") and src.can_open("abfs://c/x")
    assert not src.can_open("delta+abfss://c@a.dfs.core.windows.net/x")
    assert not src.can_open("/tmp/x.parquet") and not src.can_open("s3://b/k")


# --- authentication order ----------------------------------------------------------------------


def _jwt(exp: int) -> str:
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


class _Utils:
    """Stands in for notebookutils; records every audience asked for."""

    def __init__(self, token: str = "nb-token") -> None:
        self.asked: list[str] = []
        self.credentials = types.SimpleNamespace(getToken=self._get)
        self._token = token

    def _get(self, audience: str) -> str:
        self.asked.append(audience)
        return self._token


@pytest.fixture
def no_ambient(monkeypatch):
    """No notebookutils and no azure-identity unless a test installs a fake."""
    for mod in ("notebookutils", "mssparkutils", "azure.identity"):
        monkeypatch.setitem(sys.modules, mod, None)  # None makes `import` raise ImportError


def _install(monkeypatch, name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def test_explicit_credential_beats_everything(monkeypatch, no_ambient):
    utils = _Utils()
    _install(monkeypatch, "notebookutils", credentials=utils.credentials)
    cred = object()
    got = auth.resolve({"credential": cred, "token": "t"})
    assert (got.method, got.credential) == ("explicit-credential", cred)
    assert utils.asked == []


def test_explicit_token_is_wrapped_and_beats_notebookutils(monkeypatch, no_ambient):
    utils = _Utils()
    _install(monkeypatch, "notebookutils", credentials=utils.credentials)
    got = auth.resolve({"token": _jwt(4_000_000_000)})
    assert got.method == "explicit-token"
    tok = got.credential.get_token(auth.STORAGE_SCOPE)
    assert tok.expires_on == 4_000_000_000 and tok.token.endswith(".sig")
    assert utils.asked == []
    assert auth.resolve({"token": "opaque"}).credential.get_token().token == "opaque"


def test_explicit_storage_keys_are_passed_to_adlfs_without_a_credential(no_ambient):
    got = auth.resolve({"account_key": "k"})
    assert (got.method, got.credential, dict(got.adlfs_options)) == (
        "explicit-keys",
        None,
        {"account_key": "k"},
    )
    assert auth.resolve({"sas_token": "s", "connection_string": "c"}).method == "explicit-keys"


def test_inside_fabric_notebookutils_storage_token_beats_the_default_credential(
    monkeypatch, no_ambient
):
    utils = _Utils(_jwt(4_100_000_000))
    _install(monkeypatch, "notebookutils", credentials=utils.credentials)
    default_built: list[int] = []
    _install(
        monkeypatch,
        "azure.identity",
        DefaultAzureCredential=lambda: default_built.append(1),
    )
    got = auth.resolve({})
    assert got.method == "notebookutils"
    tok = got.credential.get_token("anything")
    assert utils.asked == ["storage"] and tok.expires_on == 4_100_000_000
    assert default_built == []


def test_default_azure_credential_is_the_last_resort(monkeypatch, no_ambient):
    sentinel = object()
    _install(monkeypatch, "azure.identity", DefaultAzureCredential=lambda: sentinel)
    got = auth.resolve({})
    assert (got.method, got.credential) == ("default", sentinel)


def test_no_credential_and_no_azure_identity_says_what_to_install(no_ambient):
    with pytest.raises(auth.AuthError, match=r"sqllocks-shape\[azure\]"):
        auth.resolve({})


def test_bearer_token_for_delta_needs_a_credential(no_ambient):
    assert auth.bearer_token(auth.resolve({"token": "abc"})) == "abc"
    with pytest.raises(auth.AuthError, match="needs a token or credential"):
        auth.bearer_token(auth.resolve({"account_key": "k"}))


# --- how adlfs is constructed (recorded) --------------------------------------------------------


@pytest.fixture
def fake_adlfs(monkeypatch, no_ambient):
    calls: list[dict[str, Any]] = []
    fs = FakeFS(
        {"ws/lh.Lakehouse/Files/t.parquet": _parquet(TABLE), "data/t.parquet": _parquet(TABLE)}
    )

    def ctor(**kwargs: Any) -> FakeFS:
        calls.append(kwargs)
        return fs

    _install(monkeypatch, "adlfs", AzureBlobFileSystem=ctor)
    _install(monkeypatch, "azure.identity", DefaultAzureCredential=lambda: "default-cred")
    return calls


def test_onelake_uri_uses_the_fabric_blob_host_and_the_credential(fake_adlfs):
    got = _read(AbfssSource(), f"{ONELAKE}/t.parquet", token="tok")
    assert got.equals(TABLE)
    call = fake_adlfs[0]  # one filesystem per schema() and per read()
    assert call["account_name"] == "onelake"
    assert call["account_host"] == "onelake.blob.fabric.microsoft.com"
    assert call["credential"].get_token().token == "tok"


def test_adls_uri_names_the_account_and_uses_the_default_credential(fake_adlfs):
    _read(AbfssSource(), f"{ADLS}/t.parquet")
    assert fake_adlfs[0] == {"account_name": "acct", "credential": "default-cred"}


def test_short_form_needs_an_account_name_or_a_connection_string(fake_adlfs, monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    with pytest.raises(ValueError, match="no storage account"):
        AbfssSource().schema("abfss://data/t.parquet", token="t")
    AbfssSource().schema("abfss://data/t.parquet", token="t", account_name="acct")
    AbfssSource().schema("abfss://data/t.parquet", connection_string="UseDevelopmentStorage=true")
    assert fake_adlfs[-2]["account_name"] == "acct"
    assert fake_adlfs[-1] == {"connection_string": "UseDevelopmentStorage=true"}


def test_without_adlfs_the_error_names_the_extra(no_ambient, monkeypatch):
    monkeypatch.setitem(sys.modules, "adlfs", None)
    with pytest.raises(ImportError, match=r"sqllocks-shape\[azure\]"):
        AbfssSource().schema(f"{ADLS}/t.parquet", token="t")


# --- conformance kit, registry and the profile entry point -------------------------------------


def test_abfss_source_passes_the_plugin_conformance_kit(fake_adlfs):
    kit.check_source(AbfssSource(), f"{ONELAKE}/t.parquet")


def test_the_sources_are_registered_built_ins_that_load_without_azure_packages():
    reset_default_host()
    try:
        host = default_host()
        for name, cls in (("abfss", AbfssSource), ("delta", DeltaSource)):
            rec = host.record("shape.sources", name)
            assert rec is not None and rec.source == "sqllocks-shape"
            assert isinstance(host.get("shape.sources", name), cls)
    finally:
        reset_default_host()


def test_importing_the_sources_imports_no_azure_package():
    code = (
        "import sys, shape.builtins.sources as s;"
        "bad=[m for m in sys.modules if m.split('.')[0] in ('adlfs','azure','fsspec','deltalake')];"
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_shape_profile_reads_an_abfss_uri_through_the_registry(fake_adlfs):
    reset_default_host()
    try:
        prof = shape.profile(f"{ONELAKE}/t.parquet")
    finally:
        reset_default_host()
    assert prof is not None
    assert fake_adlfs and fake_adlfs[0]["account_name"] == "onelake"


def test_an_unknown_scheme_has_a_clear_error():
    with pytest.raises(ValueError, match="no installed source plugin reads"):
        shape.profile("s3://bucket/key.parquet")


# --- Delta ---------------------------------------------------------------------------------------


@pytest.fixture
def delta_table(tmp_path):
    deltalake = pytest.importorskip("deltalake")
    path = tmp_path / "sales"
    deltalake.write_deltalake(str(path), TABLE)
    deltalake.write_deltalake(
        str(path),
        pa.table({"id": [5], "name": ["e"], "x": [5.5]}),
        mode="append",
    )
    return path


def test_local_delta_table_schema_rows_version_and_columns(delta_table):
    src = DeltaSource()
    assert src.can_open(str(delta_table)) and src.can_open(delta_table.as_uri())
    assert not src.can_open(str(delta_table.parent)) and not src.can_open("abfss://c@a/x")
    got = _read(src, str(delta_table))
    assert sorted(got.column("id").to_pylist()) == [1, 2, 3, 4, 5]
    assert got.column_names == ["id", "name", "x"]
    v0 = _read(src, str(delta_table), version=0)
    assert v0.num_rows == 4
    only = _read(src, str(delta_table), columns=["id"])
    assert only.column_names == ["id"] and only.num_rows == 5


def test_delta_passes_the_plugin_conformance_kit(delta_table):
    kit.check_source(DeltaSource(), str(delta_table))


def test_shape_profile_reads_a_local_delta_table(delta_table):
    assert shape.profile(str(delta_table)) is not None


def test_cloud_delta_storage_options_follow_the_auth_order(no_ambient):
    uri = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/t"
    assert delta_mod._storage_options(uri, {"token": "tok"}) == {
        "azure_storage_token": "tok",
        "use_fabric_endpoint": "true",
    }
    adls = "abfss://c@acct.dfs.core.windows.net/t"
    assert delta_mod._storage_options(adls, {"token": "tok"}) == {"azure_storage_token": "tok"}
    assert delta_mod._storage_options(adls, {"account_key": "k"}) == {
        "azure_storage_account_key": "k"
    }
    assert delta_mod._storage_options(adls, {"sas_token": "s"}) == {"azure_storage_sas_key": "s"}
    merged = delta_mod._storage_options(adls, {"token": "tok", "storage_options": {"a": 1}})
    assert merged == {"azure_storage_token": "tok", "a": "1"}
    with pytest.raises(ValueError, match="connection string"):
        delta_mod._storage_options(adls, {"connection_string": "c"})
    assert delta_mod._storage_options("/local/table", {}) == {}


def test_cloud_delta_passes_the_derived_options_to_delta_rs(monkeypatch, no_ambient):
    seen: dict[str, Any] = {}

    class FakeDeltaTable:
        def __init__(self, target: str, version: int | None = None, storage_options: Any = None):
            seen.update(target=target, version=version, storage_options=storage_options)

        def to_pyarrow_dataset(self) -> Any:
            return types.SimpleNamespace(
                schema=TABLE.schema,
                to_batches=lambda columns=None, batch_size=0: iter(TABLE.to_batches()),
            )

    _install(monkeypatch, "deltalake", DeltaTable=FakeDeltaTable)
    uri = "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/t"
    assert DeltaSource().can_open(uri) and not AbfssSource().can_open(uri)
    got = _read(DeltaSource(), uri, token="tok", version=3)
    assert got.equals(TABLE)
    assert seen == {
        "target": "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/t",
        "version": 3,
        "storage_options": {"azure_storage_token": "tok", "use_fabric_endpoint": "true"},
    }


def test_not_a_delta_table_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="not a Delta table"):
        DeltaSource().schema(str(tmp_path))
