"""The Fabric commands: ``shape fabric publish|notebook|deploy-notebook|setup|export-model``, and
``shape profile-model`` (a semantic model as a profile).

Each command is also a top-level command (``shape publish``, ``shape notebook``,
``shape deploy-notebook``, ``shape setup-fabric``, ``shape export-model``): the same code behind
both names.

Exit codes: 0 done; 1 the service or the destination failed (a sign-in, a write, an HTTP error);
2 the input is wrong (an unknown domain, a missing option, a bad value). Every message is
redacted: a connection string, key or token never reaches the terminal.

Nothing heavy loads until a command runs: ``shape --help`` imports none of this.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

SHAPE_API = "1.0"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT = 2

TARGETS = ("lakehouse", "eventhouse", "sql-database", "warehouse")
FORMATS = ("parquet", "csv", "jsonl", "delta")
WRITE_MODES = ("create", "append", "truncate", "replace")
SCHEMA_MODES = ("3nf", "star")

# The seams of the end-to-end tests: a recorded conversation with the Fabric REST API replaces the
# network. ``None`` is the real thing.
API_TRANSPORT: Any = None


def _version() -> str:
    from shape import __version__

    return str(__version__)


def _fail(message: str, code: int = EXIT_INPUT) -> int:
    from shape.security.redact import redact_text

    print(f"shape: error: {redact_text(message)}", file=sys.stderr)
    return code


def _guarded(run: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    """Turn the expected failures of a command into a message and an exit code."""

    def call(args: argparse.Namespace) -> int:
        from shape.errors import ShapeError

        from .errors import AuthError, WriteError
        from .fabric_api import FabricApiError

        try:
            return run(args)
        except (WriteError, AuthError, FabricApiError, OSError) as exc:
            return _fail(" ".join(str(exc).split()) or type(exc).__name__, EXIT_FAILED)
        except (ValueError, ShapeError, LookupError, ImportError) as exc:
            return _fail(" ".join(str(exc).split()) or type(exc).__name__, EXIT_INPUT)
        except Exception as exc:  # an HTTP error from the Fabric client, a sink error
            from shape.scale.http import HttpError
            from shape.scale.sinks.base import SinkError

            if isinstance(exc, (HttpError, SinkError)):
                return _fail(str(exc), EXIT_FAILED)
            raise

    return call


# ---------------------------------------------------------------------------------------------
# arguments


def _add_sign_in(parser: argparse.ArgumentParser, *, connection_string: bool = False) -> None:
    from shape.cli.auth import add_arguments

    add_arguments(parser, connection_string=connection_string)


def _settings(args: argparse.Namespace, *, entra_only: bool) -> dict[str, str]:
    """The sign-in the command line asks for; ``cli`` (the Azure CLI's login) when none."""
    from shape.cli.auth import settings_from_args

    settings = settings_from_args(args) or {"mode": "cli"}
    if entra_only and settings.get("mode") == "sql":
        raise ValueError("--auth sql is a database login: it does not sign in to the Fabric API")
    return settings


def _credential(settings: dict[str, str]) -> Any:
    from shape.cli.auth import make_credential

    return make_credential(settings)


def _api(args: argparse.Namespace) -> Any:
    from .fabric_api import FabricApi

    return FabricApi(_credential(_settings(args, entra_only=True)), transport=API_TRANSPORT)


def _domain_names() -> list[str]:
    from shape.generation.domains import domain_names

    return list(domain_names())


def _check_domain_and_scale(domain: str, scale: str) -> None:
    """A notebook names a domain of the installed package: check it and the scale now, not when
    the notebook runs in Fabric."""
    from shape.generation.domains import DomainNotFoundError, load_domain

    if domain not in _domain_names():
        raise DomainNotFoundError(
            f"no domain named {domain!r} (installed: {', '.join(_domain_names()) or 'none'})"
        )
    presets = load_domain(domain).schema.generation.scales
    if presets and scale not in presets:
        raise ValueError(
            f"unknown scale {scale!r} for {domain}; the presets are: {', '.join(presets)}"
        )


# ---------------------------------------------------------------------------------------------
# export-model


def _configure_export_model(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "domain", metavar="DOMAIN|SCHEMA.json", help="a domain, or a generation schema file"
    )
    p.add_argument("-s", "--scale", default="small", help="scale preset (default: small)")
    p.add_argument(
        "--source-type",
        default="lakehouse",
        choices=("lakehouse", "warehouse", "sql_database"),
        help="the data source the model's Power Query expressions read (default: lakehouse)",
    )
    p.add_argument(
        "--source-name",
        default="",
        help="the Warehouse or SQL Database server and database name for the expressions",
    )
    p.add_argument(
        "-o",
        "--output",
        default="model.bim",
        metavar="FILE",
        help="the .bim file (default: model.bim)",
    )
    p.add_argument(
        "--include-measures",
        dest="include_measures",
        action="store_true",
        default=True,
        help="write DAX measures (the default)",
    )
    p.add_argument(
        "--no-measures", dest="include_measures", action="store_false", help="write no DAX measures"
    )
    p.add_argument(
        "--schema-name",
        default="dbo",
        help="the SQL schema of a warehouse or sql_database source (default: dbo)",
    )
    p.add_argument(
        "-m",
        "--mode",
        choices=SCHEMA_MODES,
        default=None,
        help="a domain's schema: 3nf (default) or star",
    )


@_guarded
def _run_export_model(a: argparse.Namespace) -> int:
    from shape.cli.generation import load_target

    from .semantic_model import SemanticModelExporter

    is_file = Path(a.domain).is_file() or a.domain.lower().endswith(".json")
    schema = load_target(a.domain, None if is_file else (a.mode or "3nf"))
    schema.generation.scale = a.scale
    exporter = SemanticModelExporter()
    tom = exporter.to_dict(
        schema,
        source_type=a.source_type,
        source_name=a.source_name,
        include_measures=a.include_measures,
        schema_name=a.schema_name,
    )
    path = exporter.export_bim(
        schema,
        source_type=a.source_type,
        source_name=a.source_name,
        output_path=a.output,
        include_measures=a.include_measures,
        schema_name=a.schema_name,
    )
    tables = tom["model"]["tables"]
    measures = sum(len(t.get("measures", [])) for t in tables)
    print(f"Shape v{_version()} — Semantic Model Export")
    print()
    print(f"  Domain:        {a.domain}")
    print(f"  Source type:   {a.source_type}")
    print(f"  Tables:        {len(tables)}")
    print(f"  Relationships: {len(tom['model']['relationships'])}")
    print(f"  DAX measures:  {measures}")
    print(f"  Output:        {path}")
    print()
    print("Import this .bim file into Tabular Editor or deploy via XMLA endpoint.")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# notebook


def _configure_notebook(p: argparse.ArgumentParser) -> None:
    from .notebook import OUTPUT_TARGETS

    p.add_argument("domain", metavar="DOMAIN")
    p.add_argument("-s", "--scale", default="small", help="scale preset (default: small)")
    p.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    p.add_argument(
        "-o", "--output", metavar="FILE.ipynb", help="write the notebook here (default: print it)"
    )
    p.add_argument(
        "--target",
        default="lakehouse",
        choices=OUTPUT_TARGETS,
        help="where the notebook writes the data (default: lakehouse)",
    )


@_guarded
def _run_notebook(a: argparse.Namespace) -> int:
    from .notebook import generate_notebook, save_notebook

    _check_domain_and_scale(a.domain, a.scale)
    nb = generate_notebook(a.domain, a.scale, a.seed, a.target)
    if not a.output:
        print(json.dumps(nb, indent=1))
        return EXIT_OK
    path = save_notebook(nb, a.output)
    print(f"Shape v{_version()} — Notebook Generated")
    print(f"  Domain: {a.domain}")
    print(f"  Scale:  {a.scale}")
    print(f"  Target: {a.target}")
    print(f"  Cells:  {len(nb['cells'])}")
    print(f"  Output: {path}")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# deploy-notebook


def _configure_deploy_notebook(p: argparse.ArgumentParser) -> None:
    p.add_argument("domain", metavar="DOMAIN")
    p.add_argument("--workspace", required=True, help="the Fabric workspace's name or GUID")
    p.add_argument("-s", "--scale", default="small", help="scale preset (default: small)")
    p.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    p.add_argument(
        "--notebook-name",
        default=None,
        help="the notebook's name in Fabric (default: Shape_DOMAIN_SCALE)",
    )
    _add_sign_in(p)


@_guarded
def _run_deploy_notebook(a: argparse.Namespace) -> int:
    from .notebook import generate_notebook, item_definition

    _check_domain_and_scale(a.domain, a.scale)
    name = a.notebook_name or f"Shape_{a.domain}_{a.scale}"
    api = _api(a)
    nb = generate_notebook(a.domain, a.scale, a.seed, "lakehouse")
    print(f"Shape v{_version()} — Deploy Notebook")
    print(f"  Domain:    {a.domain}")
    print(f"  Workspace: {a.workspace}")
    print(f"  Name:      {name}")
    print()
    workspace_id = api.resolve_workspace(a.workspace)
    item = api.create_item(workspace_id, item_definition(nb, name))
    print(f"  Notebook created: {item.get('displayName', name)}")
    print(f"  Item ID: {item.get('id', 'unknown')}")
    print()
    print("Open the notebook in Fabric and click Run All to generate data.")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# setup


def _configure_setup(p: argparse.ArgumentParser) -> None:
    from .setup_env import DEFAULT_ENVIRONMENT, DEFAULT_LAKEHOUSE

    p.add_argument("--workspace", help="the Fabric workspace's name or GUID")
    p.add_argument(
        "--create-lakehouse", action="store_true", help="also create a Lakehouse for the output"
    )
    p.add_argument(
        "--env-name",
        default=DEFAULT_ENVIRONMENT,
        help=f"the Fabric Environment item's name (default: {DEFAULT_ENVIRONMENT})",
    )
    p.add_argument(
        "--lakehouse-name",
        default=DEFAULT_LAKEHOUSE,
        help=f"the Lakehouse's name with --create-lakehouse (default: {DEFAULT_LAKEHOUSE})",
    )
    p.add_argument(
        "--snippet",
        action="store_true",
        help="print the cell to paste into a notebook instead of creating anything",
    )
    _add_sign_in(p)


@_guarded
def _run_setup(a: argparse.Namespace) -> int:
    from .setup_env import library_spec, setup_snippet

    if a.snippet:
        print(setup_snippet())
        return EXIT_OK
    if not a.workspace:
        raise ValueError("setup needs --workspace (or --snippet)")
    api = _api(a)
    print(f"Shape v{_version()} — Fabric Environment Setup")
    print(f"  Workspace:   {a.workspace}")
    print(f"  Environment: {a.env_name}")
    print(f"  Lakehouse:   {'yes' if a.create_lakehouse else 'no'}")
    print()
    workspace_id = api.resolve_workspace(a.workspace)
    done: list[str] = []
    wanted = [("Environment", a.env_name)]
    if a.create_lakehouse:
        wanted.append(("Lakehouse", a.lakehouse_name))
    for kind, name in wanted:
        item, created = api.ensure_item(workspace_id, {"displayName": name, "type": kind})
        shown = item.get("displayName", name)
        if created:
            print(f"  Created {f'{kind}:':<12} {shown}")
        else:
            print(f"  Found existing {kind}: {shown}")
        done.append(f"{kind}: {item.get('displayName', name)}")
    print()
    print("Setup complete:")
    for line in done:
        print(f"  {line}")
    print()
    print("Next steps:")
    print("  1. Open the Environment in Fabric")
    print("  2. Add these PyPI libraries:")
    for lib in library_spec()["customLibraries"]["pypi"]:
        print(f"     - {lib['name']} {lib['version']}")
    print("  3. Publish the environment")
    print("  4. Attach to notebooks and run 'shape notebook' to generate data")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# publish


def _configure_publish(p: argparse.ArgumentParser) -> None:
    env = os.environ.get
    p.add_argument(
        "domain", metavar="DOMAIN|SCHEMA.json", help="a domain, or a generation schema file"
    )
    p.add_argument("-s", "--scale", default="small", help="scale preset (default: small)")
    p.add_argument("--seed", type=int, default=42, help="random seed (default: 42)")
    p.add_argument("-m", "--mode", choices=SCHEMA_MODES, default=None, help="3nf (default) or star")
    p.add_argument("-t", "--target", required=True, choices=TARGETS, help="the Fabric destination")
    p.add_argument(
        "--workspace-id",
        default=env("SHAPE_WORKSPACE_ID"),
        help="Fabric workspace GUID, recorded in the run manifest (env SHAPE_WORKSPACE_ID)",
    )
    p.add_argument(
        "--lakehouse-id",
        default=env("SHAPE_LAKEHOUSE_ID"),
        help="Fabric lakehouse GUID, recorded in the run manifest (env SHAPE_LAKEHOUSE_ID)",
    )
    p.add_argument(
        "--base-path",
        default=env("SHAPE_LAKEHOUSE_PATH"),
        help="lakehouse: the Files folder (local, abfss:// or onelake://); warehouse: the "
        "staging folder (env SHAPE_LAKEHOUSE_PATH)",
    )
    p.add_argument(
        "--staging-path",
        default=None,
        help="warehouse: the OneLake staging folder (instead of --base-path)",
    )
    p.add_argument("--database", default=None, help="eventhouse: the KQL database")
    p.add_argument(
        "--format",
        dest="fmt",
        default="parquet",
        choices=FORMATS,
        help="lakehouse: the file format (default: parquet; delta: local folders only)",
    )
    p.add_argument(
        "--credential",
        dest="credential_ref",
        default=None,
        metavar="REF",
        help="a credential reference (env://NAME, file://PATH, kv://VAULT/SECRET) that holds "
        "the connection string",
    )
    p.add_argument(
        "--write-mode",
        choices=WRITE_MODES,
        default="create",
        help="a database or eventhouse destination: create (the default: an existing table is an "
        "error), append, truncate or replace",
    )
    p.add_argument("--batch-size", type=int, default=5_000, help="sql-database: rows per insert")
    p.add_argument("--schema-name", default="dbo", help="sql-database, warehouse: the schema")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and summarise; publish nothing",
    )
    _add_sign_in(p, connection_string=True)
    p.set_defaults(_env_connection=env("SHAPE_SQL_CONNECTION"))


