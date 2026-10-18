"""A round trip against a real Synapse dedicated SQL pool (``live``).

    SHAPE_TEST_SYNAPSE_URI='synapse://<workspace>.sql.azuresynapse.net/<pool>' \\
    SHAPE_TEST_SYNAPSE_STAGING='abfss://<container>@<account>.dfs.core.windows.net/shape-tests' \\
        pytest -m live plugins/shape-fabric/tests/test_live_synapse.py

Signs in with the Azure CLI session (``az login``); ``SHAPE_TEST_SYNAPSE_AUTH`` names another
``--auth`` mode (``msi``, ``spn``, ...), with ``SHAPE_TEST_SYNAPSE_TENANT_ID``,
``SHAPE_TEST_SYNAPSE_CLIENT_ID`` and ``SHAPE_TEST_SYNAPSE_CLIENT_SECRET`` for a service principal.
The test fails, naming the missing setting, when the connection settings are absent. It writes a
table with a unique name, reads it back, compares the row count and the dataset id
(``shape.repro.dataset_id``) of what was written with what was read, and drops the table.
"""

import os
import uuid
import warnings

import pyarrow as pa
import pyarrow.compute as pc
import pytest
from shape_fabric import SynapseSink, _tsql
from shape_fabric.auth import AuthSettings, build_credential
from shape_fabric.testing import sample_batch

from shape.repro import dataset_id

pytestmark = pytest.mark.live


def need(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.fail(f"live test needs {', '.join(missing)}")
    return [os.environ[n] for n in names]


def credential():
    mode = os.environ.get("SHAPE_TEST_SYNAPSE_AUTH", "cli")
    settings = AuthSettings(mode=mode)
    if mode == "spn":
        tenant, client, _ = need(
            "SHAPE_TEST_SYNAPSE_TENANT_ID",
            "SHAPE_TEST_SYNAPSE_CLIENT_ID",
            "SHAPE_TEST_SYNAPSE_CLIENT_SECRET",
        )
        reference = "env://SHAPE_TEST_SYNAPSE_CLIENT_SECRET"
        settings = AuthSettings(
            mode=mode,
            tenant_id=tenant,
            client_id=client,
            client_secret=reference,
        )
    return build_credential(settings)


def test_synapse_round_trip():
    uri, staging = need("SHAPE_TEST_SYNAPSE_URI", "SHAPE_TEST_SYNAPSE_STAGING")
    identity = credential()
    table = f"shape_live_{uuid.uuid4().hex[:8]}"
    batches = [sample_batch(0, 30), sample_batch(30, 12)]
    sent = pa.Table.from_batches(batches)
    # The writers store a NaN as NULL (a SQL float has no NaN; docs/SINKS.md), so a NaN written is a
    # NULL read back. That is the only difference allowed: ±Infinity is not replaced here.
    written = pa.table(
        {
            f.name: (
                pc.if_else(pc.is_nan(sent[f.name]), pa.scalar(None, f.type), sent[f.name])
                if pa.types.is_floating(f.type)
                else sent[f.name]
            )
            for f in sent.schema
        },
        schema=sent.schema,
    )
    host, pool = SynapseSink()._parse(uri)
    from shape_sqlserver.sql import build_connection_string

    conn_string = str(build_connection_string(host, pool))
    conn = _tsql.connect(conn_string, identity)
    try:
        assert (
            SynapseSink().write(
                uri,
                table,
                iter(batches),
                staging_path=staging,
                credential=identity,
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
        # Every cell that differs from what was sent, under the plain comparison (no NaN rule):
        # (column, row, written, read back). Printed in the run summary and as a warning, so the
        # differences are observed, and required to be nothing but a NaN that came back NULL.
        observed = [
            (n, i, w, g)
            for n in names
            for i, (w, g) in enumerate(
                zip(sent.column(n).to_pylist(), got.column(n).to_pylist(), strict=True)
            )
            if not (w == g)
        ]
        report = f"{len(observed)} cell(s) differ from what was sent: " + "; ".join(
            f"{n}[{i}] written {w!r} read {g!r}" for n, i, w, g in observed
        )
        warnings.warn(report, stacklevel=1)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"\n## Synapse round trip: plain comparison\n\n{report}\n")
        float_columns = {f.name for f in sent.schema if pa.types.is_floating(f.type)}
        other = [
            o for o in observed if not (o[0] in float_columns and o[2] != o[2] and o[3] is None)
        ]  # o[2] != o[2] is a NaN
        assert not other, f"differences that are not a NaN read back as NULL: {other}"
        assert dataset_id({table: got}) == dataset_id({table: written}), report
    finally:
        try:
            # a dedicated pool does not parse DROP TABLE IF EXISTS and refuses DDL in a transaction
            conn.rollback()
            conn.autocommit = True
            name = _tsql.qualified("dbo", table)
            conn.cursor().execute(
                f"IF OBJECT_ID(N'{name}', N'U') IS NOT NULL {_tsql.drop_table_sql('dbo', table)}"  # nosec B608
            )
        finally:
            conn.close()
