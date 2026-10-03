"""#53: read Delta tables that delta-rs cannot (deletion vectors, column mapping) with DuckDB
``delta_scan``.

The tables are committed fixtures written by Apache Spark 4.2.0 with Delta Lake 4.4.0 (see
``tests/fixtures/delta_fallback/README.md``): ``dv`` (deletion vectors; 0 create, 1 insert 100
rows, 2 delete the rows whose id is a multiple of 10), ``cm`` (column mapping) and ``plain``.

The ``deltalake`` pinned in the dev environment (1.6.x) reads column mapping itself and fails
only on deletion vectors. Releases before it fail on column mapping too (0.25.5 raises the
message used in ``OLD_COLUMN_MAPPING_ERROR``), so that case is tested with the real fallback
reader plus ``deltalake`` made to raise what the older release raised.
"""

from __future__ import annotations

import json
import shutil
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.profile.reference import delta_fallback as fb
from shape.profile.reference.sources import SourceError, read_delta

deltalake = pytest.importorskip("deltalake")
duckdb = pytest.importorskip("duckdb")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "delta_fallback"

# What deltalake 0.25.5 raised when reading the `cm` fixture (measured, not invented).
OLD_COLUMN_MAPPING_ERROR = (
    "The table's minimum reader version is 2 but deltalake only supports version 1 or 3 "
    "with these reader features: {'timestampNtz'}"
)


@pytest.fixture(scope="module")
def _delta_extension():
    """The fallback needs DuckDB's ``delta`` extension (downloaded on first use). Only the tests
    that read through DuckDB use it, so the others run with no network (#331)."""
    try:
        con = duckdb.connect()
        try:
            con.execute("INSTALL delta")
            con.execute("LOAD delta")
        finally:
            con.close()
    except Exception as exc:  # pragma: no cover - offline machines
        pytest.fail(f"DuckDB's delta extension is not available: {exc}")


def _table(name: str) -> Path:
    return FIXTURES / name


def _commit_time(path: Path, version: int) -> datetime:
    line = (path / "_delta_log" / f"{version:020d}.json").read_text().splitlines()[0]
    return datetime.fromtimestamp(json.loads(line)["commitInfo"]["timestamp"] / 1000, UTC)


@pytest.fixture
def dv_dated(tmp_path):
    """A copy of ``dv`` whose log files carry their commit times as modification times, as a
    table does where it was written (delta-rs resolves ``as_of`` from them; a git checkout
    resets them)."""
    import os

    path = tmp_path / "dv"
    shutil.copytree(_table("dv"), path)
    for version in range(3):
        stamp = _commit_time(path, version).timestamp()
        os.utime(path / "_delta_log" / f"{version:020d}.json", (stamp, stamp))
    return path


def _ids(table: pa.Table) -> list[int]:
    return sorted(table.column("id").to_pylist())


# --- delta-rs, as is -------------------------------------------------------------------


def test_delta_rs_cannot_read_deletion_vectors():
    with pytest.raises(deltalake.exceptions.DeltaProtocolError, match="deletionVectors"):
        deltalake.DeltaTable(str(_table("dv"))).to_pyarrow_table()


def test_the_fixtures_use_the_features_they_claim():
    dv = deltalake.DeltaTable(str(_table("dv"))).protocol()
    assert "deletionVectors" in (dv.reader_features or [])
    cm = deltalake.DeltaTable(str(_table("cm"))).metadata().configuration
    assert cm["delta.columnMapping.mode"] == "name"
    assert list((_table("dv")).glob("deletion_vector_*.bin"))


# --- the error without the extra ---------------------------------------------------------


def test_without_duckdb_the_error_names_the_feature_and_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(fb.UnsupportedDeltaFeature) as err:
        shape.profile(_table("dv"))
    text = str(err.value)
    assert "deletionVectors" in text
    assert "pip install 'sqllocks-shape[delta-fallback]'" in text
    assert "dv" in text
    assert isinstance(err.value, ValueError)


def test_without_duckdb_the_cli_reports_it_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    rc = main(["profile", str(_table("dv")), "-o", str(tmp_path / "p.shape")])
    err = capsys.readouterr().err
    assert rc != 0
    assert "deletionVectors" in err and "sqllocks-shape[delta-fallback]" in err


def test_a_duckdb_without_the_delta_extension_says_how_to_get_it(monkeypatch):
    class _Con:
        def execute(self, sql, *a):
            if "delta" in sql.lower() and "scan" not in sql.lower():
                raise RuntimeError("IO Error: could not download extension")
            raise AssertionError(sql)

        def close(self):
            pass

    fake = types.SimpleNamespace(connect=lambda *a, **k: _Con(), __version__="0")
    monkeypatch.setitem(sys.modules, "duckdb", fake)
    with pytest.raises(fb.UnsupportedDeltaFeature, match="INSTALL delta"):
        shape.profile(_table("dv"))