def _summary(result: Any) -> str:
    lines = [f"{'table':<30}{'rows':>12}{'columns':>9}", "-" * 51]
    for name, table in result.tables.items():
        lines.append(f"{name:<30}{table.num_rows:>12,}{table.num_columns:>9}")
    lines.append("-" * 51)
    lines.append(f"{'TOTAL':<30}{sum(t.num_rows for t in result.tables.values()):>12,}")
    return "\n".join(lines)


def _connection(a: argparse.Namespace) -> str | None:
    """The connection string: ``--credential``'s reference, else ``--connection-string``, else
    the environment variable. A reference is passed on and resolved when the sink is built."""
    from shape.cli.auth import check_connection_string, connection_string_from_args
    from shape.security import credrefs

    if a.credential_ref:
        if not credrefs.is_reference(a.credential_ref):
            raise ValueError(
                "--credential takes a reference: env://NAME, file://PATH or kv://VAULT/SECRET"
            )
        return str(a.credential_ref)
    given = connection_string_from_args(a)
    if given:
        return given
    env = a._env_connection
    return check_connection_string(env, flag="SHAPE_SQL_CONNECTION") if env else None


def _resolve(value: str) -> str:
    from shape.security import credrefs

    if credrefs.is_reference(value):
        try:
            return str(credrefs.resolve_reference(value))
        except credrefs.CredentialReferenceError as exc:
            raise ValueError(f"connection string: {exc}") from None
    return value


