"""Synapse dedicated SQL pool writer: stage Parquet in ADLS Gen2, then ``COPY INTO``.

    writer = SynapseWriter(
        "Server=ws.sql.azuresynapse.net;Database=pool1",
        staging_path="abfss://staging@myacct.dfs.core.windows.net/shape",
        credential=cred,
    )
    writer.write_table("customer", batches, distribution="HASH(customer_id)")

It follows :class:`shape_fabric.warehouse.WarehouseWriter` step by step: (1) the table is prepared
with the shared T-SQL helpers (same ``write_mode`` values, same safe default, and the Warehouse's
types), (2) the rows are written as Parquet files of at most ``chunk_rows`` rows under
``<staging_path>/staging/<run>/<table>/``, (3) one ``COPY INTO ... WITH (FILE_TYPE = 'PARQUET')``
loads the folder, and (4) the staged files are deleted, **also when a step failed**. The number of
rows ``COPY INTO`` loaded must equal the number staged or the write fails, and a table this call
created is dropped.

``staging_path`` is required: an ``abfss://<container>@<account>.dfs.core.windows.net/<folder>``
folder of an ADLS Gen2 account that the pool can read (``COPY INTO`` reads it as
``https://<account>.dfs.core.windows.net/<container>/<folder>/``). ``copy_identity`` says who
``COPY INTO`` reads the storage as: ``managed_identity`` (the default; the workspace's managed
identity, ``CREDENTIAL = (IDENTITY = 'Managed Identity')``) or ``signed_in`` (the Microsoft Entra
identity of the connection, no ``CREDENTIAL`` clause; a SQL login has none, so it is refused).

Table options: ``distribution`` is ``ROUND_ROBIN`` (the default), ``HASH(column)`` or ``REPLICATE``;
``index`` is ``CLUSTERED COLUMNSTORE INDEX`` (the default) or ``HEAP``. A table that already exists
keeps its own (they apply to a table this call creates).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pyarrow as pa  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _tsql, onelake
from ._storage import Storage
from .sqldb import SqlConnection, prepare_table
from .warehouse import DEFAULT_CHUNK_ROWS, WarehouseWriter, copy_literal, staging_slug

INDEXES = ("CLUSTERED COLUMNSTORE INDEX", "HEAP")
COPY_IDENTITIES = ("managed_identity", "signed_in")
_HASH = re.compile(r"^HASH\s*\((.*)\)$", re.IGNORECASE | re.DOTALL)
_ACCOUNT = re.compile(r"^[a-z0-9]{3,24}$")
_CONTAINER = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,62}$")
_DFS_SUFFIXES = (
    ".dfs.core.windows.net",
    ".dfs.core.usgovcloudapi.net",
    ".dfs.core.chinacloudapi.cn",
)
SYNAPSE_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?\.sql\.azuresynapse\.net$")


@dataclass(frozen=True, slots=True)
class AdlsPath:
    """A folder in ADLS Gen2, with the interface the Warehouse writer uses of a OneLake path."""

    container: str
    account_host: str  # <account>.dfs.core.windows.net
    path: str = ""

    def join(self, *parts: str) -> AdlsPath:
        segs = [onelake.segment(p, "path segment") for part in parts for p in part.split("/") if p]
        return AdlsPath(
            self.container, self.account_host, "/".join([*self.path.split("/"), *segs]).strip("/")
        )

    def abfss(self) -> str:
        tail = f"/{self.path}" if self.path else ""
        return f"abfss://{self.container}@{self.account_host}{tail}"

    def https(self) -> str:
        tail = f"/{self.path}" if self.path else ""
        return f"https://{self.account_host}/{self.container}{tail}"

    def __str__(self) -> str:
        return self.abfss()


def parse_staging(uri: str) -> AdlsPath:
    """The ADLS Gen2 folder ``abfss://<container>@<account>.dfs.core.windows.net/<folder>``."""
    parts = urlsplit(uri) if isinstance(uri, str) else None
    if parts is None or parts.scheme != "abfss":
        raise ShapeError(
            "staging_path must be an ADLS Gen2 folder: "
            "abfss://<container>@<account>.dfs.core.windows.net/<folder>"
        )
    container, _, host = parts.netloc.partition("@")
    host = host.lower()
    account = host.split(".", 1)[0]
    if (
        not _CONTAINER.match(container)
        or not host.endswith(_DFS_SUFFIXES)
        or not _ACCOUNT.match(account)
    ):
        raise ShapeError(
            "staging_path must be an ADLS Gen2 folder: "
            "abfss://<container>@<account>.dfs.core.windows.net/<folder>"
        )
    base = AdlsPath(container, host)
    segments = [s for s in parts.path.split("/") if s]
    return base.join(*segments) if segments else base


