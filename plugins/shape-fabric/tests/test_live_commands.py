"""The Fabric commands against a real workspace (nightly ``fabric-live``, only where secrets exist).

The workflow runs each test only when its secrets are set (a job-level check; nothing here is
skipped by a decorator). Run by hand with the variables you have:

    FABRIC_TENANT_ID / FABRIC_CLIENT_ID / FABRIC_CLIENT_SECRET   a service principal (O-02)
    FABRIC_WORKSPACE_ID               the workspace the notebook and environment go to
    FABRIC_STAGING_PATH               a lakehouse Files folder, onelake://<ws>/<lakehouse>/Files/<dir>
    FABRIC_SQL_CONNECTION_STRING      a Fabric SQL database (no login in it: the principal signs in)

    pytest -m live plugins/shape-fabric/tests/test_live_commands.py -k deploy

A missing variable fails the test and names it. ``SHAPE_RECORD_DIR`` also writes the real Fabric
REST conversation as a scrubbed tape (``source: live``) for review. Every test removes what it
created.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from shape_fabric import _tsql, commands
from shape_fabric._storage import Storage
from shape_fabric.fabric_api import FabricApi, tuple_transport
from shape_fabric.recording import Tape, TapeTransport, save

from shape.cli.main import main

pytestmark = pytest.mark.live
RECORD = os.environ.get("SHAPE_RECORD_DIR")


def need(name):
    value = os.environ.get(name)
    assert value, f"{name} is not set (live tests need it)"
    return value


def unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def sign_in():
    """The options that sign in as the service principal (the secret as an env reference)."""
    need("FABRIC_CLIENT_SECRET")
    return [
        "--auth", "spn",
        "--tenant-id", need("FABRIC_TENANT_ID"),
        "--client-id", need("FABRIC_CLIENT_ID"),
        "--client-secret", "env://FABRIC_CLIENT_SECRET",
    ]  # fmt: skip


def credential():
    from shape.cli.auth import make_credential

    return make_credential(
        {
            "mode": "spn",
            "tenant_id": need("FABRIC_TENANT_ID"),
            "client_id": need("FABRIC_CLIENT_ID"),
            "client_secret": "env://FABRIC_CLIENT_SECRET",
        }
    )


def real_api():
    return FabricApi(credential())


@pytest.fixture
def conversation(monkeypatch):
    """The real Fabric REST API, recorded when SHAPE_RECORD_DIR is set."""
    if not RECORD:
        yield None
        return
    from shape.scale.http import urllib_transport

    def inner(method, url, headers, body, timeout):
        response = urllib_transport(method, url, headers, body or None, timeout)
        return response.status, response.headers, response.body

    tape = Tape(channel="http", scenario="live_commands")
    monkeypatch.setattr(commands, "API_TRANSPORT", tuple_transport(TapeTransport(tape, inner)))
    yield tape
    save(Path(RECORD) / "live_commands.json", tape.document("live", None))


def test_deploy_notebook_creates_a_notebook_item_and_it_can_be_removed(conversation, capsys):
    workspace = need("FABRIC_WORKSPACE_ID")
    name = unique("shape_live_nb")
    api = real_api()
    try:
        code = main(
            ["deploy-notebook", "retail", "--workspace", workspace, "--notebook-name", name]
            + sign_in()
        )
        out = capsys.readouterr().out
        assert code == 0, out
        item = api.find_item(workspace, "Notebook", name)
        assert item is not None and item["id"] in out
    finally:
        found = api.find_item(workspace, "Notebook", name)
        if found:
            api.delete_item(workspace, found["id"])


def test_setup_creates_an_environment_and_a_second_run_finds_it(conversation, capsys):
    workspace = need("FABRIC_WORKSPACE_ID")
    name = unique("shape_live_env")
    api = real_api()
    args = ["setup-fabric", "--workspace", workspace, "--env-name", name, *sign_in()]
    try:
        assert main(args) == 0
        assert "Created Environment" in capsys.readouterr().out
        assert main(args) == 0
        assert "Found existing Environment" in capsys.readouterr().out
    finally:
        found = api.find_item(workspace, "Environment", name)
        if found:
            api.delete_item(workspace, found["id"])


def test_publish_to_a_lakehouse_writes_the_landing_zone_and_the_manifest(capsys):
    folder = f"{need('FABRIC_STAGING_PATH').rstrip('/')}/{unique('shape_live_pub')}"
    storage = Storage(credential=credential())
    try:
        code = main(["publish", "retail", "-t", "lakehouse", "--base-path", folder, *sign_in()])
        assert code == 0, capsys.readouterr().out
        assert storage.exists(f"{folder}/landing/retail/customer/dt=latest/part-0001.parquet")
        assert storage.exists(f"{folder}/landing/retail/manifest/_control/run_manifest.json")
    finally:
        storage.remove(folder, recursive=True)


def test_publish_to_a_sql_database_creates_and_fills_the_tables(capsys):
    schema = unique("shape_live")
    connection = need("FABRIC_SQL_CONNECTION_STRING")
    os.environ.setdefault("SHAPE_LIVE_SQL_CS", connection)
    try:
        code = main(
            [
                "publish",
                "retail",
                "-t",
                "sql-database",
                "--connection-string",
                "env://SHAPE_LIVE_SQL_CS",
                "--schema-name",
                schema,
                *sign_in(),
            ]  # fmt: skip
        )
        assert code == 0, capsys.readouterr().out
    finally:
        conn = _tsql.connect(connection, credential())
        try:
            cursor = conn.cursor()
            for table in (
                "return", "order_line", "order", "product", "address", "store", "promotion",
                "product_category", "customer",
            ):  # fmt: skip
                cursor.execute(f"DROP TABLE IF EXISTS {_tsql.qualified(schema, table)}")
            cursor.execute(f"DROP SCHEMA IF EXISTS {_tsql.ident(schema)}")
            conn.commit()
        finally:
            conn.close()
