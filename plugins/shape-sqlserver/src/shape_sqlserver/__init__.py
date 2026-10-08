"""Shape plugin: SQL Server, Azure SQL and Fabric SQL.

* ``shape.sources``: ``mssql`` reads one table as Arrow record batches.
* ``shape.commands``: ``shape profile-db`` profiles a database schema.
* helpers shared with ``shape-fabric``: :mod:`shape_sqlserver.sql` (identifier quoting,
  connection strings, catalog queries, type maps) and :mod:`shape_sqlserver.auth`
  (connections, Microsoft Entra tokens).

``pyodbc`` and ``azure-identity`` load only when a connection is opened.
"""

from .auth import Credentials, connect
from .command import ProfileDbCommand
from .profiler import profile_database
from .source import SqlServerSource
from .sql import SqlServerError

SHAPE_API = "1.0"

__all__ = [
    "SHAPE_API",
    "Credentials",
    "ProfileDbCommand",
    "SqlServerError",
    "SqlServerSource",
    "connect",
    "profile_database",
]
