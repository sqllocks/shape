"""Delete exactly the leftovers of the Fabric documentation examples from the TEST workspace.

Owner-approved 2026-10-13: the Notebook ``Shape_retail_small`` and the nine retail tables (schema
``dbo``) in the SQL database, the Warehouse and the Eventhouse KQL database, when they exist.
Each store is listed first; only exact name matches are deleted; every deletion is printed and
written to a summary file. Nothing else is touched.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

NOTEBOOK = "Shape_retail_small"
# Children first, so foreign keys never block a drop.
TABLES = (
    "return",
    "order_line",
    "order",
    "product",
    "address",
    "store",
    "promotion",
    "product_category",
    "customer",
)


def doomed(existing: list[str], wanted: tuple[str, ...] = TABLES) -> list[str]:
    """Exact, case-insensitive matches of ``wanted`` among ``existing``, in ``wanted`` order."""
    present = {name.lower(): name for name in existing}
    return [present[name] for name in wanted if name in present]


def clean_sql(label: str, connection: Any, report: dict[str, Any]) -> None:
    """List dbo base tables, then drop only the retail tables that exist."""
    cursor = connection.cursor()
    cursor.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_TYPE = 'BASE TABLE'"
    )
    existing = [row[0] for row in cursor.fetchall()]
    found = doomed(existing)
    print(f"{label}: dbo has {len(existing)} table(s); retail tables present: {found or 'none'}")
    deleted = []
    for name in found:
        cursor.execute(f"DROP TABLE [dbo].[{name}]")
        connection.commit()
        deleted.append(name)
        print(f"{label}: deleted dbo.{name}")
    report[label] = {"present": found, "deleted": deleted}


def clean_kql(post: Callable[[str], list[list[Any]]], report: dict[str, Any]) -> None:
    """List the KQL database's tables, then drop only the retail tables that exist."""
    existing = [row[0] for row in post(".show tables")]
    found = doomed(existing)
    print(f"shape_eh: {len(existing)} table(s); retail tables present: {found or 'none'}")
    deleted = []
    for name in found:
        post(f".drop table ['{name}']")
        deleted.append(name)
        print(f"shape_eh: deleted table {name}")
    report["shape_eh"] = {"present": found, "deleted": deleted}


def clean_notebook(
    items: list[dict[str, str]], delete: Callable[[str], None], report: dict[str, Any]
) -> None:
    """Delete the Notebook items whose display name is exactly :data:`NOTEBOOK`."""
    found = [i for i in items if i.get("type") == "Notebook" and i.get("displayName") == NOTEBOOK]
    print(f"notebooks named {NOTEBOOK}: {len(found)}")
    for item in found:
        delete(item["id"])
        print(f"deleted Notebook {NOTEBOOK} ({item['id']})")
    report["notebook"] = {"found": len(found), "deleted": [i["id"] for i in found]}


def main() -> int:  # pragma: no cover - runs against the live workspace
    from azure.identity import ClientSecretCredential
    from shape_fabric import _tsql

    env = os.environ
    cred = ClientSecretCredential(
        env["FABRIC_TENANT_ID"], env["FABRIC_CLIENT_ID"], env["FABRIC_CLIENT_SECRET"]
    )
    report: dict[str, Any] = {}

    token = cred.get_token("https://api.fabric.microsoft.com/.default").token
    base = f"https://api.fabric.microsoft.com/v1/workspaces/{env['FABRIC_WORKSPACE_ID']}/items"

    def call(method: str, url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url, method=method, headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
        return json.loads(body) if body else {}

    items: list[dict[str, str]] = []
    url: str | None = base
    while url:
        page = call("GET", url)
        items += page.get("value", [])
        more = page.get("continuationToken")
        url = f"{base}?continuationToken={more}" if more else None
    clean_notebook(items, lambda item_id: call("DELETE", f"{base}/{item_id}"), report)

    clean_sql("shape_sqldb", _tsql.connect(env["FABRIC_SQL_CONNECTION_STRING"], cred), report)
    clean_sql("shape_wh", _tsql.connect(env["DOCS_WAREHOUSE_CONNECTION"], cred), report)

    host, db = env["DOCS_KQL_HOST"], env["FABRIC_KQL_DB"]
    kql_token = cred.get_token(f"https://{host}/.default").token

    def post(csl: str) -> list[list[Any]]:
        req = urllib.request.Request(
            f"https://{host}/v1/rest/mgmt",
            data=json.dumps({"db": db, "csl": csl}).encode(),
            headers={"Authorization": f"Bearer {kql_token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            tables = json.loads(resp.read())["Tables"]
        return tables[0]["Rows"] if tables else []

    clean_kql(post, report)
    out = (
        Path("docs/plans/evidence/owner-notes")
        / f"preclean-{env.get('GITHUB_RUN_ID', 'local')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