def distribution_clause(distribution: str | None, schema: pa.Schema) -> str:
    """``DISTRIBUTION = ...`` for a table; a hash column must be a column of ``schema``."""
    text = "ROUND_ROBIN" if distribution is None else distribution
    if not isinstance(text, str):
        raise ShapeError("distribution must be ROUND_ROBIN, REPLICATE or HASH(column)")
    clean = " ".join(text.split())
    if clean.upper() in ("ROUND_ROBIN", "REPLICATE"):
        return f"DISTRIBUTION = {clean.upper()}"
    match = _HASH.match(clean)
    if match is None:
        raise ShapeError(f"distribution {text!r} is not ROUND_ROBIN, REPLICATE or HASH(column)")
    column = match.group(1).strip()
    if column not in schema.names:
        raise ShapeError(
            f"HASH distribution column {column!r} is not a column of the table "
            f"(columns: {', '.join(schema.names)})"
        )
    return f"DISTRIBUTION = HASH ({_tsql.ident(column)})"


def index_clause(index: str | None) -> str:
    text = "CLUSTERED COLUMNSTORE INDEX" if index is None else index
    clean = " ".join(str(text).split()).upper()
    if clean not in INDEXES:
        raise ShapeError(f"index must be one of {', '.join(INDEXES)}, not {text!r}")
    return clean


def table_options(distribution: str | None, index: str | None, schema: pa.Schema) -> str:
    """The text inside ``WITH (...)`` of ``CREATE TABLE``."""
    return f"{distribution_clause(distribution, schema)}, {index_clause(index)}"


def copy_into_sql(schema_name: str, table: str, folder_url: str, copy_identity: str) -> str:
    """One ``COPY INTO`` over a staged folder. The table name is quoted by
    :func:`shape_fabric._tsql.qualified`; the location passed
    :func:`shape_fabric.warehouse.copy_literal`'s check."""
    if copy_identity not in COPY_IDENTITIES:
        raise ShapeError(f"copy_identity must be one of {', '.join(COPY_IDENTITIES)}")
    credential = (
        ", CREDENTIAL = (IDENTITY = 'Managed Identity')"
        if copy_identity == "managed_identity"
        else ""
    )
    return (
        f"COPY INTO {_tsql.qualified(schema_name, table)} "
        f"FROM {copy_literal(folder_url)} WITH (FILE_TYPE = 'PARQUET'{credential})"
    )  # nosec B608


class SynapseWriter(WarehouseWriter):
    """Tables into a Synapse dedicated SQL pool by ``COPY INTO``; see the module docstring."""

    def __init__(
        self,
        connection_string: str | None = None,
        staging_path: str | None = None,
        *,
        credential: Any = None,
        connection: Any = None,
        connect: Callable[..., Any] | None = None,
        filesystem: Any = None,
        storage: Storage | None = None,
        schema_name: str = "dbo",
        run_id: str | None = None,
        copy_identity: str = "managed_identity",
    ) -> None:
        if not staging_path:
            raise ShapeError(
                "a Synapse bulk load needs staging_path: an ADLS Gen2 folder "
                "(abfss://<container>@<account>.dfs.core.windows.net/<folder>)"
            )
        if copy_identity not in COPY_IDENTITIES:
            raise ShapeError(f"copy_identity must be one of {', '.join(COPY_IDENTITIES)}")
        if (
            copy_identity == "signed_in"
            and connection_string
            and _tsql._LOGIN_KEYS.search(connection_string)  # noqa: SLF001
        ):
            raise ShapeError(
                "copy_identity='signed_in' reads the storage as the connection's Microsoft "
                "Entra identity, which a SQL login (UID/PWD) does not have: use "
                "copy_identity='managed_identity' or sign in with Entra"
            )
        self.staging = parse_staging(staging_path)  # type: ignore[assignment]
        # a dedicated pool is not a Fabric Warehouse, but takes its types (varchar(8000), no
        # (n)varchar(max)), which is what a clustered columnstore index needs
        self.db = SqlConnection(connection_string, credential, connection, connect, True)
        self.storage = storage or Storage(credential=credential, filesystem=filesystem)
        self.schema_name = schema_name
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.copy_identity = copy_identity
        self._distribution: str | None = None
        self._index: str | None = None

    def _folder(self, table: str) -> Any:
        return self.staging.join("staging", self.run_id, staging_slug(table))

    def write_table(
        self,
        table: str,
        batches: Any,
        *,
        distribution: str | None = None,
        index: str | None = None,
        **options: Any,
    ) -> int:
        """Write one table; return the rows loaded (see
        :meth:`shape_fabric.warehouse.WarehouseWriter.write_table`)."""
        self._distribution, self._index = distribution, index
        try:
            return super().write_table(table, batches, **options)
        finally:
            self._distribution = self._index = None

    def _prepare(
        self,
        schema_name: str,
        table: str,
        mode: str,
        schema: pa.Schema,
        columns: Mapping[str, Mapping[str, Any]] | None,
        primary_key: Sequence[str],
    ) -> bool:
        # checked before the connection is opened (it opens at the first statement)
        options = table_options(self._distribution, self._index, schema)
        return prepare_table(
            self.db,
            schema_name,
            table,
            mode,
            schema,
            columns=columns,
            primary_key=primary_key,
            options=options,
            synapse=True,
        )

    def _copy_statement(self, schema_name: str, table: str, folder: Any) -> str:
        return copy_into_sql(schema_name, table, folder.https() + "/", self.copy_identity)


__all__ = [
    "DEFAULT_CHUNK_ROWS",
    "AdlsPath",
    "SynapseWriter",
    "copy_into_sql",
    "distribution_clause",
    "index_clause",
    "parse_staging",
    "table_options",
]
