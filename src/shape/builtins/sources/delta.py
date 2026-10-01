"""Delta table source (``shape.sources``, extra ``[azure]`` for cloud tables).

Reads a Delta table at a local directory (one with a ``_delta_log``), or in OneLake or ADLS
Gen2 as ``delta+abfss://<container>@<host>/<path>`` (the ``delta+`` prefix says "a table, not
files"). Options: ``version`` (time travel), ``columns``, ``batch_rows``, ``storage_options``
(passed to delta-rs, overriding what authentication derived), and the credential options of
:mod:`._azure_auth`.

Cloud tables authenticate with a bearer token (delta-rs takes a token, not a credential
object), so a connection string cannot be used for them; an account key or SAS token can.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pyarrow as pa  # type: ignore[import-untyped]

from . import _azure_auth as auth
from .azure import DEFAULT_BATCH_ROWS
from .files import local_path

PREFIX = "delta+"
_ONELAKE = "onelake.dfs.fabric.microsoft.com"


def _is_cloud(uri: str) -> bool:
    return uri.startswith(PREFIX)


def _storage_options(uri: str, options: Mapping[str, Any]) -> dict[str, str]:
    opts: dict[str, str] = {}
    if "://" in uri and uri.partition("://")[0] in ("abfss", "abfs"):
        resolved = auth.resolve(options)
        keys = resolved.adlfs_options or {}
        if "connection_string" in keys:
            raise ValueError(
                "a connection string cannot open a Delta table; pass a token, a credential, "
                "an account key or a SAS token"
            )
        if "account_key" in keys:
            opts["azure_storage_account_key"] = str(keys["account_key"])
        elif "sas_token" in keys:
            opts["azure_storage_sas_key"] = str(keys["sas_token"])
        else:
            opts["azure_storage_token"] = auth.bearer_token(resolved)
        host = urlparse(uri).netloc.partition("@")[2]
        if host == _ONELAKE:
            opts["use_fabric_endpoint"] = "true"
    opts.update({str(k): str(v) for k, v in dict(options.get("storage_options") or {}).items()})
    return opts


class DeltaSource:
    """A Delta table, local or in OneLake / ADLS Gen2."""

    name = "delta"
    schemes = ("delta+abfss", "delta+abfs", "file")

    def can_open(self, uri: str) -> bool:
        if _is_cloud(uri):
            return urlparse(uri[len(PREFIX) :]).scheme in ("abfss", "abfs")
        parsed = urlparse(uri)
        if parsed.scheme not in ("", "file") and len(parsed.scheme) > 1:
            return False
        return (Path(local_path(uri)) / "_delta_log").is_dir()

    def _table(self, uri: str, options: Mapping[str, Any]) -> Any:
        if not self.can_open(uri):
            raise ValueError(f"{uri!r} is not a Delta table")
        try:
            from deltalake import DeltaTable
        except ImportError as exc:
            raise ImportError(
                "reading a Delta table needs deltalake: pip install 'sqllocks-shape[azure]'"
            ) from exc
        target = uri[len(PREFIX) :] if _is_cloud(uri) else str(local_path(uri))
        version = options.get("version")
        return DeltaTable(
            target,
            version=None if version is None else int(version),
            storage_options=_storage_options(target, options) or None,
        )

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        schema: pa.Schema = self._dataset(uri, options).schema
        columns = options.get("columns")
        return pa.schema([schema.field(c) for c in columns]) if columns else schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        batch_rows = int(options.get("batch_rows", DEFAULT_BATCH_ROWS))
        columns = options.get("columns")
        dataset = self._dataset(uri, options)
        return iter(dataset.to_batches(columns=columns, batch_size=batch_rows))

    def _dataset(self, uri: str, options: Mapping[str, Any]) -> Any:
        return self._table(uri, options).to_pyarrow_dataset()
