"""A round trip against a real Synapse dedicated SQL pool (``live``).

    SHAPE_TEST_SYNAPSE_URI='synapse://<workspace>.sql.azuresynapse.net/<pool>' \\
    SHAPE_TEST_SYNAPSE_STAGING='abfss://<container>@<account>.dfs.core.windows.net/shape-tests' \\
        pytest -m live plugins/shape-fabric/tests/test_live_synapse.py

Signs in with the Azure CLI session (``az login``); ``SHAPE_TEST_SYNAPSE_AUTH`` names another
``--auth`` mode (``msi``, ``spn``, ...), with ``SHAPE_TEST_SYNAPSE_TENANT_ID``,
``SHAPE_TEST_SYNAPSE_CLIENT_ID`` and ``SHAPE_TEST_SYNAPSE_CLIENT_SECRET`` for a service principal.
The test skips, naming the missing setting, when the connection settings are absent. It writes a
table with a unique name, reads it back, compares the row count and the dataset id
(``shape.repro.dataset_id``) of what was written with what was read, and drops the table.
"""

import os
import uuid

import pyarrow as pa
import pytest
from shape_fabric import SynapseSink, _tsql
from shape_fabric.auth import AuthSettings, build_credential
from shape_fabric.testing import sample_batch

from shape.repro import dataset_id

pytestmark = pytest.mark.live


def need(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"live test needs {', '.join(missing)}")
    return [os.environ[n] for n in names]


def test_synapse_round_trip():
    uri, staging = need("SHAPE_TEST_SYNAPSE_URI", "SHAPE_TEST_SYNAPSE_STAGING")
    credential = build_credential(
        AuthSettings(mode=os.environ.get("SHAPE_TEST_SYNAPSE_AUTH", "cli"))
    )
    table = f"shape_live_{uuid.uuid4().hex[:8]}"
    batches = [sample_batch(0, 30), sample_batch(30, 12)]
    written = pa.Table.from_batches(batches)
    host, pool = SynapseSink()._parse(uri)
    from shape_sqlserver.sql import build_connection_string

    conn_string = str(build_connection_string(host, pool))
    conn = _tsql.connect(conn_string, credential)
    try:
        assert (
            SynapseSink().write(
                uri,
                table,
                iter(batches),
                staging_path=staging,
                credential=credential,
                distribution="HASH(id)",
                commit_rows=30,
            )
            == 42
        )
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {_tsql.qualified('dbo', table)} ORDER BY 1")  # nosec B608
        rows = [tuple(r) for r in cursor.fetchall()]
        assert len(rows) == written.num_rows
        names = written.schema.names
        got = pa.Table.from_pydict(
            {n: [r[i] for r in rows] for i, n in enumerate(names)}, schema=written.schema
        )
        assert dataset_id({table: got}) == dataset_id({table: written})
    finally:
        try:
            conn.cursor().execute(_tsql.drop_table_sql("dbo", table, if_exists=True))
            conn.commit()
        finally:
            conn.close()
