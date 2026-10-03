"""``shape demo init|list|run|preflight|cleanup|status|notebook|report`` (P6-12).

The commands are thin: each reads its arguments and calls the matching function of
:mod:`shape.demo.api`, which the JSON bridge calls too. Exit codes: 0 done, 1 a run failed, a
cleanup could not remove something or a preflight check failed, 2 bad input (an unknown scenario,
session or profile, a setting that cannot be used).

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

MODES = ("inference", "streaming", "seeding")
SCALE_MODES = ("auto", "local", "spark")
AUTH_METHODS = ("cli", "msi", "spn", "sql", "device-code", "fabric")


def add_arguments(sub: Any) -> None:
    dm = sub.add_parser(
        "demo",
        help="run, report on and clean up Shape demos",
        description="One-command demos for talks, clients and workshops. A scenario runs in one "
        "of three modes (inference, seeding, streaming); every run is a session that can be "
        "reported on and cleaned up. A connection profile (`demo init`) names where a seeding "
        "run writes: a folder, a Lakehouse, a Warehouse, a SQL database or an Eventhouse.",
    )
    actions = dm.add_subparsers(
        dest="demo_cmd",
        required=True,
        metavar="{init,list,run,preflight,cleanup,status,notebook,report}",
    )

    ini = actions.add_parser(
        "init",
        help="save a named connection profile",
        description="Save a connection profile. Missing values are asked for when this runs in "
        "a terminal (leave blank to skip). A secret is never stored: --client-secret takes a "
        "credential reference (env://NAME, file://PATH or kv://VAULT/SECRET), and a connection "
        "string that holds a password is refused.",
    )
    ini.add_argument("--name", help="the profile's name")
    ini.add_argument("--workspace-id", help="Fabric workspace ID")
    ini.add_argument("--lakehouse-id", help="Fabric Lakehouse item ID")
    ini.add_argument("--warehouse-conn", help="Warehouse ODBC connection string, or a reference")
    ini.add_argument(
        "--warehouse-staging-path", help="abfss:// or onelake:// folder the Warehouse loads from"
    )
    ini.add_argument("--eventhouse-uri", help="Eventhouse query URI")
    ini.add_argument("--eventhouse-database", help="Eventhouse (KQL) database name")
    ini.add_argument("--sql-db-conn", help="SQL database ODBC connection string, or a reference")
    ini.add_argument("--local-path", help="a folder on this machine that receives Parquet files")
    ini.add_argument("--auth", choices=AUTH_METHODS, default="cli", help="sign-in (default: cli)")
    ini.add_argument("--tenant-id", help="Entra tenant (spn, device-code)")
    ini.add_argument("--client-id", help="Entra application (spn, device-code, msi)")
    ini.add_argument("--client-secret", metavar="REF", help="spn: a credential reference")

    ls = actions.add_parser("list", help="show the demo scenarios")
    ls.add_argument("--json", action="store_true", help="print JSON")

    ru = actions.add_parser(
        "run",
        help="run a demo scenario",
        description="Run a scenario. `--rows` picks the scale preset that is generated (small "
        "up to 2,000 rows, medium up to 50,000, large up to 500,000, then xlarge); it is not an "
        "exact count. `--dry-run` plans the run, `--estimate` prints the cost estimate; neither "
        "generates anything.",
    )
    ru.add_argument("scenario")
    ru.add_argument("--mode", choices=MODES, default="inference")
    ru.add_argument("--connection", help="the connection profile to write to or profile from")
    ru.add_argument("--input-file", help="inference: a CSV, Parquet or JSON Lines file, or live-db")
    ru.add_argument("--rows", type=int, help="the size to generate (default: the scenario's)")
    ru.add_argument("--domain", help="the domain to generate (default: the scenario's)")
    ru.add_argument("--domains", help="comma-separated domains of a composite run")
    ru.add_argument("--env-name", help="a label for the environment, kept in the record")
    ru.add_argument(
        "--output",
        dest="output_formats",
        default="terminal",
        metavar="FORMATS",
        help="comma-separated: terminal, charts, semantic_model, all (default: terminal)",
    )
    ru.add_argument("--output-dir", metavar="DIR", help="where charts and the semantic model go")
    ru.add_argument("--dry-run", action="store_true", help="plan the run; generate nothing")
    ru.add_argument("--estimate", dest="estimate_only", action="store_true", help="cost only")
    ru.add_argument("--seed", type=int, help="the seed")
    ru.add_argument(
        "--scale-mode",
        choices=SCALE_MODES,
        default="auto",
        help="local: on this machine; spark: a Fabric notebook; auto: by row count and profile",
    )
    ru.add_argument("--max-events", type=int, default=100, help="streaming: events to stream")
    ru.add_argument("--table-prefix", help="spark: the prefix of the Delta tables")
    ru.add_argument("--db-schema", default="dbo", help="live-db: the schema to profile")
    ru.add_argument("--db-tables", help="live-db: comma-separated tables (default: all)")
    ru.add_argument("--sample-rows", type=int, default=1000, help="live-db: rows sampled per table")
    ru.add_argument("--json", action="store_true", help="print the result as JSON")

    pf = actions.add_parser("preflight", help="check that each target of a profile answers")
    pf.add_argument("--connection", help="one profile (default: all)")
    pf.add_argument("--json", action="store_true", help="print JSON")

    cl = actions.add_parser("cleanup", help="remove the artifacts of a demo session")
    cl.add_argument("session_id")
    cl.add_argument("--dry-run", action="store_true", help="list what would be removed")
    cl.add_argument("--connection", help="the profile to reach remote targets with")
    cl.add_argument("--json", action="store_true", help="print JSON")

    st = actions.add_parser("status", help="show a demo session")
    st.add_argument("session_id")
    st.add_argument("--json", action="store_true", help="print JSON")

    nb = actions.add_parser("notebook", help="write a Fabric notebook for a scenario")
    nb.add_argument("scenario")
    nb.add_argument("--mode", choices=MODES, default="inference")
    nb.add_argument("-o", "--output", help="the .ipynb path (default: shape_SCENARIO_MODE.ipynb)")

    rp = actions.add_parser("report", help="write the report of a demo session")
    rp.add_argument("session_id")
    rp.add_argument("--format", dest="fmt", choices=("md", "html"), default="md")
    rp.add_argument("-o", "--output", help="write to this file instead of printing")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _ask(value: str | None, prompt: str) -> str:
    """``value``, or (in a terminal) what the user types; blank skips."""
    if value is not None:
        return value
    if sys.stdin.isatty() and sys.stderr.isatty():
        return input(f"{prompt} (blank to skip): ").strip()
    return ""


def run(a: argparse.Namespace) -> int:
    return _COMMANDS[a.demo_cmd](a)


def _init(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_init

    name = a.name
    if name is None:
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise ValueError("give --name")
        name = input("Connection profile name: ").strip()
    saved = demo_init(
        name,
        workspace_id=_ask(a.workspace_id, "Fabric workspace ID"),
        lakehouse_id=_ask(a.lakehouse_id, "Lakehouse ID"),
        warehouse_conn=_ask(a.warehouse_conn, "Warehouse connection string"),
        warehouse_staging_path=a.warehouse_staging_path or "",
        eventhouse_uri=_ask(a.eventhouse_uri, "Eventhouse URI"),
        eventhouse_database=a.eventhouse_database or "",
        sql_db_conn=_ask(a.sql_db_conn, "SQL Database connection string"),
        local_path=a.local_path or "",
        auth=a.auth,
        tenant_id=a.tenant_id or "",
        client_id=a.client_id or "",
        client_secret=a.client_secret or "",
    )
    print(
        f"Connection profile '{name}' saved. Use with: shape demo run SCENARIO --connection {name}"
    )
    return 0 if saved else 1


def _list(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_list

    listing = demo_list()
    if a.json:
        _dump(listing)
        return 0
    rows = listing["scenarios"]
    print(f"{'Name':<16} {'Modes':<30} {'Domains':<24} {'Default rows':>12}  Description")
    for s in rows:
        text = s["description"]
        print(
            f"{s['name']:<16} {', '.join(s['supported_modes']):<30} "
            f"{', '.join(s['domains']):<24} {s['default_rows']:>12,}  "
            f"{text[:60]}{'...' if len(text) > 60 else ''}"
        )
    return 0


def _run(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_run

    settings: dict[str, Any] = {
        "scenario": a.scenario,
        "mode": a.mode,
        "connection": a.connection,
        "input_file": a.input_file,
        "rows": a.rows,
        "domain": a.domain,
        "domains": a.domains,
        "env_name": a.env_name,
        "output_formats": a.output_formats,
        "output_dir": a.output_dir,
        "dry_run": a.dry_run,
        "estimate_only": a.estimate_only,
        "seed": a.seed,
        "scale_mode": a.scale_mode,
        "max_events": a.max_events,
        "table_prefix": a.table_prefix,
        "db_schema": a.db_schema,
        "db_tables": a.db_tables,
        "sample_rows": a.sample_rows,
    }
    from shape.demo.runtime import DemoRuntime

    # With --json the progress goes to standard error, so standard output is the JSON alone.
    result = demo_run(settings, runtime=DemoRuntime(out=sys.stderr if a.json else None))
    if a.json:
        _dump(result)
        return 0 if result["success"] else 1
    if result["success"]:
        print(f"\nSession: {result['session_id']}")
        if result["fidelity_score"] is not None:
            print(f"Fidelity: {result['fidelity_score']:.1%}")
        return 0
    print(f"\nFailed: {result['error']}", file=sys.stderr)
    return 1


def _preflight(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_preflight

    result = demo_preflight(a.connection)
    if a.json:
        _dump(result)
        return 0 if result["ok"] else 1
    labels = {"ok": "OK", "fail": "FAIL", "skipped": "SKIP"}
    names = {
        "local": "Local folder",
        "lakehouse": "Lakehouse",
        "warehouse": "Warehouse",
        "sql_db": "SQL DB",
        "eventhouse": "Eventhouse",
    }
    for profile in result["profiles"]:
        print(f"\nChecking '{profile['name']}'...")
        for check in profile["checks"]:
            label = names.get(check["target"], check["target"])
            text = f"  [{labels[check['status']]}] {label}"
            print(text + (f": {check['message']}" if check["message"] else ""))
    return 0 if result["ok"] else 1


def _cleanup(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_cleanup

    result = demo_cleanup(a.session_id, dry_run=a.dry_run, connection=a.connection)
    if a.json:
        _dump(result)
        return 0 if result["ok"] else 1
    prefix = "[dry-run] Would remove" if a.dry_run else "Removed"
    for target, names in result["removed"].items():
        for name in names:
            print(f"  {prefix}: {target}/{name}")
    for item in result["skipped"]:
        print(f"  Left alone: {item['target']}/{item['name']} ({item['reason']})")
    for item in result["failed"]:
        print(f"  FAILED: {item['target']}/{item['name']}: {item['error']}", file=sys.stderr)
    if not any(result["removed"].values()) and not result["failed"] and not result["skipped"]:
        print("Nothing to remove.")
    return 0 if result["ok"] else 1


def _status(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_status

    result = demo_status(a.session_id)
    if a.json:
        _dump(result)
        return 0
    m = result["manifest"]
    print(f"Session: {m['session_id']}")
    print(f"Scenario: {m['scenario']} ({m['mode']})")
    print(f"Status: {'Success' if m['success'] else 'Failed'}")
    print(f"Started: {m['started_at']}")
    print(f"Artifacts: {len(m['artifacts'])}")
    if "fabric" in result:
        fabric = result["fabric"]
        print(f"Fabric run: {fabric['fabric_run_id']} ({fabric['status']})")
    return 0


def _notebook(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_notebook

    result = demo_notebook(a.scenario, a.mode, a.output)
    print(f"Notebook written to: {result['path']}")
    return 0


def _report(a: argparse.Namespace) -> int:
    from shape.demo.api import demo_report

    result = demo_report(a.session_id, a.fmt, a.output)
    if result["path"]:
        print(f"Report written to: {result['path']}")
    else:
        print(result["content"])
    return 0


_COMMANDS = {
    "init": _init,
    "list": _list,
    "run": _run,
    "preflight": _preflight,
    "cleanup": _cleanup,
    "status": _status,
    "notebook": _notebook,
    "report": _report,
}
