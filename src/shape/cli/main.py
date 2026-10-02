"""Shape command-line entry point.

Every command imports what it needs when it runs, so ``shape --version`` and argument errors
do not pay for numpy, pyarrow or the profiler (P1-11: start-up under 300 ms).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict


def _rows(path):
    """Typed rows of a CSV/Parquet/JSONL file (ints stay ints, not strings)."""
    from shape.io import iter_rows

    return iter_rows(path)


def _dump(x):
    print(json.dumps(x, sort_keys=True, default=str))


def _artifact_kind(path):
    """The ``kind`` in a ``.shape`` manifest, or None when ``path`` is not a Shape artifact."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json")).get("kind")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _is_profile_or_missing(path):
    """Route to the section 12.2 check/diff (exit 2 on input errors) for profile artifacts,
    and for a path that does not exist, which no legacy command can read either."""
    return _artifact_kind(path) == "profile" or not os.path.exists(path)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _run(fn, a):
    """Run a profile/check/diff command: 0 ok, 1 failed check or drift, 2 input error."""
    import zipfile

    from shape.errors import ShapeError

    try:
        return fn(a)
    except (
        OSError,
        ValueError,
        ImportError,
        NotImplementedError,
        KeyError,
        ShapeError,
        zipfile.BadZipFile,
    ) as exc:
        # The artifact modules are not imported by the commands that never touch an artifact; an
        # ArtifactSignatureError can only come from one that is.
        artifact_io = sys.modules.get("shape.artifact.io")
        if artifact_io is not None and isinstance(exc, artifact_io.ArtifactSignatureError):
            print(f"shape: signature check failed: {exc}", file=sys.stderr)
            return 1
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2


def _sign_output(a, path):
    """Sign the artifact just written when ``--sign KEY`` was given."""
    if getattr(a, "sign", None):
        from shape.artifact.signing import load_private_key, sign_artifact

        return sign_artifact(path, load_private_key(a.sign))
    return None


def _verify_inputs(a):
    """``--verify PUBKEY``: every .shape input must carry a valid signature by that key."""
    from shape.artifact.signing import load_public_key, verify_artifact

    key = load_public_key(a.verify)
    for name in ("shape", "before", "after", "target", "observed"):
        path = getattr(a, name, None)
        if isinstance(path, str) and path.endswith(".shape"):
            verify_artifact(path, key)


def _cmd_keygen(a):
    from shape.artifact.signing import key_id, load_public_key, write_keypair

    priv, pub = write_keypair(a.prefix)
    _dump(
        {"private_key": str(priv), "public_key": str(pub), "key_id": key_id(load_public_key(pub))}
    )
    return 0


def _cmd_sign(a):
    from shape.artifact.signing import load_private_key, sign_artifact

    kid = sign_artifact(a.shape, load_private_key(a.key), a.output)
    _dump({"signed": a.output or a.shape, "key_id": kid})
    return 0


def _cmd_verify_signature(a):
    from shape.artifact.signing import load_public_key, verify_artifact

    if not a.key:
        raise ValueError("verifying a .shape artifact needs --key PUBLIC.pub")
    _dump({"artifact": a.shape, **verify_artifact(a.shape, load_public_key(a.key))})
    return 0


def _cmd_profile(a):
    import shape

    if not a.output:
        raise ValueError("profile needs -o OUT.shape")
    prof = shape.profile(a.src)
    content_id = shape.save(prof, a.output)
    key_id = _sign_output(a, a.output)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(prof.to_html())
    if a.json:
        _write_json(a.json, prof.summary())
    out = {"written": a.output, "shape_content_id": content_id}
    if key_id:
        out["signed_by"] = key_id
    _dump(out)
    return 0


def _cmd_stream_profile(a):
    from shape.streaming.cli import run

    return run(a)


def _cmd_inspect(a):
    """Print what a .shape artifact holds: a profile or a Shape model."""
    if _artifact_kind(a.shape) == "profile":
        import shape

        prof = shape.load(a.shape)
        _dump({"kind": "profile", "name": prof.name, "profile": prof.to_dict()})
    elif str(a.shape).endswith(".shape"):
        from shape.artifact import read_model

        manifest, model = read_model(a.shape)
        _dump({"kind": "model", "manifest": manifest, "shape": model})
    else:
        with open(a.shape, encoding="utf-8") as fh:
            _dump(json.load(fh))
    return 0


