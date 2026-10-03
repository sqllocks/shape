"""Service operations the ``shape demo`` command needs: remove what a demo wrote, and check that
a target can be reached.

* ``drop_sql_table`` and ``drop_kql_table`` remove one table a demo created (names are quoted
  and checked like every statement of the writers);
* ``remove_files`` removes a folder or file below a local folder, ``abfss://`` or ``onelake://``;
* ``check_sql``, ``check_kql`` and ``check_files`` do one cheap round trip and return a short
  message, or raise (a failed sign-in, an unreachable host, a missing folder).

Each takes the same ``credential`` as the writers (:mod:`shape_fabric._auth`) and the same seams
(``connect``, ``transport``, ``filesystem``) so the contract tests replace the network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shape.errors import ShapeError

from . import _tsql
from ._storage import Storage
from .eventhouse import parse_uri, token_source
from .kusto import KustoClient, Transport, drop_table_command
from .sqldb import SqlConnection


def _connection(
    connection_string: str, credential: Any, connect: Callable[..., Any] | None
) -> SqlConnection:
    return SqlConnection(connection_string, credential, None, connect, None)


def drop_sql_table(
    connection_string: str,
    schema_name: str,
    table: str,
    *,
    credential: Any = None,
    connect: Callable[..., Any] | None = None,
) -> None:
    """``DROP TABLE IF EXISTS`` for ``schema_name.table`` (both quoted); committed."""
    db = _connection(connection_string, credential, connect)
    try:
        db.execute(_tsql.drop_table_sql(schema_name, table, if_exists=True))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_sql(
    connection_string: str,
    *,
    credential: Any = None,
    connect: Callable[..., Any] | None = None,
) -> str:
    """Connect and run ``SELECT 1``; the message names the server kind."""
    db = _connection(connection_string, credential, connect)
    try:
        cursor = db.execute("SELECT 1")
        if cursor.fetchone() is None:
            raise ShapeError("the server answered SELECT 1 with no row")
        return "warehouse reachable" if db.warehouse else "database reachable"
    finally:
        db.close()


def _kusto(
    uri: str, token: Any, credential: Any, transport: Transport | None
) -> tuple[KustoClient, str]:
    target = parse_uri(uri)
    client = KustoClient(
        target.kusto, token_source(target.kusto, token, credential), transport=transport
    )
    return client, target.database


def drop_kql_table(
    uri: str,
    table: str,
    *,
    token: Any = None,
    credential: Any = None,
    transport: Transport | None = None,
) -> None:
    """``.drop table <table> ifexists`` in the database ``uri`` names."""
    client, _ = _kusto(uri, token, credential, transport)
    client.mgmt(drop_table_command(table))


def check_kql(
    uri: str,
    *,
    token: Any = None,
    credential: Any = None,
    transport: Transport | None = None,
) -> str:
    """Run ``print 1`` against the database ``uri`` names."""
    client, database = _kusto(uri, token, credential, transport)
    rows = client.query("print 1")
    if not rows:
        raise ShapeError("the Eventhouse answered a query with no row")
    return f"database {database} reachable"


def remove_files(
    path: str, *, credential: Any = None, filesystem: Any = None, recursive: bool = True
) -> None:
    """Remove the file or folder ``path`` (not an error when it is not there)."""
    Storage(credential=credential, filesystem=filesystem).remove(path, recursive=recursive)


def check_files(path: str, *, credential: Any = None, filesystem: Any = None) -> str:
    """Check that the folder ``path`` can be reached."""
    if not Storage(credential=credential, filesystem=filesystem).exists(path):
        raise ShapeError(f"{path} does not exist or cannot be read")
    return "folder reachable"
