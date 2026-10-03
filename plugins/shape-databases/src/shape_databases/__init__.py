"""Shape plugin: live database sinks.

* ``shape.sinks``: ``postgres`` (``postgresql://`` and ``postgres://``; ``COPY``) and ``mysql``
  (``mysql://``; batched multi-row ``INSERT``).
* :mod:`shape_databases.testing`: in-memory fake connections for tests.

The client libraries (``psycopg``, ``PyMySQL``) load only when a connection is opened.
"""

from ._auth import Secret
from .errors import CredentialError, WriteError
from .mysql import MySqlSink
from .postgres import PostgresSink

SHAPE_API = "1.0"

__all__ = [
    "SHAPE_API",
    "CredentialError",
    "MySqlSink",
    "PostgresSink",
    "Secret",
    "WriteError",
]