def _cmd_check(a):
    import shape

    result = shape.check(shape.load(a.shape), a.contract)
    out = result.to_dict()
    if a.json:
        _write_json(a.json, out)
    _dump(out)
    return 0 if result.passed else 1


def _cmd_fidelity(a):
    """``shape fidelity REFERENCE SYNTHETIC``: 0 when every pass mark is met, 1 when not."""
    from pathlib import Path

    if a.tier:
        from shape.cli.tiers import run_fidelity

        return run_fidelity(a)
    from shape.generation.report import Thresholds, compare_tables, render_report
    from shape.quality import load_tables

    real = load_tables(a.reference, a.input_format)
    synth = load_tables(a.csv, a.input_format)
    if not real:
        raise ValueError(f"no data files found in {a.reference}")
    if len(real) == 1 and len(synth) == 1:  # two single files compare whatever they are called
        synth = {next(iter(real)): next(iter(synth.values()))}
    marks = Thresholds(a.min_score, a.min_table_score, a.min_column_score)
    report = compare_tables(real, synth, marks).to_dict()
    ext = {".json": "json", ".md": "md", ".html": "html", ".htm": "html"}
    for out in a.output:
        fmt = ext.get(Path(out).suffix.lower())
        if fmt is None:
            raise ValueError(f"cannot tell the report format of {out}: use .json, .md or .html")
        Path(out).write_bytes(render_report(report, fmt))
    sys.stdout.write(render_report(report, a.format).decode())
    return 0 if report["passed"] else 1


def _cmd_diff(a):
    import shape

    result = shape.diff(shape.load(a.before), shape.load(a.after))
    out = result.to_dict()
    if a.json:
        _write_json(a.json, out)
    _dump(out)
    return 1 if (a.fail_on_drift and result.drifted) else 0


def _cmd_verify(a):
    """``shape verify``: a ``.shape`` artifact is checked for its signature, anything else is
    data for the validation gates."""
    if str(a.shape).endswith(".shape"):
        return _cmd_verify_signature(a)
    return _cmd_verify_gates(a)


def _cmd_verify_gates(a):
    """Load tables, run the gates, print the gate table; 0 pass, 1 a gate failed (or a warning
    under --strict), 2 input error."""
    from shape.quality import VerifyReport, VerifyRunner, load_gate_schema, load_tables

    tables = load_tables(a.shape, a.format)
    if not tables:
        raise ValueError(f"no {a.format} data files found in {a.shape}")
    schema = load_gate_schema(a.schema) if a.schema else None
    result = VerifyRunner(schema, a.statistical, a.shape, a.schema).run(tables)
    print(f"Shape {_version()} - Verify\n")
    print(f"Data path:   {a.shape}")
    if a.schema:
        print(f"Schema:      {a.schema}")
    print(f"Statistical: {'yes' if a.statistical else 'no'}\n")
    if result.gate_results:
        print(f"{'Gate':<28} {'Status':<8} {'Errors':>6} {'Warnings':>8}")
        print("-" * 55)
        for g in result.gate_results:
            status = "PASS" if g.passed else "FAIL"
            print(f"{g.gate_name:<28} {status:<8} {len(g.errors):>6} {len(g.warnings):>8}")
        print()
    print("Row counts:")
    for name, n in sorted(result.row_counts.items()):
        print(f"  {name}: {n:,}")
    print()
    for g in result.gate_results:
        for e in g.errors:
            print(f"  ERROR [{g.gate_name}]: {e}", file=sys.stderr)
        for w in g.warnings:
            print(f"  WARN  [{g.gate_name}]: {w}")
    print(f"\nResult: {'PASS' if result.passed else 'FAIL'}")
    if a.output:
        report = VerifyReport(result)
        text = report.to_json() if str(a.output).endswith(".json") else report.to_markdown()
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Report written to {a.output}")
    has_warnings = any(g.warnings for g in result.gate_results)
    return 1 if (not result.passed or (a.strict and has_warnings)) else 0


