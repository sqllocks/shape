"""W5-10 item 1: the file-footer fingerprint (embed, show, verify, the sinks' option)."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape import fingerprint
from shape.cli.main import main
from shape.repro import dataset_id

deltalake = pytest.importorskip("deltalake")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def write_parquet(path: Path, table: pa.Table, **kw: Any) -> Path:
    pq.write_table(table, path, **kw)
    return path


def write_delta(path: Path, table: pa.Table) -> Path:
    deltalake.write_deltalake(str(path), table)
    return path


def key_files(tmp_path: Path, keypair) -> tuple[Path, Path]:
    private, public = keypair
    sk, pk = tmp_path / "k.key", tmp_path / "k.pub"
    sk.write_text(base64.b64encode(private).decode())
    sk.chmod(0o600)  # a private key file is owner-only (AUD-privacy)
    pk.write_text(base64.b64encode(public).decode())
    return sk, pk


# ---- the document ---------------------------------------------------------------------------


def test_build_has_exactly_the_documented_fields(small_table, repro):
    doc = fingerprint.build("people", small_table, dataset_id="sha256:x", reproducibility=repro)
    assert set(doc) == {
        "format",
        "version",
        "synthetic",
        "table",
        "table_id",
        "dataset_id",
        "profile_content_id",
        "reproducibility",
        "key_id",
        "signature",
    }
    assert doc["format"] == "shape-fingerprint" and doc["version"] == 1
    assert doc["synthetic"] is True and doc["table"] == "people"
    assert doc["table_id"] == dataset_id({"people": small_table})
    assert doc["profile_content_id"] is None and doc["signature"] is None and doc["key_id"] is None


def test_table_id_changes_with_one_value(small_table):
    other = small_table.set_column(
        1, "name", pa.array(["z"] + small_table.column("name").to_pylist()[1:])
    )
    assert fingerprint.table_id("t", small_table) != fingerprint.table_id("t", other)


def test_parse_refuses_other_formats_and_newer_versions(small_table, repro):
    doc = fingerprint.build("t", small_table, dataset_id=None, reproducibility=repro)
    assert fingerprint.parse(json.dumps(doc))["table"] == "t"
    with pytest.raises(fingerprint.FingerprintVersionError, match="newer Shape"):
        fingerprint.parse(json.dumps({**doc, "version": 2}))
    for bad in ("{", "[]", json.dumps({**doc, "format": "other"})):
        with pytest.raises(fingerprint.FingerprintError):
            fingerprint.parse(bad)
    for version in (0, "1", True, 1.5):
        with pytest.raises(fingerprint.FingerprintError):
            fingerprint.parse(json.dumps({**doc, "version": version}))
    with pytest.raises(fingerprint.FingerprintError, match="table_id"):
        fingerprint.parse(json.dumps({k: v for k, v in doc.items() if k != "table_id"}))


def test_a_float_in_the_reproducibility_tuple_is_refused(small_table):
    with pytest.raises(fingerprint.FingerprintError, match="cannot be signed"):
        fingerprint.build("t", small_table, dataset_id=None, reproducibility={"scale": 0.5})


def test_empty_and_single_row_tables_have_ids(repro):
    empty = pa.table({"a": pa.array([], pa.int64())})
    one = pa.table({"a": pa.array([1], pa.int64())})
    ids = {fingerprint.table_id("t", t) for t in (empty, one)}
    assert len(ids) == 2


# ---- Parquet --------------------------------------------------------------------------------


@pytest.fixture
def parquet_run(tmp_path, small_table, make_manifest):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    manifest = make_manifest(tmp_path / "run_manifest.json", {"people": small_table})
    return data, manifest


def test_embed_show_verify_parquet(capsys, parquet_run, small_table):
    data, manifest = parquet_run
    code, out, _ = run(capsys, "fingerprint", "embed", data, "--run", manifest)
    assert code == 0
    summary = json.loads(out)
    assert summary["table"] == "people" and summary["signed"] is False
    code, out, _ = run(capsys, "fingerprint", "show", data)
    doc = json.loads(out)
    assert doc["table_id"] == dataset_id({"people": small_table})
    assert doc["dataset_id"] == dataset_id({"people": small_table})
    assert doc["reproducibility"]["seed"] == 7
    code, out, _ = run(capsys, "fingerprint", "verify", data)
    assert code == 0 and "valid" in out
    assert pq.read_table(data).equals(small_table)  # the data is untouched


def test_embed_keeps_row_groups_and_compression_and_leaves_no_temp_file(
    tmp_path, capsys, small_table, make_manifest
):
    data = write_parquet(
        tmp_path / "people.parquet", small_table, row_group_size=10, compression="zstd"
    )
    manifest = make_manifest(tmp_path / "m.json", {"people": small_table})
    assert run(capsys, "fingerprint", "embed", data, "--run", manifest)[0] == 0
    meta = pq.read_metadata(data)
    assert meta.num_row_groups == 4
    assert meta.row_group(0).column(0).compression == "ZSTD"
    assert sorted(p.name for p in tmp_path.iterdir() if "tmp" in p.name) == []


def test_embed_failure_leaves_the_original_file(tmp_path, small_table, repro, monkeypatch):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    before = data.read_bytes()

    def boom(self, table):
        raise OSError("disk full")

    monkeypatch.setattr(pq.ParquetWriter, "write_table", boom)
    with pytest.raises(OSError, match="disk full"):
        fingerprint.embed_text(data, "{}")
    assert data.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["people.parquet"]


def test_embed_replaces_an_earlier_fingerprint(tmp_path, small_table, repro):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    for dataset in ("sha256:one", "sha256:two"):
        doc = fingerprint.build("people", small_table, dataset_id=dataset, reproducibility=repro)
        fingerprint.embed_text(data, fingerprint.dump(doc))
    assert fingerprint.read_fingerprint(data)["dataset_id"] == "sha256:two"


def test_one_changed_value_fails_verify_parquet(tmp_path, capsys, parquet_run, small_table):
    data, manifest = parquet_run
    run(capsys, "fingerprint", "embed", data, "--run", manifest)
    table = pq.read_table(data)
    names = table.column("name").to_pylist()
    names[3] = "tampered"
    changed = table.set_column(1, "name", pa.array(names))  # keeps the schema metadata
    pq.write_table(changed, data)
    assert fingerprint.read_text(data) is not None
    code, _, err = run(capsys, "fingerprint", "verify", data)
    assert code == 1 and "does NOT match" in err


def test_manifest_table_is_found_by_path_or_name(tmp_path, capsys, small_table, make_manifest):
    manifest = make_manifest(tmp_path / "m.json", {"people": small_table})
    named = write_parquet(tmp_path / "renamed.parquet", small_table)
    code, _, err = run(capsys, "fingerprint", "embed", named, "--run", manifest)
    assert code == 2 and "not a table of the run manifest" in err and "people" in err


def test_embed_needs_a_manifest_with_a_dataset_id(tmp_path, capsys, small_table, make_manifest):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    manifest = make_manifest(tmp_path / "m.json", {"people": small_table}, dataset_id="")
    code, _, err = run(capsys, "fingerprint", "embed", data, "--run", manifest)
    assert code == 2 and "dataset_id" in err
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({"format": "shape-run-manifest", "version": 99}))
    assert run(capsys, "fingerprint", "embed", data, "--run", newer)[0] == 2


def test_no_fingerprint_exits_2(tmp_path, capsys, small_table):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    for sub in ("show", "verify"):
        code, _, err = run(capsys, "fingerprint", sub, data)
        assert code == 2 and "no shape.fingerprint" in err


def test_a_newer_version_exits_2(tmp_path, capsys, small_table, repro):
    data = write_parquet(tmp_path / "people.parquet", small_table)
    doc = fingerprint.build("people", small_table, dataset_id=None, reproducibility=repro)
    fingerprint.embed_text(data, json.dumps({**doc, "version": 2}))
    for sub in ("show", "verify"):
        code, _, err = run(capsys, "fingerprint", sub, data)
        assert code == 2 and "newer Shape" in err


def test_bad_input_exits_2(tmp_path, capsys):
    (tmp_path / "x.txt").write_text("not parquet")
    (tmp_path / "dir").mkdir()
    for target in (tmp_path / "missing.parquet", tmp_path / "x.txt", tmp_path / "dir"):
        for sub in ("show", "verify"):
            assert run(capsys, "fingerprint", sub, target)[0] == 2


# ---- signing --------------------------------------------------------------------------------


@pytest.mark.sign
def test_signed_fingerprint_verifies_with_the_right_key_only(
    tmp_path, capsys, parquet_run, keypair
):
    data, manifest = parquet_run
    sk, pk = key_files(tmp_path, keypair)
    code, out, _ = run(capsys, "fingerprint", "embed", data, "--run", manifest, "--key", sk)
    assert code == 0 and json.loads(out)["signed"] is True
    assert run(capsys, "fingerprint", "verify", data, "--public-key", pk)[0] == 0
    code, out, _ = run(capsys, "fingerprint", "verify", data)
    assert code == 0 and "not checked" in out
    from shape.artifact.signing import generate_keypair

    _, other_public = generate_keypair()
    other = tmp_path / "other.pub"
    other.write_text(base64.b64encode(other_public).decode())
    code, _, err = run(capsys, "fingerprint", "verify", data, "--public-key", other)
    assert code == 1 and "signature does NOT match" in err


@pytest.mark.sign
def test_a_signature_by_another_key_fails(tmp_path, capsys, parquet_run, keypair):
    from shape.artifact.signing import generate_keypair

    data, manifest = parquet_run
    _, pk = key_files(tmp_path, keypair)
    other_private, _ = generate_keypair()
    other_key = tmp_path / "other.key"
    other_key.write_text(base64.b64encode(other_private).decode())
    other_key.chmod(0o600)  # a private key file is owner-only (AUD-privacy)
    run(capsys, "fingerprint", "embed", data, "--run", manifest, "--key", other_key)
    assert run(capsys, "fingerprint", "verify", data, "--public-key", pk)[0] == 1


@pytest.mark.sign
def test_an_edited_field_fails_the_signature(tmp_path, capsys, parquet_run, keypair):
    data, manifest = parquet_run
    sk, pk = key_files(tmp_path, keypair)
    run(capsys, "fingerprint", "embed", data, "--run", manifest, "--key", sk)
    doc = fingerprint.read_fingerprint(data)
    doc["dataset_id"] = "sha256:" + "0" * 64  # the digest of the data still matches
    fingerprint.embed_text(data, fingerprint.dump(doc))
    code, _, err = run(capsys, "fingerprint", "verify", data, "--public-key", pk)
    assert code == 1 and "table_id matches" in err and "signature does NOT match" in err
    assert run(capsys, "fingerprint", "verify", data)[0] == 0  # no key: not checked


@pytest.mark.sign
def test_a_public_key_with_an_unsigned_fingerprint_fails(tmp_path, capsys, parquet_run, keypair):
    data, manifest = parquet_run
    _, pk = key_files(tmp_path, keypair)
    run(capsys, "fingerprint", "embed", data, "--run", manifest)
    code, _, err = run(capsys, "fingerprint", "verify", data, "--public-key", pk)
    assert code == 1 and "not signed" in err


def test_signing_without_the_sign_extra_exits_2_naming_it(
    tmp_path, capsys, parquet_run, monkeypatch
):
    data, manifest = parquet_run
    key = tmp_path / "k.key"
    key.write_text(base64.b64encode(bytes(range(32))).decode())
    monkeypatch.setitem(sys.modules, "cryptography", None)
    code, _, err = run(capsys, "fingerprint", "embed", data, "--run", manifest, "--key", key)
    assert code == 2 and "sqllocks-shape[sign]" in err
    assert fingerprint.read_text(data) is None  # nothing was written


# ---- Delta ----------------------------------------------------------------------------------


@pytest.fixture
def delta_run(tmp_path, small_table, make_manifest):
    table = write_delta(tmp_path / "people", small_table)
    manifest = make_manifest(tmp_path / "run_manifest.json", {"people": small_table})
    return table, manifest


def test_embed_show_verify_delta(capsys, delta_run, small_table):
    table, manifest = delta_run
    version = deltalake.DeltaTable(str(table)).version()
    code, out, _ = run(capsys, "fingerprint", "embed", table, "--run", manifest)
    assert code == 0 and json.loads(out)["kind"] == "delta"
    dt = deltalake.DeltaTable(str(table))
    assert dt.version() == version + 1  # one new commit
    assert "shape.fingerprint" in dt.metadata().configuration
    assert dt.to_pyarrow_table().num_rows == small_table.num_rows
    code, out, _ = run(capsys, "fingerprint", "show", table)
    assert json.loads(out)["table_id"] == dataset_id({"people": small_table})
    assert run(capsys, "fingerprint", "verify", table)[0] == 0


def test_delta_embed_keeps_the_tables_other_configuration(tmp_path, small_table, repro):
    table = tmp_path / "people"
    deltalake.write_deltalake(
        str(table), small_table, configuration={"delta.logRetentionDuration": "interval 30 days"}
    )
    fingerprint.embed_text(table, "{}")
    config = deltalake.DeltaTable(str(table)).metadata().configuration
    assert config["delta.logRetentionDuration"] == "interval 30 days"
    assert config["shape.fingerprint"] == "{}"


def test_delta_embed_twice_replaces_and_reads_back(tmp_path, small_table, repro):
    table = write_delta(tmp_path / "people", small_table)
    for dataset in ("sha256:one", "sha256:two"):
        doc = fingerprint.build("people", small_table, dataset_id=dataset, reproducibility=repro)
        fingerprint.embed_text(table, fingerprint.dump(doc))
    assert fingerprint.read_fingerprint(table)["dataset_id"] == "sha256:two"
    assert deltalake.DeltaTable(str(table)).to_pyarrow_table().num_rows == small_table.num_rows


def test_one_changed_value_fails_verify_delta(tmp_path, capsys, delta_run, small_table):
    table, manifest = delta_run
    run(capsys, "fingerprint", "embed", table, "--run", manifest)
    changed = small_table.slice(0, 1)
    deltalake.write_deltalake(str(table), changed, mode="append")  # the property survives
    code, _, err = run(capsys, "fingerprint", "verify", table)
    assert code == 1 and "does NOT match" in err


@pytest.mark.sign
def test_signed_delta_fingerprint(tmp_path, capsys, delta_run, keypair):
    table, manifest = delta_run
    sk, pk = key_files(tmp_path, keypair)
    assert run(capsys, "fingerprint", "embed", table, "--run", manifest, "--key", sk)[0] == 0
    assert run(capsys, "fingerprint", "verify", table, "--public-key", pk)[0] == 0


def test_delta_without_fingerprint_exits_2(tmp_path, capsys, small_table):
    table = write_delta(tmp_path / "people", small_table)
    assert run(capsys, "fingerprint", "verify", table)[0] == 2


# ---- the sinks' option ----------------------------------------------------------------------

TYPED = pa.table(
    {
        "i": pa.array([1, None, 3], pa.int32()),
        "f": pa.array([0.5, float("nan"), None], pa.float64()),
        "s": pa.array(["a", None, "c"], pa.string()),
        "b": pa.array([True, False, None], pa.bool_()),
        "d": pa.array([None, 1, 2], pa.date32()),
        "t": pa.array([1_700_000_000_000_000, None, 5], pa.timestamp("us")),
        "tz": pa.array([1, 2, None], pa.timestamp("us", tz="UTC")),
    }
)


@pytest.mark.parametrize(
    "table", [TYPED, TYPED.slice(0, 0), TYPED.slice(0, 1)], ids=["3", "0", "1"]
)
def test_parquet_sink_fingerprint_verifies_for_typed_tables(tmp_path, table):
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "parquet")
    path = tmp_path / "t.parquet"
    sink.write(str(path), "t", iter(table.to_batches()), schema=table.schema, fingerprint=True)
    outcome = fingerprint.verify(path)
    assert outcome.ok and outcome.doc["table"] == "t" and outcome.doc["dataset_id"] is None
    assert pq.read_table(path).num_rows == table.num_rows


@pytest.mark.parametrize("table", [TYPED, TYPED.slice(0, 1)], ids=["3", "1"])
def test_delta_sink_fingerprint_verifies_for_typed_tables(tmp_path, table):
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "delta")
    sink.write(str(tmp_path), "t", iter(table.to_batches()), schema=table.schema, fingerprint=True)
    outcome = fingerprint.verify(tmp_path / "t")
    assert outcome.ok


def test_sink_context_is_recorded(tmp_path, small_table, repro):
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "parquet")
    context = {"dataset_id": "sha256:run", "reproducibility": repro, "profile_content_id": "abc"}
    path = tmp_path / "t.parquet"
    sink.write(str(path), "t", iter(small_table.to_batches()), fingerprint=context)
    doc = fingerprint.read_fingerprint(path)
    assert doc["dataset_id"] == "sha256:run" and doc["profile_content_id"] == "abc"
    assert doc["reproducibility"] == repro


def test_sink_fingerprint_option_boundaries(tmp_path, small_table):
    from shape.plugins.host import default_host

    parquet = default_host().get("shape.sinks", "parquet")
    delta = default_host().get("shape.sinks", "delta")
    batches = small_table.to_batches()
    with pytest.raises(ValueError, match="rolling"):
        parquet.write(str(tmp_path), "t", iter(batches), fingerprint=True, roll_rows=5)
    with pytest.raises(ValueError, match="mode overwrite"):
        delta.write(str(tmp_path), "t", iter(batches), fingerprint=True, mode="append")
    with pytest.raises(ValueError, match="micro-batch"):
        delta.write(str(tmp_path), "t", iter(batches), fingerprint=True, commit_rows=5)
    with pytest.raises(ValueError, match="local"):
        delta.write(
            "delta+abfss://c@h.dfs.core.windows.net/f", "t", iter(batches), fingerprint=True
        )
    off = tmp_path / "plain.parquet"
    parquet.write(str(off), "t", iter(batches))
    assert fingerprint.read_text(off) is None  # off by default


def test_generate_fingerprint_parquet_and_delta(tmp_path, capsys, schema_file):
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    for fmt, out in (("parquet", tmp_path / "p"), ("delta", tmp_path / "d")):
        code, _, err = run(capsys, "generate", schema_file, "-f", fmt, "-o", out, "--fingerprint")
        assert code == 0, err
    result = Engine(load_target(str(schema_file))).generate()
    expected = dataset_id({n: result.tables[n] for n in result.generation_order})
    for name in ("customer", "order"):
        for target in (tmp_path / "p" / f"{name}.parquet", tmp_path / "d" / name):
            doc = fingerprint.read_fingerprint(target)
            assert doc["table"] == name and doc["dataset_id"] == expected
            assert doc["table_id"] == dataset_id({name: result.tables[name]})
            assert doc["reproducibility"]["seed"] == 7
            assert fingerprint.verify(target).ok
            assert run(capsys, "fingerprint", "verify", target)[0] == 0


def test_generate_fingerprint_is_deterministic(tmp_path, capsys, schema_file):
    for out in ("a", "b"):
        assert (
            run(
                capsys,
                "generate",
                schema_file,
                "-f",
                "parquet",
                "-o",
                tmp_path / out,
                "--fingerprint",
            )[0]
            == 0
        )
    assert fingerprint.read_text(tmp_path / "a" / "order.parquet") == fingerprint.read_text(
        tmp_path / "b" / "order.parquet"
    )


def test_generate_without_the_flag_writes_none(tmp_path, capsys, schema_file):
    assert run(capsys, "generate", schema_file, "-f", "parquet", "-o", tmp_path / "o")[0] == 0
    assert fingerprint.read_text(tmp_path / "o" / "order.parquet") is None


@pytest.mark.parametrize(
    "extra", [["-f", "csv"], ["-f", "summary"], ["-f", "parquet", "--scale-mode", "local_single"]]
)
def test_generate_fingerprint_is_refused_for_other_outputs(tmp_path, capsys, schema_file, extra):
    code, _, err = run(
        capsys, "generate", schema_file, "-o", tmp_path / "o", "--fingerprint", *extra
    )
    assert code == 2 and "--fingerprint" in err


def test_embed_with_a_profile_records_its_content_id(tmp_path, capsys, parquet_run, small_table):
    data, manifest = parquet_run
    profile = tmp_path / "p.shape"
    assert main(["profile", str(data), "-o", str(profile)]) == 0
    capsys.readouterr()
    from shape.artifact.io import read_artifact

    expected = read_artifact(profile, notice=False)[0]["shape_content_id"]
    assert (
        run(capsys, "fingerprint", "embed", data, "--run", manifest, "--profile", profile)[0] == 0
    )
    assert fingerprint.read_fingerprint(data)["profile_content_id"] == expected
    code, _, err = run(
        capsys, "fingerprint", "embed", data, "--run", manifest, "--profile", tmp_path / "x.shape"
    )
    assert code == 2


def test_a_concurrent_commit_is_not_overwritten(tmp_path, small_table, monkeypatch):
    """The commit file is created exclusively: a writer that commits between the read of the log
    and the embed makes the embed retry on the new version."""
    table = write_delta(tmp_path / "people", small_table)
    real = fingerprint._latest_metadata
    raced = []

    def racing(log, dt):
        meta = real(log, dt)
        if not raced:
            raced.append(1)
            deltalake.write_deltalake(str(table), small_table, mode="append")
        return meta

    monkeypatch.setattr(fingerprint, "_latest_metadata", racing)
    version = fingerprint.set_delta_property(table, fingerprint.KEY, "{}")
    dt = deltalake.DeltaTable(str(table))
    assert dt.version() == version == 2
    assert dt.to_pyarrow_table().num_rows == 2 * small_table.num_rows
    assert dt.metadata().configuration[fingerprint.KEY] == "{}"


def test_duckdb_still_reads_a_table_with_the_property(tmp_path, small_table):
    duckdb = pytest.importorskip("duckdb")
    table = write_delta(tmp_path / "people", small_table)
    fingerprint.embed_text(table, "{}")
    con = duckdb.connect()
    try:
        con.sql("INSTALL delta")
        con.sql("LOAD delta")
    except Exception:
        pytest.skip("the DuckDB delta extension is not available")
    assert con.sql(f"SELECT count(*) FROM delta_scan('{table}')").fetchone() == (
        small_table.num_rows,
    )


def test_generate_fingerprint_is_refused_with_to_and_landing(tmp_path, capsys, schema_file):
    base = ["generate", schema_file, "-f", "parquet", "-o", tmp_path / "o", "--fingerprint"]
    code, _, err = run(capsys, *base, "--to", str(tmp_path / "t"))
    assert code == 2 and "--to" in err
    code, _, err = run(capsys, *base, "--path-template", "{table}/x.parquet")
    assert code == 2 and "landing" in err
