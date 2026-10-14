"""Seed the schema-enabled Lakehouse with the retail domain (small) and record what was written.

Run by the dispatch-only ``seed`` job of ``.github/workflows/fabric-live.yml``. Writes through the
product's own path (``shape generate retail --to delta+abfss://...``), so the Lakehouse kind is
detected and the tables land under ``Tables/<schema>/``. Then it reads every table back from
OneLake, waits for the Lakehouse's SQL analytics endpoint to list it, and prints a JSON record of
exactly what exists. A missing variable fails by name; nothing is skipped.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pyarrow as pa
from shape_fabric import LakehouseSource, _tsql, onelake
from shape_fabric.fabric_api import SCOPE_FABRIC

from shape.cli.main import main
from shape.generation.domains import load_domain
from shape.scale.http import Http

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_redact import hide, redact  # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
WAIT_SECONDS = 600


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"seed needs {name}")
    return value


def main_seed() -> int:
    tables_uri = need("FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH").rstrip("/")
    schema = os.environ.get("FABRIC_LAKEHOUSE_SCHEMA") or "dbo"
    scale = os.environ.get("SEED_SCALE") or "small"
    where = onelake.parse(tables_uri)
    from azure.identity import ClientSecretCredential

    credential = ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )
    item = (
        Http(credential.get_token(SCOPE_FABRIC).token)
        .request("GET", f"{API}/workspaces/{where.workspace}/lakehouses/{where.item}")
        .json_object()
    )
    assert (item.get("properties") or {}).get("defaultSchema"), "target is not schema-enabled"
    names = list(load_domain("retail").schema.tables)
    code = main(
        [
            "generate", "retail", "--scale", scale, "--seed", "42",
            "--to", "delta+" + where.abfss(),
            "--schema-name", schema, "--yes",
            "--auth", "spn", "--tenant-id", need("FABRIC_TENANT_ID"),
            "--client-id", need("FABRIC_CLIENT_ID"),
            "--client-secret", "env://FABRIC_CLIENT_SECRET",
        ]
    )  # fmt: skip
    if code != 0:
        print(f"shape generate exited {code}")
        return 1
    record: dict = {
        "lakehouse": item["displayName"],
        "lakehouse_id": where.item,
        "workspace_id": where.workspace,
        "schema": schema,
        "scale": scale,
        "seed": 42,
        "tables": {},
    }
    source = LakehouseSource()
    for name in names:
        uri = f"{tables_uri}/{schema}/{name}"
        arrow = pa.Table.from_batches(list(source.read(uri, credential=credential)))
        record["tables"][name] = {
            "path": f"Tables/{schema}/{name}",
            "rows": arrow.num_rows,
            "columns": arrow.column_names,
        }
    props = (item.get("properties") or {}).get("sqlEndpointProperties") or {}
    hide(props.get("connectionString", ""))
    connection = f"Data Source={props['connectionString']};Initial Catalog={item['displayName']}"
    deadline = time.monotonic() + WAIT_SECONDS
    visible: dict[str, int] = {}
    while True:
        conn = _tsql.connect(connection, credential)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES ORDER BY 1, 2"
            )
            listing = [f"{r[0]}.{r[1]}" for r in cursor.fetchall()]
            if all(f"{schema}.{n}" in listing for n in names) or time.monotonic() > deadline:
                for n in names:
                    if f"{schema}.{n}" in listing:
                        cursor.execute(f"SELECT COUNT(*) FROM {_tsql.qualified(schema, n)}")
                        visible[n] = cursor.fetchone()[0]
                break
        finally:
            conn.close()
        time.sleep(15)
    record["sql_endpoint_tables"] = listing
    record["sql_endpoint_counts"] = visible
    text = json.dumps(record, indent=2)
    print(redact(text))  # the log is public; the file keeps the IDs for the run's artifact
    with open(os.environ.get("SEED_RECORD", "seed-record.json"), "w", encoding="utf-8") as f:
        f.write(text)
    missing = [n for n in names if visible.get(n) != record["tables"][n]["rows"]]
    if missing:
        print(f"NOT visible with the written row count in the SQL endpoint: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_seed())