_VERIFY_HELP = "require every .shape input to be signed by this public key (exit 1 if not)"


def _cmd_from_ddl(a):
    """``shape from-ddl FILE``: read ``CREATE TABLE`` DDL, write a generation schema."""
    from pathlib import Path

    from shape.generation.ddl import from_ddl

    src = Path(a.input_file)
    schema, notes = from_ddl(
        src.read_text(encoding="utf-8"), domain=a.domain, smart=a.smart, scale=a.scale
    )
    out = Path(a.output) if a.output else src.with_suffix(".gen.json")
    _write_json(out, schema.to_dict())
    print(f"Shape DDL import{' (smart)' if a.smart else ''}")
    print()
    print(f"  Source: {src}")
    print(f"  Output: {out}")
    print(f"  Tables: {len(schema.tables)}")
    print(f"  Relationships: {len(schema.relationships)}")
    print(f"  Business rules: {len(schema.business_rules)}")
    if a.smart:
        print(f"  Inferences: {len(notes)}")
    print()
    for name, table in schema.tables.items():
        pk = f" (PK: {', '.join(table.primary_key)})" if table.primary_key else ""
        print(f"  {name}: {len(table.columns)} columns{pk}")
    print()
    print(f"Schema written to {out}")
    if a.explain and notes:
        print()
        print("--- Inference Report ---")
        print()
        for n in notes:
            col = f".{n.column}" if n.column else ""
            print(
                f"  [{n.rule_id}] {n.table}{col}: {n.description} (confidence: {n.confidence:.0%})"
            )
    return 0


def _cmd_plugins(a):
    """``shape plugins <sub>``: list and info inspect; doctor exits 0 when every plugin loads."""
    from shape.plugins import cli as plugin_cli
    from shape.plugins.doctor import diagnose, format_report
    from shape.plugins.host import default_host

    host = default_host()
    if a.plugins_cmd == "list":
        return plugin_cli.cmd_list(host, a)
    if a.plugins_cmd == "info":
        return plugin_cli.cmd_info(host, a)
    report = diagnose(host)
    if a.json:
        _dump(report)
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


def _stream_profile_arguments(parser):
    """The ``stream-profile`` arguments; the command itself is ``shape.streaming.cli``."""
    parser.add_argument(
        "uri", metavar="URI", help="kafka://host:9092/TOPIC or eventhubs://NAMESPACE/HUB"
    )
    parser.add_argument("-o", "--output", metavar="OUT.json", help="the global profile")
    parser.add_argument(
        "--window",
        choices=("global", "tumbling", "sliding", "session"),
        default="global",
        help="global: one profile of the whole stream (default); the others write --windows",
    )
    parser.add_argument("--windows", metavar="OUT.jsonl", help="closed windows, one per line")
    parser.add_argument("--size", metavar="DURATION", help="tumbling or sliding window size")
    parser.add_argument("--slide", metavar="DURATION", help="sliding window step (at most size)")
    parser.add_argument("--gap", metavar="DURATION", help="session window inactivity gap")
    parser.add_argument(
        "--allowed-lateness",
        default="0s",
        metavar="DURATION",
        help="how far behind the newest event time a row may arrive and still count (default: 0s)",
    )
    parser.add_argument(
        "--event-time",
        metavar="FIELD",
        help="the payload field holding the event time (default _shape_event_time; "
        "events without one use the broker's timestamp)",
    )
    parser.add_argument(
        "--event-time-unit",
        choices=("s", "ms", "us"),
        default="ms",
        help="unit of a numeric event time (default: ms)",
    )
    parser.add_argument(
        "--start",
        choices=("earliest", "latest"),
        default="earliest",
        help="where to begin when there is no checkpoint (default: earliest)",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="keep reading as events arrive, instead of stopping at the end the stream had "
        "when the run began",
    )
    parser.add_argument("--max-events", type=int, metavar="N", help="stop after N events")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        metavar="SECONDS",
        help="stop after this long without an event (with --follow)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=65_536,
        metavar="N",
        help="events per micro-batch; the first batch fixes the schema (default: 65536)",
    )
    parser.add_argument(
        "--checkpoint",
        metavar="FILE",
        help="commit the offsets and profile state here and resume from it when it exists",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=10,
        metavar="N",
        help="batches between checkpoints (default: 10)",
    )
    parser.add_argument(
        "--max-reconnects",
        type=int,
        default=5,
        metavar="N",
        help="consecutive connections that bring nothing new before giving up (default: 5)",
    )
    parser.add_argument("--name", default="stream", help="table name in the profile")
    parser.add_argument("--top-n", type=int, default=500, metavar="N", help="top values kept")
    parser.add_argument(
        "--option",
        action="append",
        metavar="KEY=VALUE",
        help="a source option (JSON values allowed); repeatable",
    )
    parser.add_argument(
        "--options-file",
        metavar="FILE.json",
        help="source options as a JSON object (keeps secrets out of the command line)",
    )


