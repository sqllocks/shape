"""Issues #40 and #42: the formats come from the installed sinks, and a sink checks the URI scheme
before it touches anything."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from engine_fixtures import STRATEGIES
from gen_fixtures import schema

from shape.cli.main import main
from shape.errors import ShapeCapabilityError
from shape.generation.engine import Engine
from shape.generation.output import available_formats, write_engine, write_result
from shape.plugins.host import PluginHost, default_host
from shape.plugins.schemes import (
    UnsupportedSchemeError,
    redact,
    require_scheme,
    sinks_by_scheme,
    uri_scheme,
)

BATCHES = [pa.record_batch({"id": [1, 2, 3], "v": ["a", "b", "c"]})]
CLOUD = {
    "parquet": "abfss://container@account.dfs.core.windows.net/landing/",
    "delta": "abfss://container@account.dfs.core.windows.net/tables/",
    "csv": "https://account.blob.core.windows.net/container/",
    "sql": "mssql://sa@localhost/db",
    "jsonl": "s3://bucket/prefix/",
    "ipc": "gs://bucket/prefix/",
    "tsv": "az://container/path",
    "excel": "abfss://container@account.dfs.core.windows.net/x.xlsx",
}


def _engine() -> Engine:
    return Engine(schema(), strategies=STRATEGIES, seed=1)


# ---- #40: the formats are the installed sinks ------------------------------------------------


def test_every_registered_sink_is_an_accepted_format():
    assert set(default_host().names("shape.sinks")) <= set(available_formats())
    assert "ipc" in available_formats()
    assert available_formats()[0] == "summary"


def test_write_result_and_write_engine_write_ipc(tmp_path: Path):
    engine = _engine()
    out = tmp_path / "engine"
    paths = write_engine(engine, "ipc", out)
    assert paths and all(p.suffix == ".arrow" and p.exists() for p in paths)
    with ipc.open_file(str(paths[0])) as reader:
        assert reader.read_all().num_rows > 0
    result = _engine().generate()
    out2 = tmp_path / "result"
    paths2 = write_result(result, "ipc", out2)
    assert [p.name for p in paths2] == [f"{n}.arrow" for n in result.generation_order]
    with ipc.open_file(str(paths2[0])) as reader:
        assert reader.read_all().num_rows == result.tables[result.generation_order[0]].num_rows


def test_the_cli_accepts_ipc(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["generate", "retail", "-f", "ipc", "-o", str(tmp_path), "--scale", "small"]) == 0
    assert list(tmp_path.glob("*.arrow"))


def test_an_unknown_format_lists_the_installed_ones(tmp_path: Path):
    with pytest.raises(ValueError, match=r"unknown format 'nope'.*ipc"):
        write_result(_engine().generate(), "nope", tmp_path)
    with pytest.raises(ValueError, match="unknown format 'summary'"):
        write_result(_engine().generate(), "summary", tmp_path)


def test_a_third_party_sink_is_a_format_without_editing_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    written: dict[str, str] = {}

    class NdjsonSink:
        name = "ndjson"
        schemes = ("file",)
        extension = "ndjson"

        def write(self, uri, table, batches, **options):
            written[table] = uri
            Path(uri).write_text("".join(str(b.num_rows) for b in batches))
            return 0

    host = default_host()
    host.register("shape.sinks", "ndjson", NdjsonSink)
    try:
        assert "ndjson" in available_formats()
        paths = write_engine(_engine(), "ndjson", tmp_path)
        assert paths and all(p.suffix == ".ndjson" and p.exists() for p in paths)
        paths = write_result(_engine().generate(), "ndjson", tmp_path / "r")
        assert all(p.suffix == ".ndjson" for p in paths)
    finally:
        host.reload()


# ---- #42: the scheme is checked first --------------------------------------------------------


@pytest.mark.parametrize("fmt", sorted(CLOUD))
def test_a_sink_refuses_a_cloud_or_database_uri(fmt: str, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sink = default_host().get("shape.sinks", fmt)
    uri = CLOUD[fmt]
    with pytest.raises(UnsupportedSchemeError) as caught:
        sink.write(uri, "t", iter(BATCHES))
    message = str(caught.value)
    assert f"the {fmt} sink writes only to local files" in message and f"got {uri}" in message
    assert "Sinks by scheme: file:" in message
    assert "plugin" in message
    assert list(tmp_path.iterdir()) == [], "nothing may be created for a refused URI"
    assert isinstance(caught.value, (ShapeCapabilityError, ValueError))


def test_the_message_explains_each_family():
    sink = default_host().get("shape.sinks", "parquet")
    with pytest.raises(
        UnsupportedSchemeError,
        match=(
            r"OneLake and ADLS Gen2 sinks are provided by the abfss and fabric-mirror sink: "
            r"shape generate --to abfss://\.\.\."
        ),
    ):
        require_scheme(sink, "abfss://c@a.dfs.core.windows.net/x")
    with pytest.raises(UnsupportedSchemeError, match="database sinks.*INSERT scripts"):
        require_scheme(sink, "mssql://sa@localhost/db")
    with pytest.raises(UnsupportedSchemeError, match="Amazon S3 sinks are not available"):
        require_scheme(sink, "s3://bucket/x")
    with pytest.raises(UnsupportedSchemeError, match="no installed sink writes weird:// URIs"):
        require_scheme(sink, "weird://x")


def test_a_password_in_the_uri_is_not_echoed():
    sink = default_host().get("shape.sinks", "sql")
    with pytest.raises(UnsupportedSchemeError) as caught:
        require_scheme(sink, "mssql://sa:hunter2@localhost/db")
    assert "hunter2" not in str(caught.value)
    assert "mssql://sa:***@localhost/db" in str(caught.value)
    assert redact("/plain/path") == "/plain/path"


@pytest.mark.parametrize(
    "uri",
    ["out.csv", "/abs/out.csv", "rel/dir/", "file:///tmp/x", "FILE:///tmp/x", "C:\\data", "C:/d"],
)
def test_local_paths_and_file_uris_pass(uri: str):
    assert uri_scheme(uri) == "file"
    require_scheme(default_host().get("shape.sinks", "csv"), uri)


def test_the_schemes_come_from_the_declaring_sinks():
    table = sinks_by_scheme()
    assert set(table["file"]) >= {"csv", "ipc", "parquet", "sql", "delta", "excel", "jsonl", "tsv"}

    class S3Sink:
        name = "s3test"
        schemes = ("s3",)

        def write(self, uri, table, batches, **options):
            return 0

    host = PluginHost(entry_points=lambda: [])
    host.register("shape.sinks", "s3test", S3Sink)
    assert sinks_by_scheme(host) == {"s3": ["s3test"]}
    require_scheme(host.get("shape.sinks", "s3test"), "s3://bucket/x")  # declared: accepted
    with pytest.raises(
        UnsupportedSchemeError, match="the s3test sink writes only to URIs of scheme s3://"
    ):
        require_scheme(host.get("shape.sinks", "s3test"), "/local/path", host=host)


def test_generation_refuses_a_cloud_destination_before_generating(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for fmt in ("parquet", "ipc"):
        with pytest.raises(UnsupportedSchemeError, match=f"the {fmt} sink writes only"):
            write_engine(_engine(), fmt, "abfss://c@a.dfs.core.windows.net/out")
        with pytest.raises(UnsupportedSchemeError):
            write_result(_engine().generate(), fmt, "s3://bucket/out")
    assert list(tmp_path.iterdir()) == []


def test_the_cli_reports_it_as_a_shape_error(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = main(["generate", "retail", "-f", "parquet", "-o", "abfss://c@a.dfs.core.windows.net/x"])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("shape: error: the parquet sink writes only to local files; got abfss://")
    assert list(tmp_path.iterdir()) == []
