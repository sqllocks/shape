"""Add the seeded retail tables and relationships to the semantic model ``shape_sm`` (REST, TMDL).

Run by the dispatch-only ``seed`` job of ``.github/workflows/fabric-live.yml`` after
``fabric_seed_retail.py``. Modes (``SM_MODE``): ``inspect`` only downloads the model definition and
lists its parts; ``update`` (default) downloads it, adds the tables and relationships, uploads it
(``updateDefinition``) and downloads it again to confirm. Every part of the downloaded definition is
written under ``SM_DIR`` (default ``sm-definition``) for the run's artifact. Every HTTP failure is
printed with the response body and fails the script: nothing is assumed to have worked. The log is
public: everything printed goes through ``ci_redact.redact`` (GUIDs and the SQL endpoint host
replaced); the artifact files keep the definition as Fabric returned it.

Tables added: customer, order, order_line, product (Direct Lake on the Lakehouse SQL endpoint,
schema ``FABRIC_LAKEHOUSE_SCHEMA`` or ``dbo``), with ``order.customer_id -> customer.customer_id``,
``order_line.order_id -> order.order_id`` and ``order_line.product_id -> product.product_id``.
``order.product_id`` does not exist in the retail domain, so that relationship is reported skipped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from shape_fabric import _tsql, onelake
from shape_fabric.fabric_api import SCOPE_FABRIC

from shape.scale.http import Http, HttpError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_redact import hide, redact  # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
TABLES = ("customer", "order", "order_line", "product")
# The retail `order` table has no product_id (a product is on `order_line`), so the requested
# order.product_id -> product.product_id relationship is reported as skipped, not invented; the
# order_line relationships carry the order -> product path.
RELATIONSHIPS = (
    ("order", "customer_id", "customer", "customer_id"),
    ("order", "product_id", "product", "product_id"),
    ("order_line", "order_id", "order", "order_id"),
    ("order_line", "product_id", "product", "product_id"),
)
TOM = {
    "int": "int64", "bigint": "int64", "smallint": "int64", "tinyint": "int64",
    "varchar": "string", "char": "string", "nvarchar": "string", "uniqueidentifier": "string",
    "decimal": "decimal", "numeric": "decimal", "float": "double", "real": "double",
    "bit": "boolean", "date": "dateTime", "datetime2": "dateTime", "datetime": "dateTime",
    "time": "string", "varbinary": "binary",
}  # fmt: skip


def need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"semantic model update needs {name}")
    return value


class Fabric:
    def __init__(self, credential: Any) -> None:
        self.credential = credential

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201, 202)):
        token = self.credential.get_token(SCOPE_FABRIC).token
        url = path if path.startswith("https://") else API + path
        try:
            return Http(token).request(method, url, body=body, ok=ok)
        except HttpError as exc:
            print(redact(f"{method} {url} -> {exc.status}\n{exc.text}"))
            raise

    def lro(self, method: str, path: str, body: Any = None, result: bool = True) -> dict[str, Any]:
        response = self.call(method, path, body)
        if response.status != 202:
            return response.json_object()
        location = response.headers.get("location", "")
        for _ in range(120):
            time.sleep(float(response.headers.get("retry-after", "3") or 3))
            state = self.call("GET", location)
            doc = state.json_object()
            if doc.get("status") == "Succeeded":
                if not result:  # updateDefinition has no result to fetch (OperationHasNoResult)
                    return {}
                fetched = self.call("GET", location.rstrip("/") + "/result", ok=(200, 204))
                return fetched.json_object() if fetched.status == 200 else {}
            if doc.get("status") in ("Failed", "Cancelled"):
                raise RuntimeError(redact(f"operation {doc.get('status')}: {json.dumps(doc)}"))
            response = state
        raise RuntimeError("operation did not finish in time")


def decode(parts: list[dict[str, Any]]) -> dict[str, str]:
    return {p["path"]: base64.b64decode(p["payload"]).decode("utf-8") for p in parts}


def encode(files: dict[str, str]) -> list[dict[str, str]]:
    return [
        {
            "path": path,
            "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64",
        }
        for path, text in sorted(files.items())
    ]


def dump(files: dict[str, str], folder: str) -> None:
    for path, text in files.items():
        target = Path(folder, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"--- {folder}/{path} ({len(text)} bytes)")
        if len(text) < 8000:  # small definition parts are printed: the record of what was there
            print(redact(text))


def expression_source(files: dict[str, str]) -> str:
    """The ``expressionSource`` of the model's existing Direct Lake partitions, as TMDL writes it:
    a plain word, or a name in single quotes (``'DirectLake - shape_lh'``, a quote doubled)."""
    for text in files.values():
        found = re.search(r"expressionSource:[ \t]*('(?:[^']|'')*'|\S+)", text)
        if found:
            return found.group(1)
    return ""


def quote(name: str) -> str:
    return (
        name
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
        else "'" + name.replace("'", "''") + "'"
    )


def columns_of(connection: str, credential: Any, schema: str, table: str):
    conn = _tsql.connect(connection, credential)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, "
            "NUMERIC_SCALE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? "
            "AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            schema,
            table,
        )
        return [tuple(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def provider_type(data_type: str, length, precision, scale) -> str:
    if data_type in ("varchar", "char", "varbinary"):
        return f"{data_type}({'max' if length in (None, -1) else length})"
    if data_type in ("decimal", "numeric"):
        return f"{data_type}({precision}, {scale})"
    return data_type


def table_tmdl(table: str, schema: str, cols, expression: str) -> str:
    out = [
        f"table {quote(table)}",
        f"\tlineageTag: {uuid.uuid4()}",
        f"\tsourceLineageTag: [{schema}].[{table}]",
        "",
    ]
    for name, data_type, length, precision, scale in cols:
        kind = TOM.get(data_type, "string")
        summarize = (
            "none"
            if kind in ("string", "dateTime", "boolean", "binary") or name.endswith("_id")
            else "sum"
        )
        out += [
            f"\tcolumn {quote(name)}",
            f"\t\tdataType: {kind}",
            f"\t\tsourceProviderType: {provider_type(data_type, length, precision, scale)}",
            f"\t\tlineageTag: {uuid.uuid4()}",
            f"\t\tsourceLineageTag: {name}",
            f"\t\tsummarizeBy: {summarize}",
            f"\t\tsourceColumn: {name}",
            "",
            "\t\tannotation SummarizationSetBy = Automatic",
            "",
        ]
    out += [
        f"\tpartition {quote(table)} = entity",
        "\t\tmode: directLake",
        "\t\tsource",
        f"\t\t\tentityName: {table}",
        f"\t\t\tschemaName: {schema}",
        f"\t\t\texpressionSource: {expression}",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    tables_uri = need("FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH").rstrip("/")
    schema = os.environ.get("FABRIC_LAKEHOUSE_SCHEMA") or "dbo"
    mode = os.environ.get("SM_MODE") or "update"
    name = os.environ.get("SM_NAME") or "shape_sm"
    folder = os.environ.get("SM_DIR") or "sm-definition"
    where = onelake.parse(tables_uri)
    from azure.identity import ClientSecretCredential

    credential = ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )
    api = Fabric(credential)
    models = api.call("GET", f"/workspaces/{where.workspace}/semanticModels").json_object()
    found = [m for m in models.get("value", []) if m.get("displayName") == name]
    if len(found) != 1:
        print(f"semantic models: {[m.get('displayName') for m in models.get('value', [])]}")
        return 1
    model_id = found[0]["id"]
    print(redact(f"semantic model {name} = {model_id}"))
    base = f"/workspaces/{where.workspace}/semanticModels/{model_id}"
    got = api.lro("POST", f"{base}/getDefinition?format=TMDL")
    files = decode(got["definition"]["parts"])
    dump(files, folder)
    if mode == "inspect":
        return 0

    item = api.call("GET", f"/workspaces/{where.workspace}/lakehouses/{where.item}").json_object()
    props = item["properties"]["sqlEndpointProperties"]
    hide(props["connectionString"])
    connection = f"Data Source={props['connectionString']};Initial Catalog={item['displayName']}"
    expression = os.environ.get("SM_EXPRESSION") or expression_source(files)
    expression = expression or "DatabaseQuery"
    print(redact(f"expression source: {expression}"))

    added: dict[str, list[str]] = {}
    for table in TABLES:
        cols = columns_of(connection, credential, schema, table)
        if not cols:
            print(f"{schema}.{table} is not visible in the SQL endpoint: seed first")
            return 1
        added[table] = [c[0] for c in cols]
        key = f"definition/tables/{table}.tmdl"
        if key in files:  # already in the model (an earlier run): keep it as it is
            print(f"table {table} is already in the model: kept")
        else:
            files[key] = table_tmdl(table, schema, cols, expression)
    model = files["definition/model.tmdl"].rstrip("\n")
    for table in TABLES:
        if f"ref table {quote(table)}" not in model:
            model += f"\nref table {quote(table)}"
    files["definition/model.tmdl"] = model + "\n"

    relationships = files.get("definition/relationships.tmdl", "").rstrip("\n")
    skipped = []
    for child, ccol, parent, pcol in RELATIONSHIPS:
        if ccol not in added[child] or pcol not in added[parent]:
            skipped.append(f"{child}.{ccol} -> {parent}.{pcol}")
            continue
        pair = (
            f"fromColumn: {quote(child)}.{quote(ccol)}\n\ttoColumn: {quote(parent)}.{quote(pcol)}"
        )
        if pair in relationships:
            print(f"relationship {child}.{ccol} -> {parent}.{pcol} is already in the model: kept")
            continue
        relationships += (
            f"\n\nrelationship {uuid.uuid4()}\n\tfromColumn: {quote(child)}.{quote(ccol)}"
            f"\n\ttoColumn: {quote(parent)}.{quote(pcol)}\n"
        )
    files["definition/relationships.tmdl"] = relationships.lstrip("\n") + "\n"
    print(f"relationships not added (column absent): {skipped}")
    dump(
        {
            k: v
            for k, v in files.items()
            if k in ("definition/model.tmdl", "definition/relationships.tmdl")
        },
        folder + "-new",
    )
    parts = encode(files)
    api.lro(
        "POST",
        f"{base}/updateDefinition",
        {"definition": {"format": "TMDL", "parts": parts}},
        result=False,
    )
    print("updateDefinition accepted")
    again = decode(api.lro("POST", f"{base}/getDefinition?format=TMDL")["definition"]["parts"])
    dump(again, folder + "-after")
    ok = all(f"definition/tables/{t}.tmdl" in again for t in TABLES)
    shown = again.get("definition/relationships.tmdl", "(none)")
    print(redact(f"tables present after update: {ok}; relationships:\n{shown}"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