def _build_parser(plugin_commands=()):
    p = argparse.ArgumentParser(prog="shape", description="Shape as Code")
    p.add_argument("--version", "-V", action="store_true", help="print the version and exit")
    g = p.add_argument_group("run logging and metrics (before the command)")
    g.add_argument("--log-json", action="store_true", help="log JSON lines to stderr")
    g.add_argument("--log-level", default="INFO", metavar="LEVEL", help="log level (default INFO)")
    g.add_argument("--metrics", metavar="FILE", help="write the run's metrics to FILE as JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("conformance")
    sub.add_parser("version")
    pl = sub.add_parser("plugins", help="inspect installed plugins")
    pls = pl.add_subparsers(dest="plugins_cmd", required=True)
    pll = pls.add_parser("list", help="list installed plugins (imports none of them)")
    pll.add_argument("--group", metavar="GROUP", help="only this entry-point group")
    pll.add_argument("--json", action="store_true", help="print the list as JSON")
    pli = pls.add_parser("info", help="load one plugin and describe it")
    pli.add_argument("plugin", metavar="[GROUP:]NAME")
    pli.add_argument("--json", action="store_true", help="print the description as JSON")
    pld = pls.add_parser("doctor", help="load every plugin and report failures")
    pld.add_argument("--json", action="store_true", help="print the report as JSON")
    c = sub.add_parser("capture")
    c.add_argument("csv")
    c.add_argument("-o", "--output")
    c.add_argument("--sign", metavar="KEY", help="sign the written .shape with this private key")
    pr = sub.add_parser(
        "profile",
        help="profile a file, glob, directory or Delta table",
        epilog="also: `shape profile safe PROFILE.shape -o SAFE.json` writes the share-safe "
        "form; `shape profile validate --safe ARTIFACT` scans it for leaks",
    )
    pr.add_argument("src", metavar="SRC")
    pr.add_argument("-o", "--output", metavar="OUT")
    pr.add_argument("--sign", metavar="KEY", help="sign the written .shape with this private key")
    pr.add_argument("--html", metavar="REPORT.html")
    pr.add_argument("--json", metavar="SUMMARY.json")
    sp = sub.add_parser(
        "stream-profile",
        help="profile a Kafka topic or an Event Hubs hub (bounded mode, windows, checkpoints)",
    )
    _stream_profile_arguments(sp)
    d = sub.add_parser("diff", help="compare two profiles")
    d.add_argument("before", metavar="BASE.shape")
    d.add_argument("after", metavar="CURRENT.shape")
    d.add_argument("--json", metavar="RESULT.json")
    d.add_argument("--fail-on-drift", action="store_true")
    d.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    for name in ("show", "inspect"):
        sh = sub.add_parser(name, help="print a .shape artifact's manifest and contents")
        sh.add_argument("shape")
        sh.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    fd = sub.add_parser(
        "from-ddl",
        help="read SQL CREATE TABLE DDL into a generation schema",
        description="Reads SQL Server, PostgreSQL, MySQL and ANSI CREATE TABLE statements "
        "(and ALTER TABLE foreign keys). With --smart (the default) it infers realistic "
        "distributions, foreign-key patterns, temporal seasonality and business rules from "
        "the schema's structure.",
    )
    fd.add_argument("input_file", metavar="FILE", help="a .sql file")
    fd.add_argument("-o", "--output", metavar="OUT", help="default: FILE with the suffix .gen.json")
    fd.add_argument("--domain", default="custom", help="domain name for the schema")
    fd.add_argument("-s", "--scale", metavar="SPEC", help="scale override: small:table1=N,table2=N")
    fd.add_argument(
        "--smart",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="infer distributions, key patterns and rules (default) or keep the first "
        "generators (--no-smart)",
    )
    fd.add_argument("--explain", action="store_true", help="print the inference report")
    kg = sub.add_parser("keygen", help="generate an Ed25519 signing key pair")
    kg.add_argument("prefix", help="writes PREFIX.key (private, mode 0600) and PREFIX.pub")
    sg = sub.add_parser("sign", help="sign a .shape artifact")
    sg.add_argument("shape", metavar="ARTIFACT.shape")
    sg.add_argument("--key", required=True, metavar="PRIVATE.key")
    sg.add_argument("-o", "--output", metavar="OUT.shape", help="default: sign in place")
    va = sub.add_parser(
        "validate",
        help="validate a generation schema or a contract (chosen by what the file holds)",
        description="Validate FILE: a Shape generation schema goes through the schema "
        "validator, a contract through the contract validation; any other document exits 2.",
    )
    va.add_argument("contract", metavar="FILE")
    vf = sub.add_parser(
        "verify",
        help="run the validation gates over data, or check a .shape artifact's signature",
    )
    vf.add_argument(
        "shape",
        metavar="DATA|ARTIFACT.shape",
        help="a data file or a directory of data files; a .shape file is checked for its "
        "signature instead (exit 1 if invalid)",
    )
    vf.add_argument("--key", metavar="PUBLIC.pub", help="public key for a .shape artifact")
    vf.add_argument("--format", choices=("auto", "csv", "parquet", "jsonl"), default="auto")
    vf.add_argument("--schema", metavar="GATES.json", help="gate schema (or Shape model v2)")
    vf.add_argument("--statistical", action="store_true", help="add KS and chi-squared tests")
    vf.add_argument("-o", "--output", metavar="REPORT", help="write a .json or .md report")
    vf.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    qu = sub.add_parser("quality")
    qu.add_argument("csv")
    qu.add_argument("--reference")
    from shape.cli.generation import add_arguments as add_generation_arguments

    add_generation_arguments(sub)
    fi = sub.add_parser(
        "fidelity",
        aliases=["compare"],
        help="score synthetic data against reference data, per column and per table",
        description=(
            "Compare SYNTHETIC with REFERENCE (a file, or a directory of one file per table) and "
            "score every column 0-100, then every table and the whole. Exit 0 when every pass "
            "mark is met, 1 when not, 2 for bad input. Given a captured profile (REFERENCE.json) "
            "and a CSV file it certifies the CSV against the profile instead (--tolerance; exit "
            "3 on failure)."
        ),
    )
    fi.add_argument("reference", metavar="REFERENCE")
    fi.add_argument("csv", metavar="SYNTHETIC")
    fi.add_argument("--tolerance", type=float, default=0.1, help="with a REFERENCE.json profile")
    fi.add_argument("--input-format", default="auto", choices=("auto", "csv", "parquet", "jsonl"))
    fi.add_argument("--min-score", type=float, default=85.0, help="overall pass mark (default 85)")
    fi.add_argument(
        "--min-table-score", type=float, default=70.0, help="per-table pass mark (default 70)"
    )
    fi.add_argument("--min-column-score", type=float, help="per-column pass mark (default: none)")
    fi.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        metavar="REPORT",
        help="write a report; .json, .md or .html by extension (repeatable)",
    )
    fi.add_argument(
        "--format",
        default="json",
        help="what to print: a shape.reports format such as json, md or html (default json)",
    )
    k = sub.add_parser("key")
    k.add_argument("csv")
    k.add_argument("fields", nargs="+")
    f = sub.add_parser("fd")
    f.add_argument("csv")
    f.add_argument("--determinant", nargs="+", required=True)
    f.add_argument("--dependent", required=True)
    q = sub.add_parser("privacy-k")
    q.add_argument("csv")
    q.add_argument("fields", nargs="+")
    cq = sub.add_parser("query")
    cq.add_argument("shape")
    cq.add_argument("expression")
    cq.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    ck = sub.add_parser("check", help="check a profile against a contract")
    ck.add_argument("shape", metavar="PROFILE.shape")
    ck.add_argument("contract", metavar="CONTRACT.json")
    ck.add_argument("--json", metavar="RESULT.json")
    ck.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    co = sub.add_parser("compatibility")
    co.add_argument("before")
    co.add_argument("after")
    co.add_argument("--mode", choices=("backward", "forward", "full"), default="backward")
    gp = sub.add_parser("plan")
    gp.add_argument("shape")
    gp.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    fc = sub.add_parser("certify-shapes")
    fc.add_argument("target")
    fc.add_argument("observed")
    fc.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="minimum certificate score, 0 to 1 (default 0.9); below it the exit code is 3",
    )
    from shape.cli.tiers import add_drift_parser, add_fidelity_arguments

    add_fidelity_arguments(fi)
    add_drift_parser(sub)
    rg = sub.add_parser("registry")
    rg.add_argument("root")
    rg.add_argument("action", choices=("commit", "checkout", "tag", "promote", "log"))
    rg.add_argument("name")
    rg.add_argument("arg1", nargs="?")
    rg.add_argument("arg2", nargs="?")
    for rec in plugin_commands:  # listed in --help only; the plugin loads when it is run
        sub.add_parser(rec.name, help=f"(plugin {rec.source})", add_help=False)
    return p


