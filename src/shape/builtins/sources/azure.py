"""``abfss://`` sources for OneLake and ADLS Gen2 (``shape.sources``, extra ``[azure]``).

``abfss://<container>@<host>/<path>`` (OneLake: ``abfss://<workspace>@onelake.dfs.fabric.
microsoft.com/<lakehouse>.Lakehouse/Files/<path>``) names one file, a glob, or a directory of
files of one kind (CSV, Parquet, JSONL or Arrow IPC). ``abfss://<container>/<path>`` works with
an ``account_name`` option or a connection string (the Azurite emulator uses that form).

The host must be an Azure Storage, OneLake or sovereign-cloud storage host; any other host is
refused before a credential is attached.

Authentication follows :mod:`._azure_auth`. Files stream through ``adlfs``; nothing is imported
from an Azure package until a read needs it. Options: ``token``, ``credential``,
``account_key``, ``sas_token``, ``connection_string``, ``account_name``, ``batch_rows``, and
``filesystem`` (any fsspec filesystem, used instead of adlfs).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.json as pajson  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.io import file_kind

from . import _azure_auth as auth

SCHEMES = ("abfss", "abfs")
DEFAULT_BATCH_ROWS = 65_536
_GLOB = "*?["
_COMPRESSION = {".gz": "gzip", ".bz2": "bz2", ".zst": "zstd", ".lz4": "lz4"}
_ADLS_SUFFIX = ".dfs.core.windows.net"
# Storage account endpoints (public, US Government and China clouds) and OneLake (global and
# regional). A credential is only ever sent to one of these, never to a host a URI names.
_STORAGE_HOST = re.compile(
    r"(?:[a-z0-9]+\.(?:dfs|blob)\.core\.(?:windows\.net|usgovcloudapi\.net|chinacloudapi\.cn)"
    r"|(?:[a-z0-9]+-)?onelake\.(?:dfs|blob)\.fabric\.microsoft\.com)",
    re.IGNORECASE,
)


def check_host(host: str) -> None:
    """``ValueError`` unless ``host`` is an Azure Storage, OneLake or sovereign-cloud host."""
    if not _STORAGE_HOST.fullmatch(host):
        raise ValueError(
            f"{host!r} is not an Azure Storage or OneLake host: use "
            "<account>.dfs.core.windows.net or onelake.dfs.fabric.microsoft.com "
            "(or the US Government and China cloud equivalents)"
        )


@dataclass(frozen=True, slots=True)
class Location:
    """A parsed ``abfss://`` URI."""

    container: str
    host: str | None  # e.g. "acct.dfs.core.windows.net"; None for the short form
    path: str  # inside the container, no leading slash

    @property
    def account(self) -> str | None:
        return self.host.split(".")[0] if self.host else None

    @property
    def fs_path(self) -> str:
        """The path as adlfs wants it: ``container/path``."""
        return f"{self.container}/{self.path}".rstrip("/") if self.path else self.container


def parse(uri: str) -> Location:
    parsed = urlparse(uri)
    if parsed.scheme not in SCHEMES:
        raise ValueError(f"{uri!r} is not an abfss:// URI")
    container, _, host = parsed.netloc.partition("@")
    if not container:
        raise ValueError(f"{uri!r} has no container (abfss://<container>[@<host>]/<path>)")
    if host:
        check_host(host)
    return Location(container, host or None, unquote(parsed.path).lstrip("/"))


def _filesystem(loc: Location, options: Mapping[str, Any]) -> Any:
    given = options.get("filesystem")
    if given is not None:
        return given
    if loc.host:
        check_host(loc.host)  # before any credential is resolved or attached
    resolved = auth.resolve(options)
    try:
        import adlfs
    except ImportError as exc:
        raise ImportError(
            "reading abfss:// needs adlfs and azure-identity: pip install 'sqllocks-shape[azure]'"
        ) from exc
    kwargs: dict[str, Any] = dict(resolved.adlfs_options or {})
    if resolved.credential is not None:
        kwargs["credential"] = resolved.credential
    if "connection_string" not in kwargs:
        account = (
            options.get("account_name")
            or loc.account
            or os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
        )
        if not account:
            raise ValueError(
                "no storage account: use abfss://<container>@<account>.dfs.core.windows.net/... "
                "or pass account_name"
            )
        kwargs["account_name"] = account
        if loc.host and not loc.host.endswith(_ADLS_SUFFIX):  # OneLake and sovereign clouds
            kwargs["account_host"] = loc.host.replace(".dfs.", ".blob.", 1)
    return adlfs.AzureBlobFileSystem(**kwargs)


