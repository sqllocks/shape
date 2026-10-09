"""Snowflake sink: ``snowflake://<user>@<account>/<database>/<schema>?warehouse=WH&role=ROLE``.

Client: ``snowflake-connector-python`` (``pip install 'sqllocks-shape-databases[snowflake]'``),
loaded only when a connection opens.

How rows go in. Each table is written as Parquet files of at most ``chunk_rows`` rows (default
1,000,000) in a private temporary folder, uploaded with ``PUT`` to the table's own internal stage
(``@%"TABLE"``), and loaded with one statement::

    COPY INTO "TABLE" FROM @%"TABLE" FILE_FORMAT = (TYPE = PARQUET)
        MATCH_BY_COLUMN_NAME = CASE_SENSITIVE PURGE = TRUE

The number of rows ``COPY INTO`` reports as loaded must equal the number staged, or the write
fails (and a table this call created is dropped). The staged files are removed afterwards with
``REMOVE``, also when a step failed; local files are removed in every case.

Names. Every table and column is created exactly as named (quoted, so case is kept: a table
``customer`` is ``"customer"``, not ``CUSTOMER``). A name over 255 characters, one with a control
character, or one that starts or ends with a space is refused before any connection. ``schema_name``
(default: the schema in the URI) qualifies the table.

Transactions. Snowflake commits DDL at once. The connection runs with ``autocommit`` off, so the
``COPY INTO`` is one transaction: a failure rolls the rows back, and a table this call created is
dropped again. ``replace`` has already dropped the old table, and ``truncate`` has already emptied
it. With ``commit_rows=N`` the files hold at most ``N`` rows and every ``N`` staged rows are
loaded (``COPY INTO``) and committed, so readers see rows during a stream; a failure then keeps
the committed rows and the table, and :class:`~shape_databases.WriteError` says how many
(``rows_committed``).

Sign-in (a password is never accepted in the URI): a key pair, ``private_key`` as a reference
(``file://PATH`` or ``env://NAME``; the key is a PEM text, so the command line, which resolves
references before the sink sees them, passes the PEM itself) and an optional
``private_key_passphrase``; or a password, the ``password`` option (a reference or the value) or
``SNOWFLAKE_PASSWORD``.

Options: ``write_mode``, ``schema_name``, ``table_prefix``, ``commit_rows``, ``columns``,
``primary_key``, ``schema`` (see :mod:`shape_databases.postgres`) and ``chunk_rows``.
"""

from __future__ import annotations

import importlib
import shutil
import tempfile
import warnings
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.errors import ShapeError

from . import _cloud, _sql
from ._auth import Secret, resolve_password
from ._base import DatabaseSink, Plan, Target, as_text, chain_batches, parse_uri
from .errors import CredentialError

INSTALL = "pip install 'sqllocks-shape-databases[snowflake]'"
_PEM_START = "-----BEGIN"
_COPY_OK = {"LOADED"}


def _split_path(target: Target) -> Target:
    """``/<database>/<schema>`` of the URI: the database stays, the schema goes to ``params``."""
    parts = [p for p in (target.database or "").split("/") if p != ""]
    if len(parts) > 2:
        raise ShapeError(
            "the destination must be snowflake://<user>@<account>/<database>/<schema>: "
            "too many path segments"
        )
    params = dict(target.params)
    if len(parts) == 2:
        params["schema"] = parts[1]
    return Target(
        host=target.host,
        port=target.port,
        user=target.user,
        database=parts[0] if parts else None,
        params=params,
    )


def private_key_pem(value: Any) -> Secret:
    """The PEM text behind ``private_key``: a ``file://`` or ``env://`` reference, or the PEM
    itself (what the command line hands over once it has resolved the reference)."""
    from shape.security import credrefs

    if not isinstance(value, str) or not value.strip():
        raise CredentialError("private_key must be a file:// or env:// reference")
    text = value
    if credrefs.is_reference(value):
        try:
            text = credrefs.resolve_reference(value)
        except credrefs.CredentialReferenceError as exc:
            raise CredentialError(str(exc)) from None
    if _PEM_START not in text:
        raise CredentialError(
            "private_key must be a file:// or env:// reference to a PEM private key"
        )
    return Secret(text)