def _version():
    from shape import __version__

    return __version__


_GLOBAL_VALUE_OPTIONS = ("--log-level", "--metrics")


def _split_global(argv):
    """Take the global options (``--log-json``, ``--log-level L``, ``--metrics FILE``) off the
    front of ``argv``; they may also come from SHAPE_LOG_JSON, SHAPE_LOG_LEVEL, SHAPE_METRICS."""
    opts = {
        "log_json": os.environ.get("SHAPE_LOG_JSON", "") not in ("", "0"),
        "log_level": os.environ.get("SHAPE_LOG_LEVEL", "INFO"),
        "metrics": os.environ.get("SHAPE_METRICS") or None,
    }
    rest = list(argv)
    while rest and rest[0].startswith("--"):
        name, eq, value = rest[0].partition("=")
        if name == "--log-json":
            opts["log_json"] = True
            rest.pop(0)
        elif name in _GLOBAL_VALUE_OPTIONS:
            if not eq:
                if len(rest) < 2:
                    print(f"shape: error: {name} needs a value", file=sys.stderr)
                    raise SystemExit(2)
                value = rest.pop(1)
            opts[name[2:].replace("-", "_")] = value
            rest.pop(0)
        else:
            break
    return opts, rest


def main(argv=None):
    """Run a command: 0 ok, 1 a check failed, 2 bad input. With the global options (or the
    environment variables) on, the run logs JSON lines to stderr and writes its metrics.

    Called as the program (no ``argv``), a ``generate`` that wrote its files ends the process as
    soon as everything is flushed (``lifecycle.exit_now``) instead of tearing the interpreter down:
    freeing the hundreds of megabytes of tables and unloading the modules takes about 40 ms of a
    half-second run, and nothing is left to do (every file is closed, no ``atexit`` handler of
    Shape's is pending)."""
    from shape.cli import lifecycle

    lifecycle.quick_exit_allowed = argv is None
    return _main(argv)