def _check_publish(a: argparse.Namespace) -> dict[str, Any]:
    """Everything about the command line that can be wrong, before anything is generated."""
    from .onelake import is_remote

    if a.batch_size < 1:
        raise ValueError("--batch-size must be at least 1")
    plan: dict[str, Any] = {"connection": _connection(a)}
    settings = _settings(a, entra_only=False)
    plan["settings"] = settings
    target = a.target
    if target == "lakehouse":
        if not a.base_path:
            raise ValueError(
                "--base-path (or SHAPE_LAKEHOUSE_PATH) is required for a lakehouse target"
            )
        if a.fmt == "delta" and is_remote(a.base_path):
            raise ValueError(
                "--format delta writes to a local folder only; for OneLake write parquet and "
                "load it into a Delta table in Fabric"
            )
        if settings.get("mode") == "sql":
            raise ValueError("--auth sql is a database login, not for a lakehouse")
    elif target == "sql-database":
        if not plan["connection"]:
            raise ValueError(
                "--connection-string (or SHAPE_SQL_CONNECTION) is required for sql-database"
            )
    elif target == "warehouse":
        if not plan["connection"]:
            raise ValueError(
                "--connection-string (or SHAPE_SQL_CONNECTION) is required for warehouse"
            )
        plan["staging"] = a.staging_path or a.base_path
        if not plan["staging"]:
            raise ValueError(
                "--staging-path or --base-path (an abfss:// OneLake staging folder) is required "
                "for warehouse"
            )
    else:  # eventhouse
        if not plan["connection"] or not a.database:
            raise ValueError("--connection-string and --database are required for eventhouse")
        if settings.get("mode") == "sql":
            raise ValueError("--auth sql is a database login, not for an eventhouse")
    return plan


