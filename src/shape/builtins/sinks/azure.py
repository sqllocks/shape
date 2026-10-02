"""``abfss://`` sink for OneLake and ADLS Gen2 (``shape.sinks``, extra ``[azure]``).

The counterpart of the ``abfss`` source: the same URI forms, the same authentication
(:mod:`shape.builtins.sources._azure_auth`: ``token``, ``credential``, ``account_key``,
``sas_token``, ``connection_string``, a Fabric notebook identity, ``DefaultAzureCredential``), and
the same ``filesystem`` option (any fsspec filesystem, used instead of ``adlfs``).

    abfss://<container>@<account>.dfs.core.windows.net/<folder>
    abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Files/<folder>

``uri`` is the landing folder; each table becomes files below it, laid out by ``path_template``
(default ``{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}``; with rolling
``..._{part}.{ext}``), Hive-style date partitions that a Fabric pipeline or Spark reads as a
partition column. ``{date}`` is ``batch_date`` or, when it is not given, today in UTC.

Options: ``format`` (``parquet`` (default), ``csv``, ``tsv``, ``jsonl``, ``ipc``) and ``formats``
(``{table: format}``); ``path_template``, ``batch_date``; ``roll_rows`` / ``roll_seconds`` (a new
file every N rows or seconds; see :mod:`shape.builtins.sinks._roll`); ``mode`` (``overwrite``,
``append``, ``fail``); ``manifest`` (write ``_SUCCESS`` in each folder after its files);
``streaming`` (a stream: every flush starts a new numbered file); ``retries``;
``spool_bytes``; the credential options of the source.

Atomic publish: a file is spooled while it is written, uploaded to ``_shape_tmp/`` and renamed to
its final name, so a reader never sees a partial file (:mod:`shape.io.store`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.builtins.sinks._roll import Encoder, RollingTableWriter
from shape.builtins.sinks.files import CsvSink, IpcSink, JsonlSink, ParquetSink, TsvSink
from shape.builtins.sources import azure
from shape.io.store import FsspecStore
from shape.plugins.schemes import require_scheme

SCHEMES = azure.SCHEMES
FORMATS = ("parquet", "csv", "tsv", "jsonl", "ipc")
DEFAULT_TEMPLATE = "{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}"
DEFAULT_ROLLING_TEMPLATE = "{table}/ingest_date={date}/{table}_{yyyymmdd}_{part}.{ext}"
_ENCODERS: dict[str, type[Any]] = {
    "parquet": ParquetSink,
    "csv": CsvSink,
    "tsv": TsvSink,
    "jsonl": JsonlSink,
    "ipc": IpcSink,
}


def encoder_for(fmt: str) -> Encoder:
    try:
        return _ENCODERS[fmt]()  # type: ignore[no-any-return]
    except KeyError:
        raise ValueError(
            f"unknown file format {fmt!r}; choose one of {', '.join(FORMATS)}"
        ) from None


def table_format(table: str, options: Mapping[str, Any]) -> str:
    formats = options.get("formats") or {}
    return str(formats.get(table) or options.get("format") or "parquet").lower()


def translate_error(exc: BaseException, where: str) -> BaseException | None:
    """A clearer exception for the failures a user can fix (authorization, missing container),
    or ``None`` to keep the original. Secrets are never part of the message."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    text = str(exc)
    if status in (401, 403) or "AuthorizationFailure" in text or "AuthenticationFailed" in text:
        return PermissionError(
            f"not authorized to write to {where}: check the credential, and that it has the "
            "'Storage Blob Data Contributor' role (OneLake: Contributor on the workspace)"
        )
    if "ContainerNotFound" in text or name == "ResourceNotFoundError" or status == 404:
        return FileNotFoundError(
            f"{where} was not found: check the container (or OneLake workspace and lakehouse) "
            "name, and that the storage account exists"
        )
    return None


class AbfssSink:
    """Tables as files in OneLake or ADLS Gen2, by ``abfss://`` URI."""

    name = "abfss"
    schemes = SCHEMES

    def _store(self, uri: str, options: Mapping[str, Any]) -> FsspecStore:
        loc = azure.parse(uri)
        opts = _with_environment_keys(options)
        fs = azure._filesystem(loc, opts)
        where = f"abfss://{loc.container}" + (f"@{loc.host}" if loc.host else "")
        kwargs: dict[str, Any] = {}
        if opts.get("retries") is not None:
            kwargs["retries"] = int(opts["retries"])
        if opts.get("spool_bytes") is not None:
            kwargs["spool_bytes"] = int(opts["spool_bytes"])
        if opts.get("sleep") is not None:
            kwargs["sleep"] = opts["sleep"]
        return FsspecStore(
            fs,
            loc.fs_path,
            label=f"{where}/{loc.path}".rstrip("/"),
            translate=lambda exc: translate_error(exc, where),
            **kwargs,
        )

    def open_table(
        self, uri: str, table: str, schema: pa.Schema | None = None, **options: Any
    ) -> RollingTableWriter:
        """A streaming writer for ``table`` (``write_batch``, ``flush``, ``close``)."""
        require_scheme(self, uri)
        store = self._store(uri, options)
        rolling = bool(
            options.get("roll_rows") or options.get("roll_seconds") or options.get("streaming")
        )
        template = options.get("path_template") or (
            DEFAULT_ROLLING_TEMPLATE if rolling else DEFAULT_TEMPLATE
        )
        encoder = encoder_for(table_format(table, options))
        encoder_options = {k: v for k, v in options.items() if k not in _CONTROL}
        return RollingTableWriter(
            store, table, encoder, {**encoder_options, **_pick(options)}, template=template
        )

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        writer = self.open_table(uri, table, **options)
        try:
            writer.write_all(batches)
        except BaseException:
            writer.abort()
            raise
        return int(writer.close(schema=options.get("schema")))


_ENV_KEYS = (
    ("account_key", "AZURE_STORAGE_ACCOUNT_KEY"),
    ("sas_token", "AZURE_STORAGE_SAS_TOKEN"),
    ("connection_string", "AZURE_STORAGE_CONNECTION_STRING"),
)


def _with_environment_keys(options: Mapping[str, Any]) -> dict[str, Any]:
    """``options``, plus a storage key from the standard environment variables when none of the
    credential options is given (so a secret never has to be on a command line)."""
    opts = dict(options)
    if any(opts.get(k) for k in ("token", "credential", "account_key", "sas_token", "filesystem")):
        return opts
    if opts.get("connection_string"):
        return opts
    for key, variable in _ENV_KEYS:
        value = os.environ.get(variable)
        if value:
            opts[key] = value
            break
    return opts


_CONTROL = frozenset(
    {
        "token",
        "credential",
        "account_key",
        "sas_token",
        "connection_string",
        "account_name",
        "filesystem",
        "retries",
        "spool_bytes",
        "sleep",
    }
)


def _pick(options: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("roll_rows", "roll_seconds", "mode", "batch_date", "manifest", "clock", "now")
    return {k: options[k] for k in keys if k in options}
