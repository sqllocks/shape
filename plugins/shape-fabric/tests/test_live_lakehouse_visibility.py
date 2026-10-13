"""A table Shape writes to a Lakehouse is visible through that Lakehouse's SQL analytics endpoint.

Owner finding (2026-10-12): in the schema-enabled Lakehouse ``shape_lh`` only a Microsoft sample
table showed in the SQL endpoint and the semantic-model dialog, because Shape had written
``Tables/<table>`` instead of ``Tables/<schema>/<table>``. These tests write a small table to each
kind of Lakehouse with the Delta sink - without saying which kind it is, so the kind is detected -
and assert that ``INFORMATION_SCHEMA.TABLES`` of the SQL endpoint lists it and that it can be
counted and read back; then they remove it.

Variables (fail by name when missing): FABRIC_TENANT_ID, FABRIC_CLIENT_ID, FABRIC_CLIENT_SECRET and
FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH / FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH, in the form
``onelake://<workspace-guid>/<lakehouse-guid>/Tables``. FABRIC_LAKEHOUSE_SCHEMA names the schema
(default ``dbo``) for the schema-enabled Lakehouse.
"""

import os
import time
import uuid

import pyarrow as pa
import pytest
from shape_fabric import LakehouseSource, _tsql, onelake
from shape_fabric._storage import Storage
from shape_fabric.fabric_api import SCOPE_FABRIC
from shape_fabric.testing import sample_batches

from shape.plugins.host import default_host
from shape.scale.http import Http, HttpError

pytestmark = pytest.mark.live
LAKEHOUSES = [
    ("FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH", False),
    ("FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH", True),
]
API = "https://api.fabric.microsoft.com/v1"
VISIBLE_WITHIN_SECONDS = 420


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


def unique():
    return "shape_live_" + uuid.uuid4().hex[:12]


def lakehouse_item(credential, workspace, lakehouse):
    token = credential.get_token(SCOPE_FABRIC).token
    return (
        Http(token)
        .request("GET", f"{API}/workspaces/{workspace}/lakehouses/{lakehouse}")
        .json_object()
    )


def refresh_endpoint(credential, workspace, endpoint_id):
    """Ask Fabric to sync the SQL endpoint now (a preview API; a refusal is only printed, the
    endpoint's own automatic sync is what the poll below waits for)."""
    token = credential.get_token(SCOPE_FABRIC).token
    try:
        Http(token).request(
            "POST",
            f"{API}/workspaces/{workspace}/sqlEndpoints/{endpoint_id}/refreshMetadata",
            body={},
        )
    except HttpError as exc:
        print(f"refreshMetadata was not accepted ({exc.status}); relying on automatic sync")


def endpoint_connection(item):
    props = (item.get("properties") or {}).get("sqlEndpointProperties") or {}
    host, state = props.get("connectionString"), props.get("provisioningStatus")
    assert host and state == "Success", (
        f"SQL endpoint of {item.get('displayName')} not ready: {props}"
    )
    return f"Data Source={host};Initial Catalog={item['displayName']}", props.get("id")


def listed(connection, credential, schema, table):
    conn = _tsql.connect(connection, credential)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            schema,
            table,
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


def count(connection, credential, schema, table):
    conn = _tsql.connect(connection, credential)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {_tsql.qualified(schema, table)}")
        return cursor.fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("variable,enabled", LAKEHOUSES)
def test_a_written_table_is_visible_in_the_sql_endpoint(variable, enabled):
    tables = need(variable).rstrip("/")
    assert tables.endswith("/Tables"), f"{variable} must end in /Tables, got {tables!r}"
    where = onelake.parse(tables)
    credential = identity()
    storage = Storage(credential=credential)
    name = unique()
    schema = os.environ.get("FABRIC_LAKEHOUSE_SCHEMA", "dbo") if enabled else "dbo"
    root = f"{tables}/{schema}" if enabled else tables
    try:
        item = lakehouse_item(credential, where.workspace, where.item)
        assert bool((item.get("properties") or {}).get("defaultSchema")) is enabled, (
            f"{variable} points at a {'classic' if enabled else 'schema-enabled'} Lakehouse"
        )
        connection, endpoint_id = endpoint_connection(item)
        options = {"credential": credential}
        if enabled:  # a schema is user-selectable; the kind itself is detected
            options["schema_name"] = schema
        sink = default_host().get("shape.sinks", "delta")
        written = sink.write(
            "delta+" + onelake.parse(tables).abfss(), name, sample_batches(), **options
        )
        assert written == 7
        refresh_endpoint(credential, where.workspace, endpoint_id)
        deadline = time.monotonic() + VISIBLE_WITHIN_SECONDS
        seen = 0
        while time.monotonic() < deadline:
            seen = listed(connection, credential, schema, name)
            if seen:
                break
            time.sleep(15)
        assert seen == 1, (
            f"table {schema}.{name} is not in INFORMATION_SCHEMA.TABLES of the SQL endpoint of "
            f"{item['displayName']} after {VISIBLE_WITHIN_SECONDS}s"
        )
        assert count(connection, credential, schema, name) == 7
        rows = pa.Table.from_batches(
            list(LakehouseSource().read(f"{root}/{name}", credential=credential))
        )
        assert rows.num_rows == 7
    finally:
        storage.remove(f"{root}/{name}", recursive=True)
        credential.close()
