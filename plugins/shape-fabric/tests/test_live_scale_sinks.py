"""The scale router's Fabric sinks, and the ``fabric_spark`` mode, against real Fabric items
(nightly ``fabric-live``, only where the owner's O-02 secrets exist).

Gated like ``test_live_writers.py``: the workflow runs each step only when its secrets are set (a
job-level check; nothing here is skipped by a decorator) and a missing variable fails its test by
name. The variables are the ones of ``test_live_writers.py``, plus for ``fabric_spark``:

    FABRIC_WORKSPACE_ID / FABRIC_LAKEHOUSE_ID   GUIDs of the workspace and the lakehouse

    pytest -m live plugins/shape-fabric/tests/test_live_scale_sinks.py -k sql_database

Every test removes what it created.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from shape_fabric import SqlDatabaseWriter, WarehouseWriter, _tsql
from shape_fabric._storage import Storage

from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.scale.api import scale_generate
from shape.scale.jobs import Jobs, JobStore
from shape.scale.router import ScaleRouter
from shape.scale.sinks.fabric import KqlSink, LakehouseSink, SqlDatabaseSink, WarehouseSink

pytestmark = pytest.mark.live

ROWS = {"customer": 30, "order": 200}


def need(name: str) -> str:
    value = os.environ.get(name)
    assert value, f"{name} is not set (live tests need it)"
    return value


def credential():
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )


def unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def schema_doc(prefix: str) -> dict:
    def col(name, strategy, type_="integer", **gen):
        return {
            "name": name,
            "type": type_,
            "generator": {"strategy": strategy, **gen},
            "nullable": False,
            "null_rate": 0.0,
        }

    return {
        "schema_version": 1,
        "model": {"name": prefix, "seed": 5},
        "tables": {
            f"{prefix}_customer": {
                "name": f"{prefix}_customer",
                "primary_key": ["customer_id"],
                "columns": {
                    "customer_id": col("customer_id", "sequence"),
                    "score": col("score", "distribution", "float", low=0.0, high=9.0),
                },
            },
            f"{prefix}_order": {
                "name": f"{prefix}_order",
                "primary_key": ["order_id"],
                "columns": {
                    "order_id": col("order_id", "sequence"),
                    "customer_id": col(
                        "customer_id", "foreign_key", ref=f"{prefix}_customer.customer_id"
                    ),
                },
            },
        },
        "relationships": [
            {
                "name": "o_c",
                "parent": f"{prefix}_customer",
                "child": f"{prefix}_order",
                "parent_columns": ["customer_id"],
                "child_columns": ["customer_id"],
            }
        ],
        "generation": {
            "scale": "small",
            "scales": {"small": {f"{prefix}_customer": 30, f"{prefix}_order": 200}},
        },
    }


def run(sink, prefix: str) -> None:
    engine = Engine(GenSchema.from_dict(schema_doc(prefix)), seed=3)
    ScaleRouter(engine, [sink], mode="local_mp", chunk_size=100).run()


def test_lakehouse_sink_writes_files_to_onelake():
    prefix = unique("shape_live")
    folder = f"{need('FABRIC_STAGING_PATH').rstrip('/')}/{prefix}"
    cred = credential()
    storage = Storage(credential=cred)
    try:
        run(LakehouseSink(folder, "parquet", writer_options={"credential": cred}), prefix)
        assert storage.exists(f"{folder}/{prefix}_order/part-0001.parquet")
    finally:
        storage.remove(folder, recursive=True)


def test_sql_database_sink_loads_both_tables():
    prefix = unique("shape_live")
    cs = need("FABRIC_SQL_CONNECTION_STRING")
    cred = credential()
    try:
        run(SqlDatabaseSink(cs, writer_options={"credential": cred}), prefix)
        with SqlDatabaseWriter(cs, credential=cred) as w:
            count = w.db.execute(
                f"SELECT COUNT(*) FROM {_tsql.qualified('dbo', prefix + '_order')}"
            )
            assert count.fetchone()[0] == ROWS["order"]
    finally:
        with SqlDatabaseWriter(cs, credential=cred) as w:
            for t in ("order", "customer"):
                w.db.execute(f"DROP TABLE IF EXISTS {_tsql.qualified('dbo', f'{prefix}_{t}')}")
            w.db.commit()


def test_warehouse_sink_copies_both_tables_in():
    prefix = unique("shape_live")
    cs = need("FABRIC_WAREHOUSE_CONNECTION_STRING")
    cred = credential()
    sink = WarehouseSink(
        cs, need("FABRIC_STAGING_PATH"), writer_options={"credential": cred}, chunk_size=100
    )
    try:
        run(sink, prefix)
        with WarehouseWriter(cs, need("FABRIC_STAGING_PATH"), credential=cred) as w:
            count = w.db.execute(
                f"SELECT COUNT(*) FROM {_tsql.qualified('dbo', prefix + '_order')}"
            )
            assert count.fetchone()[0] == ROWS["order"]
    finally:
        with WarehouseWriter(cs, need("FABRIC_STAGING_PATH"), credential=cred) as w:
            for t in ("order", "customer"):
                w.db.execute(f"DROP TABLE IF EXISTS {_tsql.qualified('dbo', f'{prefix}_{t}')}")
            w.db.commit()


def test_kql_sink_ingests_both_tables():
    from shape_fabric import EventhouseWriter

    prefix = unique("shape_live")
    uri = need("FABRIC_EVENTHOUSE_URI")
    token = need("FABRIC_EVENTHOUSE_TOKEN")
    host_and_db = uri.split("://", 1)[-1].split("?")[0]
    host, _, database = host_and_db.partition("/")
    run(KqlSink(f"https://{host}", database, writer_options={"token": token}), prefix)
    writer = EventhouseWriter(uri, token=token)
    try:
        deadline = time.monotonic() + 300
        while writer.row_count(f"{prefix}_order") < ROWS["order"] and time.monotonic() < deadline:
            time.sleep(10)
        assert writer.row_count(f"{prefix}_order") == ROWS["order"]
    finally:
        for t in ("order", "customer"):
            writer.client.mgmt(f".drop table ['{prefix}_{t}'] ifexists")


def test_fabric_spark_runs_the_worker_notebook_to_success(tmp_path):
    workspace, lakehouse = need("FABRIC_WORKSPACE_ID"), need("FABRIC_LAKEHOUSE_ID")
    cred = credential()
    fabric_token = cred.get_token("https://api.fabric.microsoft.com/.default").token
    storage_token = cred.get_token("https://storage.azure.com/.default").token
    prefix = unique("shape_live")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(schema_doc(prefix)))
    jobs = Jobs(JobStore(tmp_path / "jobs"))
    job = scale_generate(
        {
            "domain": str(schema),
            "scale_mode": "fabric_spark",
            "chunk_size": 100,
            "sinks": ["lakehouse"],
            "fabric": {
                "workspace_id": workspace,
                "lakehouse_id": lakehouse,
                "table_prefix": f"{prefix}_",
            },
        },
        jobs=jobs,
        token=fabric_token,
        storage_token=storage_token,
    )
    deadline = time.monotonic() + 25 * 60
    status = job["status"]
    while status not in ("succeeded", "failed", "cancelled") and time.monotonic() < deadline:
        time.sleep(20)
        status = jobs.status(job["job_id"], fabric_token)["status"]
    if status not in ("succeeded", "failed", "cancelled"):
        jobs.cancel(job["job_id"], fabric_token)
    assert status == "succeeded", jobs.status(job["job_id"], fabric_token)
