"""Unattended Owner checks; see docs/plugins/fabric-writers.md for required variables.

No interactive sign-in. Missing variables fail with their names, as required by P6-07a
and LIVE-FIX #15. Service failures and assertion failures also fail.
"""

import os
import uuid

import pyarrow as pa
import pytest
from shape_fabric import LakehouseSource, SqlDatabaseWriter, WarehouseWriter, WriteError, _tsql
from shape_fabric._storage import Storage
from shape_fabric.sinks import LakehouseSink
from shape_fabric.testing import sample_batches

from shape.cli.main import main

pytestmark = pytest.mark.live
LAKEHOUSES = [
    ("FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH", False),
    ("FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH", True),
]


def need(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"live test needs {name}")
    return value


def identity():
    tenant, client, secret = (
        need(name) for name in ("FABRIC_TENANT_ID", "FABRIC_CLIENT_ID", "FABRIC_CLIENT_SECRET")
    )
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(tenant, client, secret)


def sign_in():
    need("FABRIC_CLIENT_SECRET")
    return [
        "--auth",
        "spn",
        "--tenant-id",
        need("FABRIC_TENANT_ID"),
        "--client-id",
        need("FABRIC_CLIENT_ID"),
        "--client-secret",
        "env://FABRIC_CLIENT_SECRET",
    ]


def unique():
    return "shape_live_" + uuid.uuid4().hex[:12]


def db_sign_in(monkeypatch, connection):
    # profile-db's documented environment options avoid putting its secret in argv.
    monkeypatch.setenv("SHAPE_SQLSERVER_CONNECTION_STRING", connection)
    monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", need("FABRIC_CLIENT_SECRET"))
    return [
        "--auth",
        "spn",
        "--tenant-id",
        need("FABRIC_TENANT_ID"),
        "--client-id",
        need("FABRIC_CLIENT_ID"),
    ]


@pytest.mark.parametrize("variable,enabled", LAKEHOUSES)
def test_delta_cli_prefix_and_onelake_readback(variable, enabled):
    from shape_fabric import onelake

    tables = need(variable).rstrip("/")
    assert tables.startswith("onelake://") and tables.endswith("/Tables")
    schema = os.environ.get("FABRIC_LAKEHOUSE_SCHEMA", "dbo")
    prefix = unique() + "_"
    credential = identity()
    storage = Storage(credential=credential)
    target = "delta+" + onelake.parse(tables).abfss()
    options = (
        ["--sink-config", "delta.schema_enabled=true", "--schema-name", schema] if enabled else []
    )
    from shape.generation.domains import load_domain

    names = list(load_domain("retail").schema.tables)
    root = f"{tables}/{schema}" if enabled else tables
    try:
        assert (
            main(
                [
                    "generate",
                    "retail",
                    "--scale",
                    "small",
                    "--seed",
                    "42",
                    "--to",
                    target,
                    "--table-prefix",
                    prefix,
                    "--yes",
                    *options,
                    *sign_in(),
                ]
            )
            == 0
        )
        source = LakehouseSource()
        for name in names:
            uri = f"{root}/{prefix}{name}"
            arrow = pa.Table.from_batches(list(source.read(uri, credential=credential)))
            assert arrow.num_rows > 0 and arrow.num_columns > 0
    finally:
        for name in names:
            storage.remove(f"{root}/{prefix}{name}", recursive=True)
        credential.close()


@pytest.mark.parametrize("variable,enabled", LAKEHOUSES)
def test_files_prefix_and_onelake_readback(variable, enabled):
    tables = need(variable).rstrip("/")
    assert tables.endswith("/Tables")
    folder = tables.removesuffix("/Tables") + "/Files/" + unique()
    prefix = "shape_"
    credential = identity()
    storage = Storage(credential=credential)
    try:
        assert (
            LakehouseSink().write(
                folder, "orders", sample_batches(), credential=credential, table_prefix=prefix
            )
            == 7
        )
        uri = f"{folder}/{prefix}orders/part-0001.parquet"
        rows = pa.Table.from_batches(list(LakehouseSource().read(uri, credential=credential)))
        assert rows.num_rows == 7
    finally:
        storage.remove(folder, recursive=True)
        credential.close()


@pytest.mark.parametrize("with_key", [False, True])
def test_warehouse_copy_keys_and_staging_cleanup(with_key):
    connection = need("FABRIC_WAREHOUSE_CONNECTION_STRING")
    staging = need("FABRIC_STAGING_PATH")
    credential = identity()
    name = unique()
    with WarehouseWriter(connection, staging, credential=credential) as writer:
        try:
            assert (
                writer.write_table(name, sample_batches(), primary_key=["id"] if with_key else [])
                == 7
            )
            cursor = writer.db.execute(f"SELECT COUNT(*) FROM {_tsql.qualified('dbo', name)}")
            try:
                assert cursor.fetchone()[0] == 7
            finally:
                cursor.close()
            assert not writer.storage.exists(writer._folder(name).abfss())
        finally:
            writer.db.execute(_tsql.drop_table_sql("dbo", name, if_exists=True)).close()
            writer.db.commit()
            credential.close()


