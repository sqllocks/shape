"""``shape profile-db``: profile a SQL Server, Azure SQL or Fabric SQL database."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .auth import AUTH_METHODS, Credentials
from .sql import (
    DEFAULT_SAMPLE_ROWS,
    DEFAULT_SCHEMA,
    SqlServerError,
    build_connection_string,
    redact_connection_string,
)

ENV_CONNECTION_STRING = "SHAPE_SQLSERVER_CONNECTION_STRING"
ENV_CLIENT_SECRET = "SHAPE_SQLSERVER_CLIENT_SECRET"


class ProfileDbCommand:
    """``shape profile-db``: profile one schema of a database into a ``.shape`` artifact."""

    name = "profile-db"
    help = "profile a SQL Server / Azure SQL / Fabric SQL database schema"

    def configure(self, parser: Any) -> None:
        parser.add_argument(
            "--connection-string",
            metavar="ODBC",
            help=f"ODBC connection string (or set {ENV_CONNECTION_STRING}; keeps secrets out of "
            "shell history)",
        )
        parser.add_argument("--server", help="server name, instead of --connection-string")
        parser.add_argument("--database", help="database name, with --server")
        parser.add_argument(
            "--auth",
            choices=AUTH_METHODS,
            default="cli",
            help="sql: login in the connection string; cli, msi, spn or fabric: Microsoft Entra "
            "(default: cli)",
        )
        parser.add_argument("--tenant-id")
        parser.add_argument("--client-id")
        parser.add_argument(
            "--client-secret",
            help=f"service principal secret (prefer the {ENV_CLIENT_SECRET} variable)",
        )
        parser.add_argument("--schema", default=DEFAULT_SCHEMA, help="schema to profile")
        parser.add_argument(
            "--tables", metavar="A,B", help="only these tables (default: every table in the schema)"
        )
        parser.add_argument(
            "--sample-rows",
            type=int,
            default=DEFAULT_SAMPLE_ROWS,
            help="rows sampled per table; 0 profiles the catalog only "
            f"(default: {DEFAULT_SAMPLE_ROWS})",
        )
        parser.add_argument("-o", "--output", metavar="OUT.shape", help="write the profile here")
        parser.add_argument("--json", metavar="SUMMARY.json", help="also write a JSON summary")

    def _connection_string(self, args: Any) -> str:
        text = args.connection_string or os.environ.get(ENV_CONNECTION_STRING)
        if text:
            return str(text)
        if args.server:
            return build_connection_string(args.server, args.database)
        raise SqlServerError(
            f"give --connection-string (or {ENV_CONNECTION_STRING}) or --server and --database"
        )

    def run(self, args: Any) -> int:
        try:
            return self._run(args)
        except (SqlServerError, ValueError) as exc:
            print(f"shape: error: {redact_connection_string(str(exc))}", file=sys.stderr)
            return 2

    def _run(self, args: Any) -> int:
        if args.sample_rows < 0:
            raise SqlServerError("--sample-rows cannot be negative")
        if not args.output and not args.json:
            raise SqlServerError("profile-db needs -o OUT.shape (and/or --json SUMMARY.json)")
        import shape

        from .profiler import profile_database

        creds = Credentials(
            method=args.auth,
            tenant_id=args.tenant_id,
            client_id=args.client_id,
            client_secret=args.client_secret or os.environ.get(ENV_CLIENT_SECRET),
        )
        tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None
        prof = profile_database(
            self._connection_string(args),
            credentials=creds,
            schema=args.schema,
            sample_rows=args.sample_rows,
            tables=tables,
            name=args.database or args.schema,
        )
        out: dict[str, Any] = {}
        if args.output:
            out["written"] = args.output
            out["shape_content_id"] = shape.save(prof, args.output)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(prof.summary(), fh, indent=2, default=str)
                fh.write("\n")
        summary = prof.summary()
        out["tables"] = len(summary["tables"])
        out["relationships"] = len(summary["relationships"])
        print(json.dumps(out))
        return 0
