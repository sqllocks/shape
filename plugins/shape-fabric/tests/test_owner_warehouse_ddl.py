"""The Warehouse CREATE surface differs from SQL Server's."""

import os
import sys
from types import SimpleNamespace

import pyarrow as pa
import pytest
from shape_fabric import WarehouseWriter, WriteError
from shape_fabric._tsql import create_table_sql
from shape_fabric.testing import FakeSqlServer, MemoryFS, sample_batches


def test_warehouse_declares_keys_after_create():
    sql = create_table_sql(
        "dbo", "orders", pa.schema([pa.field("id", pa.int64())]), warehouse=True, primary_key=["id"]
    )
    create, alter = sql.split(";\n", 1)
    assert "PRIMARY KEY" not in create
    assert (
        alter == "ALTER TABLE [dbo].[orders] ADD CONSTRAINT [PK_orders] "
        "PRIMARY KEY NONCLUSTERED ([id]) NOT ENFORCED"
    )


@pytest.mark.parametrize("key", ["PRIMARY KEY", "UNIQUE"])
def test_the_warehouse_fake_rejects_inline_keys(key):
    server = FakeSqlServer()
    cursor = server.connect("Server=x.datawarehouse.fabric.microsoft.com;Database=w").cursor()
    with pytest.raises(RuntimeError, match="24584"):
        cursor.execute(
            "CREATE TABLE [dbo].[t] (\n    [id] BIGINT NOT NULL,\n"
            f"    CONSTRAINT [PK_t] {key} NONCLUSTERED ([id]) NOT ENFORCED\n)"
        )


def test_failed_copy_cleans_the_same_identity_authenticated_filesystem(monkeypatch):
    created = []
    server = FakeSqlServer()

    class FS(MemoryFS):
        def __init__(self, **options):
            super().__init__()
            self.credential = options["credential"]
            self.account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
            self.connection_string = None
            self.sas_token = None
            self.do_connect()
            created.append(self)
            server.files = self

        def do_connect(self):
            if self.account_key:
                raise ValueError("Unable to determine account name for shared key credential")

    def fail(sql, params):
        if sql.startswith("COPY INTO"):
            raise RuntimeError("failed load")

    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "unrelated-account-key")
    monkeypatch.setitem(sys.modules, "adlfs", SimpleNamespace(AzureBlobFileSystem=FS))
    server.fail = fail
    credential = SimpleNamespace(
        get_token=lambda *args: SimpleNamespace(token="test-token", expires_on=4100000000)
    )
    writer = WarehouseWriter(
        "Server=x.datawarehouse.fabric.microsoft.com;Database=w",
        "onelake://ws/lh/Files/staging",
        credential=credential,
        connect=server.connect,
    )
    with pytest.raises(WriteError, match="failed load"):
        writer.write_table("orders", sample_batches())
    assert len(created) == 1
    assert created[0].files == {}
    assert created[0].credential.get_token().token == "test-token"