def test_failed_warehouse_load_removes_uploaded_staging(monkeypatch):
    connection = need("FABRIC_WAREHOUSE_CONNECTION_STRING")
    staging = need("FABRIC_STAGING_PATH")
    credential = identity()
    name = unique()

    def failure(*args):
        raise RuntimeError("injected load failure after real upload")

    with WarehouseWriter(connection, staging, credential=credential) as writer:
        monkeypatch.setattr(writer, "_copy", failure)
        try:
            with pytest.raises(WriteError, match="injected load failure"):
                writer.write_table(name, sample_batches(), primary_key=["id"])
            assert not writer.storage.exists(writer._folder(name).abfss())
            assert not writer.db.table_exists("dbo", name)
        finally:
            credential.close()


def test_sql_database_write_and_profile_db(tmp_path, monkeypatch):
    connection = need("FABRIC_SQL_CONNECTION_STRING")
    credential = identity()
    name = unique()
    with SqlDatabaseWriter(connection, credential=credential) as writer:
        try:
            assert writer.write_table(name, sample_batches(), primary_key=["id"]) == 7
            assert (
                main(
                    [
                        "profile-db",
                        "--tables",
                        name,
                        "--sample-rows",
                        "1000",
                        "-o",
                        str(tmp_path / "sql.shape"),
                        *db_sign_in(monkeypatch, connection),
                    ]
                )
                == 0
            )
            import shape

            assert shape.load(tmp_path / "sql.shape").to_dict()["tables"][name]["row_count"] == 7
        finally:
            writer.db.execute(_tsql.drop_table_sql("dbo", name, if_exists=True)).close()
            writer.db.commit()
            credential.close()


def test_profile_db_sample_warehouse(tmp_path, monkeypatch, capsys):
    connection = need("FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING")
    code = main(
        [
            "profile-db",
            "--schema",
            os.environ.get("FABRIC_SAMPLE_WAREHOUSE_SCHEMA", "dbo"),
            "--sample-rows",
            "1000",
            "-o",
            str(tmp_path / "warehouse.shape"),
            *db_sign_in(monkeypatch, connection),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    import shape

    assert shape.load(tmp_path / "warehouse.shape").to_dict()["tables"]


def test_publish_warehouse_keys_copy_and_cleanup():
    connection = need("FABRIC_WAREHOUSE_CONNECTION_STRING")
    staging = need("FABRIC_STAGING_PATH").rstrip("/") + "/" + unique()
    schema = unique()
    credential = identity()
    storage = Storage(credential=credential)
    conn = _tsql.connect(connection, credential)
    try:
        assert (
            main(
                [
                    "publish",
                    "retail",
                    "--scale",
                    "small",
                    "-t",
                    "warehouse",
                    "--connection-string",
                    "env://FABRIC_WAREHOUSE_CONNECTION_STRING",
                    "--staging-path",
                    staging,
                    "--schema-name",
                    schema,
                    *sign_in(),
                ]
            )
            == 0
        )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", schema
            )
            names = [row[0] for row in cursor.fetchall()]
            assert "customer" in names and "order" in names
        finally:
            cursor.close()
        assert not storage.exists(staging + "/staging")
    finally:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", schema
            )
            for row in cursor.fetchall():
                cursor.execute(_tsql.drop_table_sql(schema, row[0]))
            cursor.execute("SELECT 1 FROM sys.schemas WHERE name = ?", schema)
            if cursor.fetchone():
                cursor.execute(f"DROP SCHEMA {_tsql.ident(schema)}")
            conn.commit()
        finally:
            cursor.close()
            conn.close()
            storage.remove(staging, recursive=True)
            credential.close()


@pytest.mark.parametrize("variable,enabled", LAKEHOUSES)
def test_publish_lakehouse_files_in_each_lakehouse_kind(variable, enabled):
    tables = need(variable).rstrip("/")
    assert tables.endswith("/Tables")
    folder = tables.removesuffix("/Tables") + "/Files/" + unique()
    credential = identity()
    storage = Storage(credential=credential)
    try:
        assert (
            main(
                [
                    "publish",
                    "retail",
                    "--scale",
                    "small",
                    "-t",
                    "lakehouse",
                    "--base-path",
                    folder,
                    *sign_in(),
                ]
            )
            == 0
        )
        uri = f"{folder}/landing/retail/customer/dt=latest/part-0001.parquet"
        assert (
            pa.Table.from_batches(list(LakehouseSource().read(uri, credential=credential))).num_rows
            > 0
        )
        assert storage.exists(f"{folder}/landing/retail/manifest/_control/run_manifest.json")
    finally:
        storage.remove(folder, recursive=True)
        credential.close()
