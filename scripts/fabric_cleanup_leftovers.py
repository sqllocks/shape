"""Remove the test tables an earlier ``test_fabric_spark_runs_the_worker_notebook_to_success`` left.

Approved by the Owner (2026-10-12) for the schema-enabled Lakehouse ``shape_lh``, schema ``dbo``,
workspace ``shape-test``. That test, before it removed its tables, wrote
``dbo/shape_live_<8 hex>_shape_live_<8 hex>_customer`` and ``..._order``. This lists the SQL
endpoint's tables of the schema, prints the full path of every one that matches that pattern
exactly, and deletes only those. Any other table (the retail seed, ``publicholidays``) and any
other Lakehouse are never touched. Run by the dispatch-only ``seed`` job of
``.github/workflows/fabric-live.yml``.
"""

from __future__ import annotations

import os
import re
import sys

from shape_fabric import _tsql, onelake
from shape_fabric._storage import Storage
from shape_fabric.fabric_api import SCOPE_FABRIC

from shape.scale.http import Http

LEFTOVER = re.compile(r"shape_live_[0-9a-f]{8}_shape_live_[0-9a-f]{8}_(?:customer|order)")
EXPECTED_LAKEHOUSE = "shape_lh"


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"cleanup needs {name}")
    return value


def main() -> int:
    tables = need("FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH").rstrip("/")
    schema = os.environ.get("FABRIC_LAKEHOUSE_SCHEMA") or "dbo"
    where = onelake.parse(tables)
    from azure.identity import ClientSecretCredential

    credential = ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )
    item = (
        Http(credential.get_token(SCOPE_FABRIC).token)
        .request(
            "GET",
            f"https://api.fabric.microsoft.com/v1/workspaces/{where.workspace}"
            f"/lakehouses/{where.item}",
        )
        .json_object()
    )
    if item.get("displayName") != EXPECTED_LAKEHOUSE:
        print(f"refusing: the Lakehouse is {item.get('displayName')!r}, not {EXPECTED_LAKEHOUSE!r}")
        return 1
    props = item["properties"]["sqlEndpointProperties"]
    conn = _tsql.connect(
        f"Data Source={props['connectionString']};Initial Catalog={item['displayName']}",
        credential,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", schema
        )
        every = sorted(r[0] for r in cursor.fetchall())
    finally:
        conn.close()
    names = [n for n in every if LEFTOVER.fullmatch(n)]
    print(
        f"{len(every)} tables in {item['displayName']}.{schema}; {len(names)} match the leftovers:"
    )
    for name in names:
        print(f"  {tables}/{schema}/{name}")
    print("kept: " + ", ".join(n for n in every if n not in names))
    storage = Storage(credential=credential)
    for name in names:
        storage.remove(f"{tables}/{schema}/{name}", recursive=True)
        print(f"deleted {tables}/{schema}/{name}")
    print(f"deleted {len(names)} leftover table folders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
