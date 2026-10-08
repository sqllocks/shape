"""``shape fabric publish`` (alias ``publish``): generate a domain and publish it, end to end.

Destinations are the in-repo fakes (a SQL Server that speaks the statements the writers send, a
Kusto service, an in-memory OneLake) and local folders; sign-in is the fake identity. The
destinations' own recorded conversations are pinned in ``test_recorded.py``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from shape_fabric import _tsql, kusto
from shape_fabric.testing import (
    FAKE_ENTRA_TOKEN,
    FakeIdentity,
    FakeKusto,
    FakeSqlServer,
    MemoryFS,
)

from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from refengine_name import names_refengine  # noqa: E402

pytestmark = pytest.mark.contract


CS = "Driver={ODBC Driver 18 for SQL Server};Server=db.example.test;Database=d"
ROWS = {"customer": 30, "order": 200}
RETAIL_SMALL = {
    "customer": 1000,
    "product_category": 50,
    "promotion": 200,
    "store": 150,
    "address": 1500,
    "product": 500,
    "order": 5000,
    "order_line": 12500,
    "return": 850,
}


def _col(name, strategy, type_="integer", **gen):
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "domain": "tiny", "seed": 5},
    "tables": {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {"customer_id": _col("customer_id", "sequence")},
        },
        "order": {
            "name": "order",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": _col("order_id", "sequence"),
                "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
            },
        },
    },
    "relationships": [
        {
            "name": "o_c",
            "parent": "customer",
            "child": "order",
            "parent_columns": ["customer_id"],
            "child_columns": ["customer_id"],
        }
    ],
    "generation": {"scale": "small", "scales": {"small": ROWS}},
}


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_LAKEHOUSE_PATH", raising=False)
    monkeypatch.delenv("SHAPE_SQL_CONNECTION", raising=False)
    monkeypatch.delenv("SHAPE_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("SHAPE_LAKEHOUSE_ID", raising=False)
    monkeypatch.setattr("shape_fabric.auth._notebookutils", lambda: None)
    schema = tmp_path / "tiny.json"
    schema.write_text(json.dumps(DOC))

    class World:
        pass

    w = World()
    w.tmp, w.schema, w.monkeypatch = tmp_path, str(schema), monkeypatch
    w.identity = FakeIdentity().install(monkeypatch)
    w.fs = MemoryFS()
    w.server = FakeSqlServer(w.fs)
    w.connections = []

    def connect(connection_string, credential=None, **kw):
        w.connections.append((connection_string, credential))
        return w.server.connect(connection_string, credential, **kw)

    monkeypatch.setattr(_tsql, "connect", connect)
    monkeypatch.setattr("shape.builtins.sources.azure._filesystem", lambda loc, options: w.fs)

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    w.run = run
    return w


def landing(root: Path, domain: str, table: str, name: str = "part-0001.parquet") -> Path:
    return root / "landing" / domain / table / "dt=latest" / name


# --- lakehouse -----------------------------------------------------------------------------


def test_lakehouse_writes_the_landing_zone_and_the_manifest(world):
    root = world.tmp / "lh"
    code, out, err = world.run("publish", "retail", "-t", "lakehouse", "--base-path", str(root))
    assert (code, err) == (0, "")
    for table, rows in RETAIL_SMALL.items():
        path = landing(root, "retail", table)
        assert pq.read_metadata(path).num_rows == rows, table
        assert f"{table}: {rows:,} rows → {path}" in out
    assert "Published 9 tables to lakehouse." in out and "Publish complete." in out
    manifest_path = root / "landing" / "retail" / "manifest" / "_control" / "run_manifest.json"
    assert f"Manifest: {manifest_path}" in out
    manifest = json.loads(manifest_path.read_text())
    assert set(manifest) == {
        "run_id", "spec_hash", "pack_id", "domain", "scale", "seed", "engine_version", "outputs",
        "tables", "validation", "chaos", "timestamps", "workspace_id", "lakehouse_id", "sbom",
        # the run manifest's declaration (W1-01) and reproducibility fields (W1-03)
        "format", "version", "shape_version", "min_shape_version", "reproducibility",
        "dataset_id",
    }  # fmt: skip
    assert re.fullmatch(r"\d{8}_\d{6}_retail_small_s42", manifest["run_id"])
    assert (manifest["domain"], manifest["scale"], manifest["seed"]) == ("retail", "small", 42)
    assert {t: v["rows"] for t, v in manifest["tables"].items()} == RETAIL_SMALL
    assert (
        manifest["tables"]["customer"]["columns"]
        == pq.read_metadata(landing(root, "retail", "customer")).num_columns
    )
    assert manifest["tables"]["customer"]["file_paths"] == [
        str(landing(root, "retail", "customer"))
    ]
    from shape import __version__

    assert manifest["engine_version"] == __version__
    assert manifest["sbom"]["sqllocks-shape"] == __version__
    assert set(manifest["timestamps"]) == {"started", "finished", "elapsed_seconds"}


@pytest.mark.parametrize("fmt, name", [("csv", "part-0001.csv"), ("jsonl", "part-0001.jsonl")])
def test_lakehouse_formats(world, fmt, name):
    root = world.tmp / "lh"
    code, out, _ = world.run(
        "publish", world.schema, "-t", "lakehouse", "--base-path", str(root), "--format", fmt
    )
    assert code == 0 and f"Format: {fmt}" in out
    assert landing(root, "tiny", "customer", name).is_file()
    lines = landing(root, "tiny", "order", name).read_text().splitlines()
    assert len(lines) == (ROWS["order"] if fmt == "jsonl" else ROWS["order"] + 1)


def test_lakehouse_delta_writes_delta_tables_in_a_local_folder(world):
    from deltalake import DeltaTable

    root = world.tmp / "lh"
    code, out, err = world.run(
        "publish", world.schema, "-t", "lakehouse", "--base-path", str(root), "--format", "delta"
    )
    assert code == 0, err
    table = root / "landing" / "tiny" / "order" / "dt=latest"
    assert (table / "_delta_log").is_dir()
    assert DeltaTable(str(table)).to_pyarrow_table().num_rows == ROWS["order"]
    manifest = json.loads(
        (root / "landing" / "tiny" / "manifest" / "_control" / "run_manifest.json").read_text()
    )
    assert manifest["tables"]["order"]["rows"] == ROWS["order"]


def test_lakehouse_delta_to_onelake_is_refused_before_anything_is_generated(world):
    code, out, err = world.run(
        "publish", "retail", "-t", "lakehouse", "--format", "delta",
        "--base-path", "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files",
    )  # fmt: skip
    assert code == 2 and "delta" in err and "local" in err
    assert "Generating" not in out and world.fs.files == {}


def test_dry_run_generates_and_writes_nothing(world):
    root = world.tmp / "lh"
    code, out, _ = world.run(
        "publish", "retail", "-t", "lakehouse", "--base-path", str(root), "--dry-run"
    )
    assert code == 0 and "[DRY RUN]" in out and "Dry run complete. No data published." in out
    assert "TOTAL" in out and "21,750" in out
    assert not root.exists()


def test_base_path_from_the_environment(world, monkeypatch):
    monkeypatch.setenv("SHAPE_LAKEHOUSE_PATH", str(world.tmp / "env-lh"))
    assert world.run("publish", world.schema, "-t", "lakehouse")[0] == 0
    assert landing(world.tmp / "env-lh", "tiny", "customer").is_file()


def test_the_manifest_records_the_fabric_ids(world):
    root = world.tmp / "lh"
    world.run(
        "publish", world.schema, "-t", "lakehouse", "--base-path", str(root),
        "--workspace-id", "ws-1", "--lakehouse-id", "lh-1",
    )  # fmt: skip
    manifest = json.loads(
        (root / "landing" / "tiny" / "manifest" / "_control" / "run_manifest.json").read_text()
    )
    assert (manifest["workspace_id"], manifest["lakehouse_id"]) == ("ws-1", "lh-1")


def test_publishing_again_replaces_the_files(world):
    root = world.tmp / "lh"
    args = ("publish", world.schema, "-t", "lakehouse", "--base-path", str(root))
    assert world.run(*args)[0] == 0
    assert world.run(*args)[0] == 0
    assert pq.read_metadata(landing(root, "tiny", "order")).num_rows == ROWS["order"]


def test_the_same_seed_gives_the_same_data_and_another_seed_does_not(world):
    def tables(seed, folder):
        world.run(
            "publish", "retail", "-t", "lakehouse", "--base-path", str(world.tmp / folder),
            "--seed", str(seed),
        )  # fmt: skip
        return pq.read_table(landing(world.tmp / folder, "retail", "customer"))

    assert tables(7, "a").equals(tables(7, "b"))
    assert not tables(7, "a").equals(tables(8, "c"))


def test_star_mode_publishes_the_star_schema(world):
    root = world.tmp / "lh"
    code, out, err = world.run(
        "publish", "retail", "-t", "lakehouse", "--base-path", str(root), "--mode", "star"
    )
    assert code == 0, err
    landed = {p.name for p in (root / "landing" / "retail").iterdir()}
    assert "manifest" in landed and len(landed) > 1


def test_onelake_paths_are_written_through_the_remote_storage(world):
    base = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files"
    code, out, err = world.run(
        "publish", world.schema, "-t", "lakehouse", "--base-path", base, "--auth", "cli"
    )
    assert code == 0, err
    names = sorted(world.fs.files)
    assert any(n.endswith("landing/tiny/customer/dt=latest/part-0001.parquet") for n in names)
    assert any(n.endswith("landing/tiny/manifest/_control/run_manifest.json") for n in names)
    assert any(c.get("credential") == "AzureCliCredential" for c in world.identity.calls)


def test_the_alias_and_the_subcommand_publish_the_same_files(world):
    a, b = world.tmp / "a", world.tmp / "b"
    world.run("publish", world.schema, "-t", "lakehouse", "--base-path", str(a), "--seed", "3")
    world.run(
        "fabric", "publish", world.schema, "-t", "lakehouse", "--base-path", str(b), "--seed", "3"
    )
    assert pq.read_table(landing(a, "tiny", "order")).equals(
        pq.read_table(landing(b, "tiny", "order"))
    )


@pytest.mark.parametrize(
    "argv, message",
    [
        (("publish", "nonesuch", "-t", "lakehouse", "--base-path", "x"), "nonesuch"),
        (
            ("publish", "retail", "-s", "gigantic", "-t", "lakehouse", "--base-path", "x"),
            "gigantic",
        ),
        (("publish", "retail", "-t", "lakehouse"), "--base-path"),
        (("publish", "retail", "-t", "sql-database"), "--connection-string"),
        (("publish", "retail", "-t", "warehouse", "--connection-string", CS), "--staging-path"),
        (
            (
                "publish",
                "retail",
                "-t",
                "eventhouse",
                "--connection-string",
                "https://k.example.test",
            ),
            "--database",
        ),
        (("publish", "retail", "-t", "eventhouse", "--database", "d"), "--connection-string"),
        (
            ("publish", "retail", "-t", "lakehouse", "--base-path", "x", "--batch-size", "0"),
            "batch-size",
        ),
        (("publish", "retail", "-t", "lakehouse", "--base-path", "x", "--auth", "sql"), "sql"),
    ],
)
def test_input_errors_are_exit_2_before_anything_is_generated(world, argv, message):
    code, out, err = world.run(*argv)
    assert code == 2 and message in err
    assert "Generating" not in out
    assert world.connections == [] and not (world.tmp / "x").exists()


def test_a_target_is_required_and_must_be_known(world):
    assert world.run("publish", "retail")[0] == 2
    assert world.run("publish", "retail", "-t", "mainframe")[0] == 2


# --- SQL database --------------------------------------------------------------------------


def sql_args(world, *extra):
    return ("publish", world.schema, "-t", "sql-database", *extra)


def test_sql_database_creates_the_tables_and_inserts_the_rows(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    code, out, err = world.run(*sql_args(world, "--connection-string", "env://SHAPE_TEST_CS"))
    assert code == 0, err
    assert {t: len(world.server.rows("dbo", t)) for t in ROWS} == ROWS
    assert "customer: 30 rows" in out and "order: 200 rows" in out and "Publish complete." in out
    ddl = [s for s in world.server.statements if s.startswith("CREATE TABLE")]
    assert len(ddl) == 2 and all("PRIMARY KEY" in s for s in ddl)
    (conn, credential), *_ = world.connections
    assert conn.startswith(CS) and credential is not None  # --auth cli is the default


def test_a_second_publish_with_the_default_mode_never_touches_the_tables(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    args = sql_args(world, "--connection-string", "env://SHAPE_TEST_CS")
    assert world.run(*args)[0] == 0
    before = {t: list(world.server.rows("dbo", t)) for t in ROWS}
    code, out, err = world.run(*args)
    assert code == 1 and "customer" in err
    assert not any(s.startswith("DROP") for s in world.server.statements)
    assert {t: list(world.server.rows("dbo", t)) for t in ROWS} == before


@pytest.mark.parametrize("mode, expected", [("append", 2), ("truncate", 1), ("replace", 1)])
def test_write_modes(world, monkeypatch, mode, expected):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    base = sql_args(world, "--connection-string", "env://SHAPE_TEST_CS")
    assert world.run(*base)[0] == 0
    code, _, err = world.run(*base, "--write-mode", mode)
    assert code == 0, err
    assert len(world.server.rows("dbo", "order")) == ROWS["order"] * expected


def test_schema_name_and_batch_size(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    code, _, err = world.run(
        *sql_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--schema-name",
            "gen",
            "--batch-size",
            "50",
        )  # fmt: skip
    )
    assert code == 0, err
    assert len(world.server.rows("gen", "order")) == ROWS["order"]


def test_sql_login_from_references_and_the_credential_option(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", "Pa55;w0rd}value")
    code, out, err = world.run(
        *sql_args(
            world,
            "--credential",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
        )  # fmt: skip
    )
    assert code == 0, err
    (conn, credential), *_ = world.connections
    assert "UID={app}" in conn and "PWD={Pa55;w0rd}}value}" in conn and credential is None
    assert "Pa55" not in out + err and "Server=db.example.test" not in out + err


def test_connection_string_from_the_environment(world, monkeypatch):
    monkeypatch.setenv("SHAPE_SQL_CONNECTION", CS)
    assert world.run(*sql_args(world))[0] == 0
    assert len(world.server.rows("dbo", "customer")) == ROWS["customer"]


def test_a_connection_string_with_a_password_is_refused_and_not_echoed(world):
    code, out, err = world.run(
        *sql_args(world, "--connection-string", CS + ";UID=app;PWD=hunter2hunter2")
    )
    assert code == 2 and "hunter2hunter2" not in out + err and world.connections == []
    code, out, err = world.run(*sql_args(world, "--credential", CS + ";PWD=hunter2hunter2"))
    assert code == 2 and "hunter2hunter2" not in out + err


def test_a_failing_insert_is_exit_1_and_the_password_is_not_in_the_message(world, monkeypatch):
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", "Pa55;w0rd}value")

    def broken(connection_string, credential=None, **kw):
        raise RuntimeError(f"cannot connect with {connection_string}")

    monkeypatch.setattr(_tsql, "connect", broken)
    code, out, err = world.run(
        *sql_args(
            world,
            "--connection-string",
            "env://SHAPE_TEST_CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "env://SHAPE_TEST_PW",
        )  # fmt: skip
    )
    assert code == 1 and "Pa55" not in out + err and "Publish complete" not in out


# --- warehouse -----------------------------------------------------------------------------


def test_warehouse_stages_parquet_and_copies_into(world):
    code, out, err = world.run(
        "publish", world.schema, "-t", "warehouse", "--connection-string", CS,
        "--staging-path", "onelake://Analytics/Sales/Files",
    )  # fmt: skip
    assert code == 0, err
    copies = [s for s in world.server.statements if s.startswith("COPY INTO")]
    assert [c.split("[")[2].split("]")[0] for c in copies] == ["customer", "order"]
    assert {t: len(world.server.rows("dbo", t)) for t in ROWS} == ROWS
    assert world.fs.files == {}  # the staged files were removed
    assert "Publishing to Warehouse" in out and "order: 200 rows" in out


def test_warehouse_staging_can_be_the_base_path(world):
    code, _, err = world.run(
        "publish", world.schema, "-t", "warehouse", "--connection-string", CS,
        "--base-path", "onelake://Analytics/Sales/Files",
    )  # fmt: skip
    assert code == 0, err


# --- eventhouse ----------------------------------------------------------------------------


def test_eventhouse_ingests_every_table(world, monkeypatch):
    kql = FakeKusto()
    monkeypatch.setattr(kusto, "urllib_transport", kql)
    code, out, err = world.run(
        "publish", world.schema, "-t", "eventhouse",
        "--connection-string", "https://kql.example.test", "--database", "db1",
    )  # fmt: skip
    assert code == 0, err
    assert {t: len(r) for t, r in kql.by_table.items()} == ROWS
    assert set(kql.auth) == {f"Bearer {FAKE_ENTRA_TOKEN}"}
    assert "Publishing to Eventhouse (db1)" in out


def test_eventhouse_uri_from_a_reference(world, monkeypatch):
    kql = FakeKusto()
    monkeypatch.setattr(kusto, "urllib_transport", kql)
    monkeypatch.setenv("SHAPE_TEST_KQL", "https://kql.example.test")
    code, _, err = world.run(
        "publish", world.schema, "-t", "eventhouse", "--credential", "env://SHAPE_TEST_KQL",
        "--database", "db1",
    )  # fmt: skip
    assert code == 0, err
    assert kql.by_table


# --- the whole command family ----------------------------------------------------------------


def test_no_trace_of_the_baseline_and_no_token_in_any_output(world, monkeypatch):
    kql = FakeKusto()
    monkeypatch.setattr(kusto, "urllib_transport", kql)
    code, out, err = world.run(
        "publish", world.schema, "-t", "eventhouse",
        "--connection-string", "https://kql.example.test", "--database", "db1",
    )  # fmt: skip
    assert not names_refengine(out + err) and FAKE_ENTRA_TOKEN not in out + err


def test_fabric_publish_sql_database_with_every_option(world, monkeypatch):
    """G6 (§10 `generate` row, `sql-database`): the command under its own name, `shape fabric
    publish`, with `--auth`, `--connection-string`, `--write-mode` and `--batch-size` together."""
    monkeypatch.setenv("SHAPE_TEST_CS", CS)
    monkeypatch.setenv("SHAPE_TEST_PW", "Pa55;w0rd}value")
    base = (
        "fabric", "publish", world.schema, "-t", "sql-database",
        "--connection-string", "env://SHAPE_TEST_CS",
        "--auth", "sql", "--sql-user", "app", "--sql-password", "env://SHAPE_TEST_PW",
    )  # fmt: skip
    assert world.run(*base)[0] == 0
    world.server.statements.clear()
    code, out, err = world.run(*base, "--write-mode", "replace", "--batch-size", "50")
    assert code == 0, err
    assert {t: len(world.server.rows("dbo", t)) for t in ROWS} == ROWS
    assert sum(s.startswith("DROP") for s in world.server.statements) == 2
    inserts = [s.split("]")[1].lstrip(".[") for s in world.server.statements if "INSERT" in s]
    assert inserts.count("customer") == 1 and inserts.count("order") == 4  # 200 rows, 50 a trip
    assert all("UID={app}" in conn and cred is None for conn, cred in world.connections)
    assert "Pa55" not in out + err and "Publish complete." in out


def test_fabric_publish_warehouse_with_a_staging_path(world):
    """G6 (§10 `generate` row): `--staging-path`, which the warehouse target takes."""
    code, out, err = world.run(
        "fabric", "publish", world.schema, "-t", "warehouse", "--connection-string", CS,
        "--staging-path", "onelake://Analytics/Sales/Files",
    )  # fmt: skip
    assert code == 0, err
    assert sum(s.startswith("COPY INTO") for s in world.server.statements) == 2
    assert {t: len(world.server.rows("dbo", t)) for t in ROWS} == ROWS
    assert world.fs.files == {} and "Publishing to Warehouse" in out