# --- the fallback reads the right rows ---------------------------------------------------


@pytest.mark.usefixtures("_delta_extension")
def test_deletion_vectors_are_honoured(capsys):
    table, prov = read_delta(_table("dv"))
    assert table.num_rows == 90
    assert not any(i % 10 == 0 for i in _ids(table))
    assert _ids(table) == [i for i in range(100) if i % 10]
    assert prov["reader"] == "duckdb"
    assert prov["version"] == 2


@pytest.mark.usefixtures("_delta_extension")
def test_the_notice_goes_to_stderr_and_names_the_feature(capsys):
    shape.profile(_table("dv"))
    out, err = capsys.readouterr()
    assert out == ""
    assert "deletionVectors" in err
    assert "DuckDB" in err and "delta_scan" in err
    assert err.count("\n") == 1  # one line, once per read


@pytest.mark.usefixtures("_delta_extension")
def test_provenance_records_the_fallback_and_why():
    prof = shape.profile(_table("dv"))
    prov = prof.provenance
    assert prov["format"] == "delta" and prov["version"] == 2
    assert prov["reader"] == "duckdb"
    assert prov["reader_features"] == ["deletionVectors"]
    assert "deletionVectors" in prov["fallback_reason"]
    assert prov["as_of"] is None


@pytest.mark.usefixtures("_delta_extension")
def test_provenance_survives_save_and_load(tmp_path):
    prof = shape.profile(_table("dv"))
    shape.save(prof, tmp_path / "dv.shape")
    assert shape.load(tmp_path / "dv.shape").provenance == prof.provenance


@pytest.mark.usefixtures("_delta_extension")
def test_the_profile_counts_only_live_rows():
    d = shape.profile(_table("dv")).to_dict()
    assert d["row_count"] == 90
    assert d["columns"]["id"]["max_value"] == ["int", 99]
    assert d["columns"]["id"]["min_value"] == ["int", 1]


@pytest.mark.usefixtures("_delta_extension")
@pytest.mark.parametrize(("version", "rows"), [(0, 0), (1, 100), (2, 90)])
def test_version_is_honoured_by_the_fallback(version, rows):
    table, prov = read_delta(_table("dv"), version=version)
    assert table.num_rows == rows
    assert prov["version"] == version and prov["reader"] == "duckdb"


@pytest.mark.usefixtures("_delta_extension")
def test_as_of_is_honoured_by_the_fallback(dv_dated):
    dv = dv_dated
    table, prov = read_delta(dv, as_of=_commit_time(dv, 1))
    assert table.num_rows == 100 and prov["version"] == 1
    assert prov["as_of"] == _commit_time(dv, 1).isoformat()
    table, prov = read_delta(dv, as_of=_commit_time(dv, 2))
    assert table.num_rows == 90 and prov["version"] == 2


def test_as_of_before_the_first_commit_is_an_error(dv_dated):
    dv = dv_dated
    with pytest.raises(SourceError, match="no version"):
        read_delta(dv, as_of=datetime(2000, 1, 1, tzinfo=UTC))


def test_a_version_that_does_not_exist_is_an_error():
    with pytest.raises(SourceError, match="does not exist"):
        read_delta(_table("dv"), version=9)


def test_version_and_as_of_together_are_refused_before_any_read():
    with pytest.raises(ValueError, match="not both"):
        read_delta(_table("dv"), version=1, as_of="2030-01-01T00:00:00Z")


@pytest.mark.usefixtures("_delta_extension")
def test_the_cli_profiles_a_deletion_vector_table(tmp_path, capsys):
    out = tmp_path / "dv.shape"
    assert main(["profile", str(_table("dv")), "-o", str(out)]) == 0
    captured = capsys.readouterr()
    assert "deletionVectors" in captured.err
    doc = json.loads(captured.out)
    assert doc["provenance"]["reader"] == "duckdb"
    assert shape.load(out).to_dict()["row_count"] == 90


@pytest.mark.usefixtures("_delta_extension")
def test_the_cli_honours_version(tmp_path, capsys):
    out = tmp_path / "dv1.shape"
    assert main(["profile", str(_table("dv")), "--version", "1", "-o", str(out)]) == 0
    assert shape.load(out).to_dict()["row_count"] == 100


# --- column mapping ---------------------------------------------------------------------
#
# deltalake 1.6.x does not refuse a column-mapped table: it returns the right number of rows
# with every column null. Older releases raise (OLD_COLUMN_MAPPING_ERROR). Both are covered:
# the table's configuration sends it to the fallback before delta-rs is asked.