def der_from_pem(pem: str, passphrase: str | None) -> bytes:
    """The unencrypted PKCS#8 DER the connector takes, from a PEM private key."""
    failure: CredentialError | None = None
    try:
        serialization = importlib.import_module("cryptography.hazmat.primitives.serialization")
        key = serialization.load_pem_private_key(
            pem.encode(), password=passphrase.encode() if passphrase else None
        )
        return bytes(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    except ImportError:
        raise ShapeError(f"key-pair sign-in needs the cryptography package: {INSTALL}") from None
    except Exception:  # noqa: BLE001 - the driver's text could hold key material
        failure = CredentialError(
            "the private key could not be read (not a PEM private key, or the passphrase is wrong)"
        )
    raise failure


class SnowflakeSink(DatabaseSink):
    """``RecordBatch``es for one table to a Snowflake table: Parquet, ``PUT``, ``COPY INTO``."""

    name = "snowflake"
    schemes = ("snowflake",)
    dialect = "snowflake"
    password_env = ("SNOWFLAKE_PASSWORD",)
    uri_params = {"warehouse": as_text, "role": as_text}
    exists_sql = (
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = COALESCE(%s, CURRENT_SCHEMA()) AND table_name = %s"
    )

    # -- plan --------------------------------------------------------------------------------
    def parse_target(self, uri: str) -> Target:
        target = _split_path(parse_uri(uri, self.schemes, self.uri_params))
        if not target.host:
            raise ShapeError("the destination needs an account: snowflake://<user>@<account>/...")
        if not target.user:
            raise ShapeError("the destination needs a user: snowflake://<user>@<account>/...")
        if target.port:
            raise ShapeError("a Snowflake account has no port in the URI")
        return target

    def plan(self, uri: str, table: str, options: Mapping[str, Any]) -> Plan:
        plan = super().plan(uri, table, options)
        chunk = options.get("chunk_rows", _cloud.DEFAULT_CHUNK_ROWS)
        _sql.positive_int(chunk, "chunk_rows")
        uri_schema = plan.target.params.get("schema")
        if uri_schema is not None:
            _sql.check_identifier(uri_schema, "schema", self.dialect)
        if plan.target.database is not None:
            _sql.check_identifier(plan.target.database, "database", self.dialect)
        return plan

    def resolve_auth(self, options: Mapping[str, Any]) -> tuple[Secret | None, dict[str, Any]]:
        key = options.get("private_key")
        if key is None:
            if options.get("private_key_passphrase") is not None:
                raise CredentialError("private_key_passphrase is given without private_key")
            return resolve_password(options, self.password_env), {}
        if options.get("password") is not None or options.get("credential") is not None:
            raise CredentialError("give a private_key or a password, not both")
        auth: dict[str, Any] = {"private_key_pem": private_key_pem(key)}
        phrase = options.get("private_key_passphrase")
        if phrase is not None:
            if not isinstance(phrase, str) or not phrase:
                raise CredentialError("private_key_passphrase must be a non-empty string")
            from ._auth import _resolve_reference

            auth["private_key_passphrase"] = Secret(_resolve_reference(phrase) or phrase)
        return None, auth

    def check_schema(self, schema: pa.Schema, plan: Plan) -> None:
        _cloud.check_types(schema, self.dialect, plan.columns, plan.primary_key)

    def create_table_sql(self, plan: Plan, schema: pa.Schema, first: pa.RecordBatch | None) -> str:
        qualified = _sql.qualified(plan.schema_name, plan.table, self.dialect)
        return _cloud.create_table_sql(
            qualified, schema, self.dialect, plan.columns, plan.primary_key
        )

    # -- connection --------------------------------------------------------------------------
    def connect_params(self, plan: Plan) -> dict[str, Any]:
        t = plan.target
        params: dict[str, Any] = {"account": t.host, "user": t.user, "autocommit": False}
        params["session_parameters"] = {"TIMEZONE": "UTC"}
        if t.database:
            params["database"] = t.database
        for key in ("schema", "warehouse", "role"):
            if key in t.params:
                params[key] = t.params[key]
        if plan.secret is not None:
            params["password"] = plan.secret.reveal()
        for key, value in plan.auth.items():
            params[key] = value.reveal() if isinstance(value, Secret) else value
        return params

    def default_connect(self, **params: Any) -> Any:
        try:
            connector = importlib.import_module("snowflake.connector")
        except ImportError:
            raise ShapeError(
                f"the snowflake sink needs snowflake-connector-python: {INSTALL}"
            ) from None
        pem = params.pop("private_key_pem", None)
        phrase = params.pop("private_key_passphrase", None)
        if pem is not None:
            params["private_key"] = der_from_pem(pem, phrase)
        elif not params.get("password"):
            raise CredentialError(
                "no Snowflake credentials: give private_key (a file:// or env:// reference), "
                "password, or set SNOWFLAKE_PASSWORD"
            )
        params.setdefault("application", "sqllocks-shape")
        return connector.connect(**params)

    def ddl_is_transactional(self) -> bool:
        return False

    # -- load --------------------------------------------------------------------------------
    @staticmethod
    def stage_ref(plan: Plan) -> str:
        table = _sql.quote(plan.table, "snowflake")
        if plan.schema_name:
            return f"@{_sql.quote(plan.schema_name, 'snowflake')}.%{table}"
        return f"@%{table}"

    @staticmethod
    def copy_sql(qualified: str, stage: str) -> str:
        # identifiers only, each checked and quoted
        return (
            f"COPY INTO {qualified} FROM {stage} "
            "FILE_FORMAT = (TYPE = PARQUET USE_LOGICAL_TYPE = TRUE) "
            "MATCH_BY_COLUMN_NAME = CASE_SENSITIVE PURGE = TRUE"
        )  # nosec B608

    def load(
        self,
        conn: Any,
        qualified: str,
        plan: Plan,
        first: pa.RecordBatch | None,
        rest: Iterator[pa.RecordBatch],
        progress: list[int],
    ) -> int:
        if first is None:
            return 0
        stage = self.stage_ref(plan)
        run = _cloud.new_run_id()
        chunk_rows = int(plan.options.get("chunk_rows", _cloud.DEFAULT_CHUNK_ROWS))
        workdir = Path(tempfile.mkdtemp(prefix="shape_snowflake_"))
        cur = conn.cursor()
        staged = 0
        pending = 0
        loaded = 0
        put_any = False

        def rows() -> Iterator[pa.RecordBatch]:
            for batch in chain_batches(first, rest):
                _sql.check_batch(first.schema, batch, plan.table)
                yield _cloud.parquet_batch(batch)

        def groups() -> Iterator[tuple[list[pa.RecordBatch], bool]]:
            """Files' worth of batches, each with whether it ends an input batch. A commit only
            falls on an input batch's end (a ``COPY INTO`` is a heavy statement, so
            ``commit_rows`` is rounded up to whole batches, as the SQL Server writer rounds it
            up to whole round trips)."""
            if not plan.commit_rows:
                for group in _cloud.chunked(rows(), chunk_rows):
                    yield group, False
                return
            for batch in rows():
                pieces = list(_cloud.chunked([batch], chunk_rows))
                for index, group in enumerate(pieces):
                    yield group, index == len(pieces) - 1

        def copy() -> None:
            nonlocal pending, loaded
            cur.execute(self.copy_sql(qualified, stage))
            got = _loaded_rows(cur)
            if got != pending:
                raise ShapeError(
                    f"COPY INTO loaded {got:,} of the {pending:,} staged rows of {qualified}"
                )
            loaded += got
            pending = 0

        try:
            schema = _cloud.parquet_batch(first).schema
            for index, (group, batch_end) in enumerate(groups()):
                path = workdir / f"shape_{run}_{index:06d}.parquet"
                pq.write_table(pa.Table.from_batches(group, schema=schema), path)
                put_any = True
                cur.execute(
                    f"PUT {_cloud.string_literal('file://' + path.as_posix())} {stage} "
                    "AUTO_COMPRESS = FALSE OVERWRITE = TRUE"
                )
                _check_put(cur, path.name)
                path.unlink()
                count = sum(b.num_rows for b in group)
                staged += count
                pending += count
                if batch_end and plan.commit_rows and pending >= plan.commit_rows:
                    copy()
                    conn.commit()
                    progress[0] = loaded
            if pending:
                copy()
                if plan.commit_rows:
                    conn.commit()
                    progress[0] = loaded
        finally:
            try:
                if put_any:
                    self._remove(cur, stage, run)
            finally:
                shutil.rmtree(workdir, ignore_errors=True)
                cur.close()
        return loaded

    @staticmethod
    def _remove(cur: Any, stage: str, run: str) -> None:
        try:
            cur.execute(f"REMOVE {stage} PATTERN = {_cloud.string_literal(f'.*shape_{run}_.*')}")
        except Exception as exc:  # noqa: BLE001 - the write's own outcome matters more
            warnings.warn(
                f"could not remove the staged files of run {run} from {stage}: "
                f"{type(exc).__name__}",
                RuntimeWarning,
                stacklevel=2,
            )


def _names(cur: Any) -> list[str]:
    return [str(d[0]).lower() for d in (getattr(cur, "description", None) or ())]


def _check_put(cur: Any, name: str) -> None:
    names = _names(cur)
    if "status" not in names:
        return
    column = names.index("status")
    for row in cur.fetchall() or ():
        if str(row[column]).upper() not in ("UPLOADED", "SKIPPED"):
            raise ShapeError(f"uploading {name} to the stage failed (status {row[column]})")


def _loaded_rows(cur: Any) -> int:
    """The rows ``COPY INTO`` says it loaded, over all files; a file that did not load fails."""
    names = _names(cur)
    status = names.index("status") if "status" in names else 1
    column = names.index("rows_loaded") if "rows_loaded" in names else 3
    total = 0
    for row in cur.fetchall() or ():
        if len(row) <= column:  # "Copy executed with 0 files processed."
            continue
        if str(row[status]).upper() not in _COPY_OK:
            raise ShapeError(f"COPY INTO did not load a staged file (status {row[status]})")
        total += int(row[column])
    return total