@contextlib.contextmanager
def _destination_failures() -> Iterator[None]:
    """What goes wrong once publishing has begun is the destination's: exit 1, not a bad command
    line (a table that exists, a folder that cannot be written)."""
    from shape.errors import ShapeError

    from .errors import WriteError

    try:
        yield
    except WriteError:
        raise
    except (ShapeError, ValueError) as exc:
        raise WriteError(str(exc)) from exc


def _publish_lakehouse(a: argparse.Namespace, result: Any, settings: dict[str, str]) -> int:
    from . import onelake
    from .lakehouse import LakehouseWriter

    base = a.base_path
    domain = _label(a.domain, result)
    credential = _credential(settings) if onelake.is_remote(base) else None
    fmt = "parquet" if a.fmt == "delta" else a.fmt
    writer = LakehouseWriter(base, format=fmt, credential=credential)
    print(f"Publishing to Lakehouse ({a.fmt})...")
    from shape.scenario.manifest import ManifestBuilder

    builder = ManifestBuilder()
    builder.start(None, None, domain, a.scale, a.seed)
    if a.workspace_id:
        builder.set_fabric_ids(workspace_id=a.workspace_id, lakehouse_id=a.lakehouse_id or "")
    count = 0
    with _destination_failures():
        for name, table in result.tables.items():
            folder = onelake.join(base, "landing", domain, name, "dt=latest")
            if a.fmt == "delta":
                rows = _write_delta(base, domain, name, table)
                path = folder
            else:
                rows = writer.write_table(
                    name, table.to_batches(), directory=folder, schema=table.schema
                )
                path = onelake.join(folder, f"part-0001.{fmt}")
            builder.record_output(name, rows=rows, columns=table.num_columns, paths=[path])
            print(f"  {name}: {rows:,} rows → {path}")
            count += 1
        print()
        print(f"Published {count} tables to lakehouse.")
        manifest_path = onelake.join(
            base, "landing", domain, "manifest", "_control", "run_manifest.json"
        )
        writer.write_manifest(manifest_path, builder.finish().to_dict())
        print(f"Manifest: {manifest_path}")
    return EXIT_OK