@pytest.mark.usefixtures("_delta_extension")
def test_the_fallback_reads_a_column_mapped_table_with_logical_names():
    table = fb.read_via_duckdb(str(_table("cm")))
    assert table.column_names == ["id", "full name", "amount"]
    assert table.num_rows == 100
    assert table.column("amount").to_pylist()[3] == 4.5


@pytest.mark.usefixtures("_delta_extension")
def test_a_column_mapped_table_is_never_read_with_delta_rs(capsys):
    table, prov = read_delta(_table("cm"))
    assert prov["reader"] == "duckdb" and prov["reader_features"] == ["columnMapping"]
    assert [table.column(n).null_count for n in table.column_names] == [0, 0, 0]
    assert _ids(table) == list(range(100))
    assert "columnMapping" in capsys.readouterr().err


@pytest.mark.usefixtures("_delta_extension")
def test_a_column_mapped_table_has_the_rows_of_the_same_table_without_mapping():
    mapped, _ = read_delta(_table("cm"))
    plain = deltalake.DeltaTable(str(_table("plain"))).to_pyarrow_table()
    assert mapped.column("id").to_pylist() == plain.column("id").to_pylist()
    assert mapped.column("full name").to_pylist() == plain.column("name").to_pylist()
    assert mapped.column("amount").to_pylist() == plain.column("amount").to_pylist()


@pytest.mark.usefixtures("_delta_extension")
def test_a_column_mapped_profile_is_correct_and_says_why(capsys):
    prof = shape.profile(_table("cm"))
    d = prof.to_dict()
    assert d["row_count"] == 100 and list(d["columns"]) == ["id", "full name", "amount"]
    assert d["columns"]["id"]["null_count"] == 0
    assert prof.provenance["reader"] == "duckdb"
    assert "columnMapping" in prof.provenance["fallback_reason"]