def _suffix_compression(path: str) -> tuple[str, str | None]:
    """-> (path without a compression suffix, pyarrow codec or None)."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _COMPRESSION:
        return path[: -len(suffix)], _COMPRESSION[suffix]
    return path, None


def _kind(path: str) -> str | None:
    return file_kind(PurePosixPath(path))  # type: ignore[arg-type]


def _list_files(fs: Any, root: str) -> list[str]:
    if any(ch in root for ch in _GLOB):
        found = sorted(p for p in fs.glob(root) if fs.isfile(p))
    elif fs.isfile(root):
        found = [root]
    elif fs.isdir(root):
        found = sorted(p for p in fs.find(root) if not PurePosixPath(p).name.startswith((".", "_")))
    else:
        raise FileNotFoundError(f"abfss path not found: {root}")
    found = [p for p in found if _kind(_suffix_compression(p)[0]) is not None]
    if not found:
        raise FileNotFoundError(f"no CSV, Parquet, JSONL or IPC files at {root}")
    kinds = {_kind(_suffix_compression(p)[0]) for p in found}
    if len(kinds) != 1:
        raise ValueError(
            f"files of mixed types cannot be read as one table: {sorted(str(k) for k in kinds)}"
        )
    return found


class _Opened:
    """One remote file opened as ``schema`` plus a batch iterator; ``close`` releases it."""

    def __init__(self, fs: Any, path: str, batch_rows: int) -> None:
        bare, codec = _suffix_compression(path)
        self.kind = _kind(bare) or ""
        self._file = fs.open(path, "rb")
        self._stream: Any = self._file
        if codec is not None:
            self._stream = pa.CompressedInputStream(pa.PythonFile(self._file, mode="r"), codec)
        self._batch_rows = batch_rows
        try:
            self._start()
        except BaseException:
            self.close()
            raise

    def _start(self) -> None:
        if self.kind == "parquet":
            reader = pq.ParquetFile(self._stream)
            self.schema: pa.Schema = reader.schema_arrow
            self._iter: Iterator[pa.RecordBatch] = reader.iter_batches(batch_size=self._batch_rows)
        elif self.kind == "csv":
            opened = pacsv.open_csv(
                self._stream, read_options=pacsv.ReadOptions(block_size=8 << 20)
            )
            self.schema = opened.schema
            self._iter = iter(opened)
        elif self.kind == "ipc":
            ipc = self._ipc()
            self.schema = ipc.schema
            self._iter = _ipc_batches(ipc)
        else:  # jsonl
            table = pajson.read_json(self._stream)
            self.schema = table.schema
            self._iter = iter(table.to_batches(max_chunksize=self._batch_rows))

    def _ipc(self) -> Any:
        try:
            return pa.ipc.open_file(self._stream)
        except pa.ArrowInvalid:
            self._stream.seek(0)
            return pa.ipc.open_stream(self._stream)

    def batches(self) -> Iterator[pa.RecordBatch]:
        return self._iter

    def close(self) -> None:
        handles = [self._stream] if self._stream is self._file else [self._stream, self._file]
        for handle in handles:
            try:
                handle.close()
            except Exception:  # noqa: S110 - closing twice or after a failed open is harmless
                pass


def _ipc_batches(reader: Any) -> Iterator[pa.RecordBatch]:
    if hasattr(reader, "num_record_batches"):
        for i in range(reader.num_record_batches):
            yield reader.get_batch(i)
    else:
        yield from reader


def _conform(batch: pa.RecordBatch, schema: pa.Schema, origin: str) -> pa.RecordBatch:
    if batch.schema.equals(schema):
        return batch
    missing = [n for n in schema.names if n not in batch.schema.names]
    if missing:
        raise ValueError(f"{origin} lacks columns {missing} that the first file has")
    table = pa.Table.from_batches([batch]).select(schema.names).cast(schema)
    out = table.combine_chunks().to_batches()
    return out[0] if out else pa.RecordBatch.from_pylist([], schema=schema)


class AbfssSource:
    """Files in OneLake and ADLS Gen2, by ``abfss://`` URI."""

    name = "abfss"
    schemes = SCHEMES

    def can_open(self, uri: str) -> bool:
        return urlparse(uri).scheme in SCHEMES

    def _setup(self, uri: str, options: dict[str, Any]) -> tuple[Any, list[str], int]:
        batch_rows = int(options.pop("batch_rows", DEFAULT_BATCH_ROWS))
        loc = parse(uri)
        fs = _filesystem(loc, options)
        return fs, _list_files(fs, loc.fs_path), batch_rows

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        fs, files, batch_rows = self._setup(uri, options)
        opened = _Opened(fs, files[0], batch_rows)
        try:
            schema: pa.Schema = opened.schema
        finally:
            opened.close()
        return schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        fs, files, batch_rows = self._setup(uri, options)
        return self._read(fs, files, batch_rows)

    @staticmethod
    def _read(fs: Any, files: list[str], batch_rows: int) -> Iterator[pa.RecordBatch]:
        schema: pa.Schema | None = None
        for path in files:
            opened = _Opened(fs, path, batch_rows)
            try:
                if schema is None:
                    schema = opened.schema
                for batch in opened.batches():
                    yield _conform(batch, schema, path)
            finally:
                opened.close()
