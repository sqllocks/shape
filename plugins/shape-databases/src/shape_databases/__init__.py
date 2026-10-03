"""Shape plugin: live database sinks.

* ``shape.sinks``: ``postgres`` (``postgresql://`` and ``postgres://``; ``COPY``), ``mysql``
  (``mysql://``; batched multi-row ``INSERT``), ``snowflake`` (``snowflake://``; Parquet ``PUT``
  to the table stage, then ``COPY INTO``) and ``databricks`` (``databricks://``; batched, bound
  multi-row ``INSERT`` into Delta tables of a SQL warehouse).
* :mod:`shape_databases.testing`: in-memory fake connections for tests.

The client libraries (``psycopg``, ``PyMySQL``, ``snowflake-connector-python``,
``databricks-sql-connector``) load only when a connection is opened.
"""

from ._auth import Secret
from .databricks import DatabricksSink
from .errors import CredentialError, WriteError
from .mysql import MySqlSink
from .postgres import PostgresSink
from .snowflake import SnowflakeSink

SHAPE_API = "1.0"

__all__ = [
    "SHAPE_API",
    "CredentialError",
    "DatabricksSink",
    "MySqlSink",
    "PostgresSink",
    "Secret",
    "SnowflakeSink",
    "WriteError",
]