def _write_delta(base: str, domain: str, name: str, table: Any) -> int:
    from shape.plugins.host import default_host

    from . import onelake

    sink = default_host().get("shape.sinks", "delta")
    parent = onelake.join(base, "landing", domain, name)
    return int(
        sink.write(
            Path(parent).resolve().as_uri(), "dt=latest", table.to_batches(), schema=table.schema
        )
    )


def _label(domain: str, result: Any) -> str:
    """The name the landing folders and the manifest use: the domain, or a schema file's domain."""
    if Path(domain).is_file() or domain.lower().endswith(".json"):
        return str(result.schema.model.domain or Path(domain).stem)
    return domain


def _publish_database(a: argparse.Namespace, result: Any, plan: dict[str, Any]) -> int:
    from shape.scale.sinks import build_sink

    target = a.target
    if target == "sql-database":
        name = "sql_database"
        config: dict[str, Any] = {
            "connection_string": plan["connection"],
            "schema_name": a.schema_name,
            "write_mode": a.write_mode,
            "batch_size": a.batch_size,
        }
        label = f"SQL Database (auth={plan['settings'].get('mode')})"
    elif target == "warehouse":
        name = "warehouse"
        config = {
            "connection_string": plan["connection"],
            "staging_path": plan["staging"],
            "schema_name": a.schema_name,
            "write_mode": a.write_mode,
        }
        label = f"Warehouse (auth={plan['settings'].get('mode')})"
    else:
        name = "kql"
        config = {
            "cluster_uri": _resolve(plan["connection"]),
            "database": a.database,
            "write_mode": a.write_mode,
        }
        label = f"Eventhouse ({a.database})"
    sink = build_sink(name, config, auth=plan["settings"])
    print(f"Publishing to {label}...")
    with _destination_failures():
        sink.open(result.schema)
        try:
            for table_name, table in result.tables.items():
                for batch in table.to_batches():
                    sink.write_batch(table_name, batch)
                finish = getattr(sink, "finish_table", None)
                if finish is not None:
                    finish(table_name)
                print(f"  {table_name}: {table.num_rows:,} rows")
        finally:
            sink.close()
    return EXIT_OK