def test_a_column_mapped_table_without_duckdb_is_an_error_not_null_columns(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(fb.UnsupportedDeltaFeature, match="columnMapping") as err:
        shape.profile(_table("cm"))
    assert "sqllocks-shape[delta-fallback]" in str(err.value)


@pytest.mark.usefixtures("_delta_extension")
def test_an_old_delta_rs_failing_on_column_mapping_is_covered_too(monkeypatch):
    def old_read(self, *a, **k):
        raise deltalake.exceptions.DeltaProtocolError(OLD_COLUMN_MAPPING_ERROR)

    assert fb.is_unsupported_feature_error(
        deltalake.exceptions.DeltaProtocolError(OLD_COLUMN_MAPPING_ERROR)
    )
    monkeypatch.setattr(deltalake.DeltaTable, "to_pyarrow_table", old_read)
    prof = shape.profile(_table("cm"))
    assert prof.to_dict()["row_count"] == 100
    assert prof.provenance["reader_features"] == ["columnMapping"]


# --- same table, same profile ------------------------------------------------------------


@pytest.mark.usefixtures("_delta_extension")
def test_both_readers_give_the_same_profile(tmp_path, capsys):
    path = _table("plain")
    via_delta_rs = shape.profile(path, name="t")
    assert "reader" not in (via_delta_rs.provenance or {})
    via_duckdb = shape.profile(fb.read_via_duckdb(str(path)), name="t")
    assert via_delta_rs == via_duckdb
    id_a = shape.save(via_delta_rs, tmp_path / "a.shape")
    id_b = shape.save(via_duckdb, tmp_path / "b.shape")
    assert id_a == id_b
    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("_delta_extension")
def test_both_readers_agree_on_a_table_with_every_common_type(tmp_path):
    import datetime as dt

    data = pa.table(
        {
            "i": [1, 2, None],
            "s": ["a", "b", None],
            "f": [1.5, 2.5, None],
            "b": [True, False, None],
            "d": [dt.date(2020, 1, 1), None, dt.date(2021, 1, 1)],
            "ts": pa.array([dt.datetime(2020, 1, 1, 1), None, dt.datetime(2021, 1, 1)]),
            "dec": pa.array([1, 2, None], pa.decimal128(10, 2)),
        }
    )
    path = tmp_path / "t"
    deltalake.write_deltalake(str(path), data)
    a = shape.profile(path, name="t")
    b = shape.profile(fb.read_via_duckdb(str(path)), name="t")
    assert a == b
    assert shape.save(a, tmp_path / "a.shape") == shape.save(b, tmp_path / "b.shape")


def test_a_table_delta_rs_reads_never_touches_duckdb(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "duckdb", None)  # an import would raise
    prof = shape.profile(_table("plain"))
    assert prof.to_dict()["row_count"] == 100
    assert "reader" not in prof.provenance
    assert capsys.readouterr().err == ""


def test_other_errors_are_not_swallowed(monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(deltalake.DeltaTable, "to_pyarrow_table", boom)
    with pytest.raises(RuntimeError, match="disk on fire"):
        shape.profile(_table("plain"))


@pytest.mark.usefixtures("_delta_extension")
def test_a_different_unsupported_reader_feature_falls_back_too(monkeypatch, capsys):
    def v2(self, *a, **k):
        raise deltalake.exceptions.DeltaProtocolError(
            "The table has set these reader features: {'v2Checkpoint'} but these are not yet "
            "supported by the deltalake reader."
        )

    monkeypatch.setattr(deltalake.DeltaTable, "to_pyarrow_table", v2)
    prof = shape.profile(_table("plain"))
    assert prof.to_dict()["row_count"] == 100
    assert "v2Checkpoint" in capsys.readouterr().err
    assert "v2Checkpoint" in prof.provenance["reader_features"]


def test_the_extra_is_declared_and_duckdb_is_not_a_core_dependency():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    meta = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert any(d.startswith("duckdb") for d in meta["optional-dependencies"]["delta-fallback"])
    assert not any("duckdb" in d for d in meta["dependencies"])


# --- the source plugin (local and delta+abfss) -------------------------------------------


@pytest.mark.usefixtures("_delta_extension")
def test_the_delta_source_plugin_reads_a_deletion_vector_table(capsys):
    from shape.builtins.sources.delta import DeltaSource

    src = DeltaSource()
    uri = str(_table("dv"))
    assert src.can_open(uri)
    batches = list(src.read(uri))
    table = pa.Table.from_batches(batches)
    assert table.num_rows == 90
    assert "deletionVectors" in capsys.readouterr().err
    assert src.schema(uri).names == ["id", "name", "amount"]
    assert pa.Table.from_batches(list(src.read(uri, version=1))).num_rows == 100
    cols = pa.Table.from_batches(list(src.read(uri, columns=["id"])))
    assert cols.column_names == ["id"] and cols.num_rows == 90


def test_the_delta_source_plugin_without_duckdb_names_the_extra(monkeypatch):
    from shape.builtins.sources.delta import DeltaSource

    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(fb.UnsupportedDeltaFeature, match=r"delta-fallback"):
        list(DeltaSource().read(str(_table("dv"))))


class _Recorder:
    """A stand-in DuckDB connection that records the SQL it is given."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str, *params: Any):
        self.sql.append(sql)
        return self

    def to_arrow_table(self):
        return pa.table({"id": [1]})

    fetch_arrow_table = to_arrow_table
    arrow = to_arrow_table

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("opts", "needle"),
    [
        ({"azure_storage_account_key": "KEY123"}, "AccountKey=KEY123"),
        ({"azure_storage_sas_key": "sv=1&sig=abc"}, "SharedAccessSignature=sv=1&sig=abc"),
        ({"azure_storage_token": "TOKEN123"}, "ACCESS_TOKEN 'TOKEN123'"),
    ],
)
def test_cloud_reads_carry_the_existing_auth_into_duckdb(monkeypatch, opts, needle):
    rec = _Recorder()
    monkeypatch.setitem(
        sys.modules, "duckdb", types.SimpleNamespace(connect=lambda *a, **k: rec, __version__="0")
    )
    uri = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Tables/events"
    fb.read_via_duckdb(uri, version=3, storage_options=opts)
    joined = "\n".join(rec.sql)
    assert "CREATE" in joined and "SECRET" in joined.upper()
    assert needle in joined
    scan = [s for s in rec.sql if "delta_scan" in s][0]
    assert uri in scan and "version=3" in scan.replace(" ", "")
    assert any("azure" in s.lower() and "load" in s.lower() for s in rec.sql)


def test_a_quote_in_a_credential_cannot_break_out_of_the_secret(monkeypatch):
    rec = _Recorder()
    monkeypatch.setitem(
        sys.modules, "duckdb", types.SimpleNamespace(connect=lambda *a, **k: rec, __version__="0")
    )
    fb.read_via_duckdb(
        "abfss://c@acct.dfs.core.windows.net/t",
        storage_options={"azure_storage_token": "x'); DROP TABLE y; --"},
    )
    secret = [s for s in rec.sql if "SECRET" in s.upper()][0]
    assert "x''); DROP TABLE y; --" in secret


def test_a_local_path_with_a_quote_is_escaped(monkeypatch, tmp_path):
    rec = _Recorder()
    monkeypatch.setitem(
        sys.modules, "duckdb", types.SimpleNamespace(connect=lambda *a, **k: rec, __version__="0")
    )
    odd = tmp_path / "it's"
    shutil.copytree(_table("dv"), odd)
    fb.read_via_duckdb(str(odd))
    assert "it''s" in [s for s in rec.sql if "delta_scan" in s][0]
