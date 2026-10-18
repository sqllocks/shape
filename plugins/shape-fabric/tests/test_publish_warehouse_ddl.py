"""``shape publish -t warehouse`` never puts a key inside ``CREATE TABLE``.

Fabric Data Warehouse accepts a primary key only as ``NONCLUSTERED ... NOT ENFORCED`` added with
``ALTER TABLE ... ADD CONSTRAINT`` after the table exists:
https://learn.microsoft.com/en-us/fabric/data-warehouse/table-constraints

``test_publish.py`` forces the warehouse flag on every fake connection, so it cannot see the
dialect the command chooses by itself. Here the connection string is the portal's, the flag is
left to the code under test, and every statement the publish sends is checked.
"""

from __future__ import annotations

import json
import re

import pytest
from shape_fabric import _tsql
from shape_fabric.testing import FakeIdentity, FakeSqlServer, MemoryFS

from shape.cli.main import main

pytestmark = pytest.mark.contract

PORTAL = "Data Source=host.datawarehouse.fabric.microsoft.com;Initial Catalog=shape_wh"


def _col(name, strategy, **gen):
    return {
        "name": name,
        "type": "integer",
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
    "generation": {"scale": "small", "scales": {"small": {"customer": 30, "order": 200}}},
}


@pytest.fixture
def published(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")
    monkeypatch.setenv("WH", PORTAL)
    monkeypatch.setattr("shape_fabric.auth._notebookutils", lambda: None)
    FakeIdentity().install(monkeypatch)
    fs = MemoryFS()
    server = FakeSqlServer(fs)  # the dialect follows the connection string, as in a real run
    monkeypatch.setattr(_tsql, "connect", server.connect)
    monkeypatch.setattr("shape.builtins.sources.azure._filesystem", lambda loc, options: fs)
    schema = tmp_path / "tiny.json"
    schema.write_text(json.dumps(DOC))
    code = main(
        [
            "publish", str(schema), "-t", "warehouse",
            "--connection-string", "env://WH",
            "--staging-path", "onelake://W/L/Files/shape_staging",
        ]
    )  # fmt: skip
    assert code == 0
    return server


def test_warehouse_publish_never_declares_a_key_inside_create_table(published):
    creates = [s for s in published.statements if s.lstrip().upper().startswith("CREATE TABLE")]
    assert len(creates) == 2
    for sql in creates:
        create = sql.split(";\n", 1)[0]
        assert "PRIMARY KEY" not in create.upper() and "UNIQUE" not in create.upper()


def test_warehouse_publish_adds_each_key_by_alter_table(published):
    sent = "\n".join(published.statements)
    for table, column in (("customer", "customer_id"), ("order", "order_id")):
        assert re.search(
            rf"ALTER TABLE \[dbo\]\.\[{table}\] ADD CONSTRAINT \[PK_{table}\] "
            rf"PRIMARY KEY NONCLUSTERED \(\[{column}\]\) NOT ENFORCED",
            sent,
        )