def _main(argv):
    opts, argv = _split_global(sys.argv[1:] if argv is None else argv)
    if not (opts["log_json"] or opts["metrics"]):
        return _dispatch(argv)
    from shape.cli import lifecycle

    lifecycle.quick_exit_allowed = False  # the log line and metrics file come after the command
    import logging
    import time

    from shape import runlog

    if opts["log_json"]:
        runlog.configure_logging(level=opts["log_level"])
    command = next((x for x in argv if not x.startswith("-")), "")
    run = runlog.begin(f"{time.strftime('%Y%m%dT%H%M%S')}_{command or 'shape'}")
    run.set(command=command)
    log = logging.getLogger(runlog.LOGGER)
    log.info("command started", extra={"command": command})
    code = 1
    try:
        code = _dispatch(argv)
        return code
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        raise
    finally:
        run.set(exit_code=code)
        summary = run.finish()
        log.info(
            "command finished",
            extra={"command": command, "exit_code": code, "metrics": summary},
        )
        if opts["metrics"]:
            with open(opts["metrics"], "w", encoding="utf-8") as fh:
                fh.write(run.to_json() + "\n")


def _dispatch(argv):
    if argv[:1] in (["--version"], ["-V"]):
        print(f"shape {_version()}")
        return 0
    if argv[:1] == ["profile"] and argv[1:2] in (["safe"], ["validate"]):
        from shape.privacy.cli import main as privacy_main

        return privacy_main(argv[1:])
    parser = _build_parser()
    builtin = {
        n
        for act in parser._actions
        if isinstance(act, argparse._SubParsersAction)
        for n in act.choices
    }
    first = next((x for x in argv if not x.startswith("-")), None)
    plugin_cmds = {}
    if first is None or first not in builtin or argv[:1] in (["-h"], ["--help"]):
        from shape.plugins.cli import command_names
        from shape.plugins.host import default_host

        plugin_cmds = command_names(default_host(), builtin)
    if argv[:1] and argv[0] in plugin_cmds:
        from shape.plugins.cli import run_command
        from shape.plugins.host import default_host

        return run_command(default_host(), argv[0], argv[1:])
    a = (_build_parser(plugin_cmds.values()) if plugin_cmds else parser).parse_args(argv)
    if a.version:
        print(f"shape {_version()}")
        return 0
    if a.cmd in ("keygen", "sign", "verify"):
        return _run({"keygen": _cmd_keygen, "sign": _cmd_sign, "verify": _cmd_verify}[a.cmd], a)
    if getattr(a, "verify", None):
        rc = _run(_verify_inputs, a)
        if rc:
            return rc
    if a.cmd in ("generate", "describe", "list", "presets"):
        from shape.cli.generation import run as run_generation

        return _run(run_generation, a)
    if a.cmd == "from-ddl":
        return _run(_cmd_from_ddl, a)
    if a.cmd == "profile":
        return _run(_cmd_profile, a)
    if a.cmd == "stream-profile":
        return _run(_cmd_stream_profile, a)
    if a.cmd == "check" and _is_profile_or_missing(a.shape):
        return _run(_cmd_check, a)
    if a.cmd == "diff" and _is_profile_or_missing(a.before):
        return _run(_cmd_diff, a)
    if a.cmd == "plugins":
        return _cmd_plugins(a)
    if a.cmd == "version":
        _dump({"shape": _version(), "specification": "2", "artifact_format": 2})
        return 0
    if a.cmd == "doctor":
        checks = {"python": sys.version.split()[0]}
        for name in ("pyarrow", "cryptography", "yaml"):
            try:
                checks[name] = __import__(name).__version__
            except Exception:
                checks[name] = None
        _dump(checks)
        return 0
    if a.cmd == "conformance":
        from shape.validation.suite import conformance

        r = conformance()
        _dump([asdict(x) for x in r])
        return 0 if all(x.passed for x in r) else 1
    if a.cmd == "capture":
        from shape.artifact import write_shape
        from shape.capture import capture_rows

        obj = capture_rows(_rows(a.csv)).to_dict()
        if a.output and str(a.output).endswith(".shape"):
            cid = write_shape(a.output, obj, name=__import__("pathlib").Path(a.csv).stem)
            out = {"written": a.output, "shape_content_id": cid}
            kid = _sign_output(a, a.output)
            if kid:
                out["signed_by"] = kid
            _dump(out)
            return 0
        if getattr(a, "sign", None):
            raise SystemExit("capture --sign needs -o OUT.shape")
        raw = json.dumps(obj, sort_keys=True, indent=2, default=str)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(raw + "\n")
        else:
            print(raw)
        return 0
    if a.cmd == "diff":
        from shape.drift import compare

        _dump([asdict(v) for v in compare(json.load(open(a.before)), json.load(open(a.after)))])
        return 0
    if a.cmd in ("show", "inspect"):
        return _run(_cmd_inspect, a)
    if a.cmd == "validate":
        from shape.cli.validate import cmd_validate

        return _run(cmd_validate, a)
    if a.cmd == "quality":
        from shape.capture import capture_rows
        from shape.quality import infer_rules, validate_rows

        rows = list(_rows(a.csv))
        ref = json.load(open(a.reference)) if a.reference else capture_rows(rows).to_dict()
        result = validate_rows(rows, infer_rules(ref))
        _dump({"passed": result.passed, "violations": [asdict(v) for v in result.violations]})
        return 0 if result.passed else 2
    if a.cmd == "drift":
        from shape.cli.tiers import run_drift

        return _run(run_drift, a)
    if a.cmd in ("fidelity", "compare") and (a.tier or not str(a.reference).endswith(".json")):
        return _run(_cmd_fidelity, a)
    if a.cmd in ("fidelity", "compare"):
        from shape.generation import certify

        ref = json.load(open(a.reference))
        cert = certify(ref, list(_rows(a.csv)), tolerance=a.tolerance)
        _dump(cert.to_dict())
        return 0 if cert.passed else 3
    if a.cmd in ("query", "plan") and _artifact_kind(a.shape) == "profile":
        print(
            f"shape: error: `shape {a.cmd}` does not read profiles made by `shape profile` yet "
            "(planned). Use `shape check` or `shape diff`.",
            file=sys.stderr,
        )
        return 2
    if a.cmd in ("query", "check", "plan"):
        from shape.artifact import read_shape

        _, s = (
            read_shape(a.shape)
            if str(a.shape).endswith(".shape")
            else ({}, json.load(open(a.shape)))
        )
        if a.cmd == "query":
            from shape.query import query as shape_query

            _dump({"result": shape_query(s, a.expression)})
            return 0
        if a.cmd == "plan":
            from shape.generation.fidelity import plan_reconstruction

            _dump(plan_reconstruction(s).to_dict())
            return 0
        from shape.contracts import evaluate_contract

        contract = json.load(open(a.contract))
        r = evaluate_contract(s, contract)
        _dump(r.to_dict())
        return 0 if r.passed else 4
    if a.cmd in ("compatibility", "certify-shapes"):
        from shape.artifact import read_shape

        def LS(p):
            return read_shape(p)[1] if str(p).endswith(".shape") else json.load(open(p))

        if a.cmd == "compatibility":
            from shape.contracts import compatibility

            r = compatibility(LS(a.before), LS(a.after), a.mode)
            _dump(r.to_dict())
            return 0 if r.compatible else 5
        from shape.generation.fidelity import certify_shapes

        cert = certify_shapes(LS(a.target), LS(a.observed))
        _dump(cert.to_dict())
        return 0 if cert.score >= a.threshold else 3
    if a.cmd == "registry":
        from shape.registry import LocalRegistry

        r = LocalRegistry(a.root)
        if a.action == "commit":
            if not a.arg1:
                raise SystemExit("registry commit requires artifact path")
            _dump({"content_id": r.commit(a.name, open(a.arg1, "rb").read())})
        elif a.action == "checkout":
            data = r.checkout(a.name, a.arg1 or "latest")
            if a.arg2:
                open(a.arg2, "wb").write(data)
                _dump({"written": a.arg2})
            else:
                sys.stdout.buffer.write(data)
        elif a.action == "tag":
            _dump({"content_id": r.tag(a.name, a.arg1, a.arg2 or "latest")})
        elif a.action == "promote":
            _dump({"content_id": r.promote(a.name, a.arg1, a.arg2)})
        else:
            _dump(r.log(a.name))
        return 0
    from shape.privacy.measure import k_anonymity
    from shape.profile.dependencies import candidate_key, functional_dependency

    rows = list(_rows(a.csv))
    if a.cmd == "key":
        result = candidate_key(rows, tuple(a.fields))
    elif a.cmd == "fd":
        result = functional_dependency(rows, tuple(a.determinant), a.dependent)
    else:
        result = k_anonymity(rows, tuple(a.fields))
    _dump(asdict(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