@_guarded
def _run_publish(a: argparse.Namespace) -> int:
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    plan = _check_publish(a)
    is_file = Path(a.domain).is_file() or a.domain.lower().endswith(".json")
    schema = load_target(a.domain, None if is_file else (a.mode or "3nf"))
    presets = schema.generation.scales
    if presets and a.scale not in presets:
        raise ValueError(f"unknown scale {a.scale!r}; the presets are: {', '.join(presets)}")
    dry = "[DRY RUN] " if a.dry_run else ""
    print(f"Shape v{_version()} — {dry}Publishing {a.domain} → {a.target}")
    print(f"Scale: {a.scale} | Seed: {a.seed} | Format: {a.fmt}")
    if a.workspace_id:
        print(f"Workspace: {a.workspace_id}")
    print()
    print("Generating data...")
    result = Engine(schema, scale=a.scale, seed=a.seed).generate()
    print(_summary(result))
    if a.dry_run:
        print()
        print("Dry run complete. No data published.")
        return EXIT_OK
    print()
    code = (
        _publish_lakehouse(a, result, plan["settings"])
        if a.target == "lakehouse"
        else _publish_database(a, result, plan)
    )
    if code == EXIT_OK:
        print()
        print("Publish complete.")
    return code


# ---------------------------------------------------------------------------------------------
# the commands


def _configure_profile_model(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "model",
        metavar="WORKSPACE/MODEL",
        help="the workspace and the semantic model, names or GUIDs (a / inside a name is %%2F)",
    )
    p.add_argument("-o", "--output", metavar="OUT.shape", help="write the profile here")
    p.add_argument("--tables", metavar="T1,T2", help="only these tables (default: every table)")
    p.add_argument(
        "--max-rows",
        metavar="N",
        help="read at most N rows of each table, as a DAX TOPN (default: every row)",
    )
    p.add_argument("--json", action="store_true", help="print one JSON document, not a sentence")


def _model_ref(text: str) -> tuple[str, str]:
    from urllib.parse import unquote

    parts = text.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"expected WORKSPACE/MODEL (a / inside a name is %2F), got {text!r}")
    return unquote(parts[0]), unquote(parts[1])


@_guarded
def _run_profile_model(a: argparse.Namespace) -> int:
    import shape

    from .semantic_profile import profile_model

    if not a.output:
        raise ValueError("profile-model needs -o OUT.shape")
    workspace, model = _model_ref(a.model)
    cap: int | None = None
    if a.max_rows is not None:
        try:
            cap = int(a.max_rows)
        except ValueError:
            cap = -1
        if cap < 0:
            raise ValueError(f"--max-rows must be a whole number of 0 or more, got {a.max_rows!r}")
    names = [t for t in (x.strip() for x in a.tables.split(",")) if t] if a.tables else None
    prof = profile_model(workspace, model, tables=names, max_rows=cap)
    content_id = shape.save(prof, a.output)
    data = prof.to_dict()
    tables, rels = len(data["tables"]), len(data["relationships"])
    if a.json:
        print(
            json.dumps(
                {
                    "written": a.output,
                    "shape_content_id": content_id,
                    "workspace": workspace,
                    "model": model,
                    "tables": tables,
                    "relationships": rels,
                    "max_rows": cap,
                }
            )
        )
    else:
        print(
            f"Profiled {tables} table{'s' if tables != 1 else ''} and {rels} "
            f"relationship{'s' if rels != 1 else ''} of semantic model {model} in workspace "
            f"{workspace}: {a.output}"
        )
    return EXIT_OK


class _Command:
    """One ``shape`` command: ``name``, ``help``, ``configure(parser)`` and ``run(args)``."""

    name = ""
    help = ""
    _configure: Callable[[argparse.ArgumentParser], None]
    _run: Callable[[argparse.Namespace], int]

    def configure(self, parser: Any) -> None:
        type(self)._configure(parser)

    def run(self, args: Any) -> int:
        return type(self)._run(args)


class ExportModelCommand(_Command):
    """``shape export-model``: a domain as a Power BI semantic model (``.bim``)."""

    name = "export-model"
    help = "export a domain as a Power BI / Fabric semantic model (.bim)"
    _configure = staticmethod(_configure_export_model)
    _run = staticmethod(_run_export_model)


class ProfileModelCommand(_Command):
    """``shape profile-model``: profile a whole semantic model with its relationships."""

    name = "profile-model"
    help = "profile every table of a Power BI / Fabric semantic model and its relationships"
    _configure = staticmethod(_configure_profile_model)
    _run = staticmethod(_run_profile_model)


class NotebookCommand(_Command):
    """``shape notebook``: a ready-to-run Fabric notebook for a domain."""

    name = "notebook"
    help = "generate a ready-to-run Fabric notebook for a domain"
    _configure = staticmethod(_configure_notebook)
    _run = staticmethod(_run_notebook)


class DeployNotebookCommand(_Command):
    """``shape deploy-notebook``: make that notebook in a Fabric workspace."""

    name = "deploy-notebook"
    help = "generate a notebook and deploy it to a Fabric workspace"
    _configure = staticmethod(_configure_deploy_notebook)
    _run = staticmethod(_run_deploy_notebook)


class SetupFabricCommand(_Command):
    """``shape setup-fabric``: a Fabric Environment (and Lakehouse) for Shape."""

    name = "setup-fabric"
    help = "set up a Fabric Environment (and optionally a Lakehouse) for Shape"
    _configure = staticmethod(_configure_setup)
    _run = staticmethod(_run_setup)


class PublishCommand(_Command):
    """``shape publish``: generate a domain and publish it to a Fabric destination."""

    name = "publish"
    help = "generate a domain and publish it to a Fabric lakehouse, warehouse, SQL database or eventhouse"  # noqa: E501
    _configure = staticmethod(_configure_publish)
    _run = staticmethod(_run_publish)


_SUBCOMMANDS: tuple[tuple[str, type[_Command]], ...] = (
    ("publish", PublishCommand),
    ("notebook", NotebookCommand),
    ("deploy-notebook", DeployNotebookCommand),
    ("setup", SetupFabricCommand),
    ("export-model", ExportModelCommand),
)


class FabricCommand:
    """``shape fabric``: publish, notebook, deploy-notebook, setup and export-model."""

    name = "fabric"
    help = (
        "Microsoft Fabric: publish data, make and deploy notebooks, set up, export a semantic model"
    )

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="fabric_command", metavar="COMMAND", required=True)
        for name, command in _SUBCOMMANDS:
            child = sub.add_parser(name, help=command.help, description=command.help)
            command._configure(child)
            child.set_defaults(_fabric_run=command._run)

    def run(self, args: Any) -> int:
        return int(args._fabric_run(args))
