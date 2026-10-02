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

    from shape.artifact.io import read_manifest_bytes

    try:
        return json.loads(read_manifest_bytes(path)).get("kind")
    except (OSError, ValueError, KeyError, AttributeError, zipfile.BadZipFile, RecursionError):
        return None


def _is_profile_or_missing(path):
    """Route to the section 12.2 check/diff (exit 2 on input errors) for profile artifacts,
    and for a path that does not exist, which no legacy command can read either."""
    return _artifact_kind(path) == "profile" or not os.path.exists(path)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


_VERIFIED = set()  # inputs ``--verify`` has checked in this run


def _notices_to_stderr():
    """Route "artifact not verified" notices to stderr as ``shape: note: ...``, once per message,
    without importing the artifact modules (they load only for commands that read artifacts).
    Returns what to restore."""
    import warnings

    previous = warnings.showwarning
    seen = set()

    def show(message, category, filename, lineno, file=None, line=None):
        if category.__name__ != "ArtifactNotVerifiedWarning":
            return previous(message, category, filename, lineno, file, line)
        text = str(message)
        if any(text.startswith(f"{p} ") for p in _VERIFIED):
            return  # --verify already checked this file; the command's own read has no key
        if text not in seen:
            seen.add(text)
            print(f"shape: note: {text}", file=sys.stderr)

    warnings.showwarning = show
    return previous


def _run(fn, a):
    """Run a profile/check/diff command: 0 ok, 1 failed check or drift, 2 input error."""
    import warnings

    from shape.cli import errors

    restore = _notices_to_stderr()
    try:
        return fn(a)
    except errors.EXPECTED as exc:
        if errors.debug_enabled():
            raise
        # The artifact modules are not imported by the commands that never touch an artifact; an
        # ArtifactSignatureError can only come from one that is.
        artifact_io = sys.modules.get("shape.artifact.io")
        if artifact_io is not None and isinstance(exc, artifact_io.ArtifactSignatureError):
            print(f"shape: signature check failed: {exc}", file=sys.stderr)
            return 1
        return errors.fail(exc)
    finally:
        warnings.showwarning = restore


def _load_json(path):
    """A JSON file's content; a file that is not JSON is an input error that names the file."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not a text file: {exc}") from exc


def _sign_output(a, path):
    """Sign the artifact just written when ``--sign KEY`` was given."""
    if getattr(a, "sign", None):
        from shape.artifact.signing import sign_artifact

        return sign_artifact(path, _private_key(a.sign, a))
    return None


def _private_key(source, a):
    """The private key behind ``source`` (a file, ``-``, ``env://``, ``file://`` or ``kv://``). An
    encrypted key's passphrase comes from ``--passphrase-env``, ``--passphrase-stdin``,
    ``SHAPE_KEY_PASSPHRASE`` or a prompt, and is only asked for when the key is encrypted."""
    from shape.artifact.keys import STDIN, load_private_key, read_passphrase

    use_stdin = bool(getattr(a, "passphrase_stdin", False))
    if use_stdin and source == STDIN:
        raise ValueError("the key and the passphrase cannot both come from standard input")

    def passphrase():
        return read_passphrase(
            env=getattr(a, "passphrase_env", None),
            use_stdin=use_stdin,
            prompt="Private key passphrase: ",
        )

    return load_private_key(source, passphrase)


def _add_passphrase_args(parser):
    parser.add_argument(
        "--passphrase-env",
        metavar="VAR",
        help="read the private key passphrase from this environment variable "
        "(default: SHAPE_KEY_PASSPHRASE, then a prompt)",
    )
    parser.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="read the private key passphrase from the first line of standard input",
    )


_KEY_HELP = (
    "private key: a file, - (standard input), env://VAR, file://PATH or kv://... "
    "(an encrypted key asks for its passphrase)"
)


def _looks_like_artifact(path):
    """True for a path the readers will treat as a Shape artifact. The readers sniff content, not
    the file name, so ``--verify`` must too: deciding by extension let a forged ``x.bin`` or
    ``x.SHAPE`` through unverified (P7-04)."""
    import zipfile

    if path.lower().endswith(".shape"):
        return True
    try:
        with zipfile.ZipFile(path) as z:
            return "manifest.json" in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _verify_inputs(a):
    """``--verify PUBKEY``: every .shape input must carry a valid signature by that key."""
    from shape.artifact.signing import load_public_key, verify_artifact

    key = load_public_key(a.verify)
    for name in ("shape", "before", "after", "target", "observed"):
        path = getattr(a, name, None)
        if isinstance(path, str) and _looks_like_artifact(path):
            verify_artifact(path, key)
            _VERIFIED.add(path)


def _cmd_keygen(a):
    import warnings

    from shape.artifact.keys import (
        KeyFilePermissionWarning,
        UnencryptedKeyWarning,
        read_passphrase,
    )
    from shape.artifact.signing import key_id, load_public_key, write_keypair

    if a.no_passphrase and (a.passphrase_env or a.passphrase_stdin):
        raise ValueError("--no-passphrase cannot be combined with a passphrase option")
    passphrase = None
    if not a.no_passphrase:
        passphrase = read_passphrase(
            env=a.passphrase_env,
            use_stdin=a.passphrase_stdin,
            prompt="New private key passphrase: ",
            confirm=True,
        )
        if passphrase is None:
            raise ValueError(
                "keygen protects the private key with a passphrase: set SHAPE_KEY_PASSPHRASE, "
                "use --passphrase-env VAR or --passphrase-stdin, or run it in a terminal; "
                "--no-passphrase writes an UNENCRYPTED key"
            )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        priv, pub = write_keypair(a.prefix, passphrase, unencrypted=a.no_passphrase)
    for w in caught:
        if issubclass(w.category, UnencryptedKeyWarning):
            print(
                f"shape: WARNING: {priv} is an UNENCRYPTED private key. Anyone who can read it "
                "can sign as you. Prefer a passphrase (omit --no-passphrase).",
                file=sys.stderr,
            )
        elif issubclass(w.category, KeyFilePermissionWarning):
            print(f"shape: warning: {w.message}", file=sys.stderr)
    _dump(
        {
            "encrypted": passphrase is not None,
            "key_id": key_id(load_public_key(pub)),
            "private_key": str(priv),
            "public_key": str(pub),
        }
    )
    return 0


def _cmd_sign(a):
    from shape.artifact.signing import sign_artifact

    kid = sign_artifact(a.shape, _private_key(a.key, a), a.output)
    _dump({"signed": a.output or a.shape, "key_id": kid})
    return 0


def _cmd_verify_signature(a):
    from shape.artifact.signing import load_public_key, verify_artifact

    if not a.key:
        raise ValueError("verifying a .shape artifact needs --key PUBLIC.pub")
    _dump({"artifact": a.shape, **verify_artifact(a.shape, load_public_key(a.key))})
    return 0


def _profile_source(a):
    """What ``shape profile`` reads. A folder is one table (its files are partitions) unless
    ``--dataset`` asks for one table per file, named by the file's stem. A folder whose files
    do not share their columns is refused: read as one table it would be a meaningless merge."""
    if not os.path.isdir(a.src):
        if a.dataset:
            raise ValueError(f"--dataset needs a folder of table files, and {a.src} is not one")
        return a.src
    from shape.profile.reference.sources import folder_is_one_table, folder_tables

    if a.dataset:
        return {name: str(path) for name, path in folder_tables(a.src).items()}
    if not folder_is_one_table(a.src):
        raise ValueError(
            f"the files in {a.src} do not share their columns, so the folder is not one table: "
            "add --dataset to profile it as several tables (one per file, named by the file "
            "name without its extension)"
        )
    return a.src


def _profile_name(a):
    """``--name``, else the name of the profile ``-o`` is about to overwrite (so a versioned
    ``.shape`` keeps its name when the input file changes), else None: the input's own name."""
    if a.name:
        return a.name
    if _artifact_kind(a.output) == "profile":
        from shape.artifact.io import read_manifest_bytes

        return str(json.loads(read_manifest_bytes(a.output)).get("name") or "") or None
    return None


def _warn_empty(a, prof):
    """A table with 0 rows is almost always a pipeline mistake: warn, or refuse with
    ``--fail-on-empty``."""
    empty = [n for n, t in prof.tables.items() if not t["row_count"]]
    if not empty:
        return
    what = f"{a.src}" if len(prof.tables) == 1 else f"{a.src} (table {', '.join(empty)})"
    msg = f"{what} has 0 rows: the profile holds no data, and a diff against it is meaningless"
    if a.fail_on_empty:
        raise ValueError(msg)
    print(f"shape: warning: {msg}", file=sys.stderr)


def _cmd_profile(a):
    import shape

    if not a.output:
        raise ValueError("profile needs -o OUT.shape")
    prof = shape.profile(
        _profile_source(a), name=_profile_name(a), version=a.delta_version, as_of=a.as_of
    )
    _warn_empty(a, prof)
    content_id = shape.save(prof, a.output)
    key_id = _sign_output(a, a.output)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(prof.to_html())
    if a.json:
        _write_json(a.json, prof.summary())
    out = {"written": a.output, "shape_content_id": content_id}
    if prof.provenance is not None:
        out["provenance"] = prof.provenance
    if key_id:
        out["signed_by"] = key_id
    _dump(out)
    return 0


def _cmd_plan_profile(a):
    """``shape plan PROFILE.shape``: the fitted schema's plan."""
    import shape
    from shape.generation.fit import fit_schema

    plan = fit_schema(shape.load(a.shape), rows=a.rows).plan
    out = plan.to_dict()
    if a.status:
        out["items"] = [x for x in out["items"] if x["status"] in a.status]
    _dump(out)
    return 0


def _cmd_stream_profile(a):
    from shape.streaming.cli import run

    return run(a)


def _cmd_inspect(a):
    """Print what a .shape artifact holds: a profile or a Shape model."""
    if a.pretty:
        from shape.cli.gitcmds import render

        sys.stdout.write(render(a.shape, "json"))
    elif _artifact_kind(a.shape) == "profile":
        import shape

        prof = shape.load(a.shape)
        doc = {"kind": "profile", "name": prof.name, "profile": prof.to_dict()}
        if prof.provenance is not None:
            doc["provenance"] = prof.provenance
        _dump(doc)
    elif str(a.shape).endswith(".shape"):
        from shape.artifact import read_model

        read = read_model(a.shape)
        manifest, model = read
        _dump({"kind": "model", "manifest": manifest, "shape": model, "signature": read.signature})
    else:
        _dump(_load_json(a.shape))
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


_DIFF_THRESHOLD_FLAGS = (
    ("--null-rate", "null_rate", float),
    ("--cardinality-ratio-max", "cardinality_ratio_max", float),
    ("--cardinality-ratio-min", "cardinality_ratio_min", float),
    ("--mean-shift-std", "mean_shift_std", float),
    ("--min-severity", "min_severity", str),
)


def _diff_policy_arguments(d):
    """The ``shape diff`` flags that set thresholds, ignore columns and read a policy file."""
    g = d.add_argument_group("drift thresholds (defaults: docs/DRIFT.md)")
    for flag, key, kind in _DIFF_THRESHOLD_FLAGS:
        g.add_argument(flag, dest=f"th_{key}", type=kind, metavar=key.upper())
    g.add_argument(
        "--threshold",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="any threshold by name (repeatable), e.g. --threshold category_tvd=0.2",
    )
    g.add_argument(
        "--column-threshold",
        action="append",
        default=[],
        metavar="COLUMN:KEY=VALUE",
        help="a threshold for the columns COLUMN matches (a name, table.column or a glob)",
    )
    g.add_argument("--ignore", metavar="COL1,COL2", help="columns to leave out (table.column ok)")
    g.add_argument("--only", metavar="COL1,COL2", help="compare only these columns")
    g.add_argument(
        "--policy",
        metavar="POLICY.json",
        help='thresholds, "columns", "ignore" and "only" in one JSON file (a contract\'s '
        '"drift" object works)',
    )


def _threshold_value(key, raw):
    return raw if key == "min_severity" else float(raw)


def _diff_options(a):
    """``shape.diff`` keyword arguments from the command line; a bad value is an input error."""
    thresholds = {}
    for _flag, key, _kind in _DIFF_THRESHOLD_FLAGS:
        if getattr(a, f"th_{key}") is not None:
            thresholds[key] = getattr(a, f"th_{key}")
    for item in a.threshold:
        key, sep, raw = item.partition("=")
        if not sep:
            raise ValueError(f"--threshold wants KEY=VALUE, got {item!r}")
        thresholds[key] = _threshold_value(key, raw)
    columns = {}
    for item in a.column_threshold:
        column, sep, rest = item.rpartition(":")
        key, eq, raw = rest.partition("=")
        if not sep or not eq:
            raise ValueError(f"--column-threshold wants COLUMN:KEY=VALUE, got {item!r}")
        columns.setdefault(column, {})[key] = _threshold_value(key, raw)

    def names(raw):
        return [n.strip() for n in raw.split(",") if n.strip()] if raw else None

    return {
        "thresholds": thresholds or None,
        "column_thresholds": columns or None,
        "ignore_columns": names(a.ignore),
        "only_columns": names(a.only),
        "policy": a.policy,
    }


def _cmd_diff(a):
    import shape

    result = shape.diff(shape.load(a.before), shape.load(a.after), **_diff_options(a))
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
    from shape.quality import (
        VerifyReport,
        VerifyRunner,
        load_gate_schema,
        load_tables,
        load_verify_config,
    )
    from shape.quality.verify import data_files

    tables = load_tables(a.shape, a.format)
    if not tables:
        raise ValueError(f"no {a.format} data files found in {a.shape}")
    schema = load_gate_schema(a.schema) if a.schema else None
    config = load_verify_config(a.config) if a.config else None
    result = VerifyRunner(
        schema,
        a.statistical,
        a.shape,
        a.schema,
        config,
        a.config,
        data_files(a.shape, a.format),
    ).run(tables)
    print(f"Shape {_version()} - Verify\n")
    print(f"Data path:   {a.shape}")
    if a.schema:
        print(f"Schema:      {a.schema}")
    if a.config:
        print(f"Config:      {a.config}")
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


_VERIFY_HELP = (
    "require every .shape input to be signed by this public key: a file, - (standard input), "
    "env://VAR, file://PATH or kv://... (exit 1 if not)"
)


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
        "uri",
        metavar="URI",
        help="kafka://host:9092/TOPIC, eventhubs://NAMESPACE/HUB, or no broker at all: a file "
        "(file:///PATH or PATH), a folder or glob of files, or - for standard input, as JSON "
        "lines (what `shape emit` and `shape stream` write), CSV or Parquet",
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
        "--order",
        choices=("file", "event-time"),
        help="files only: replay in file order (default) or sorted by event time (reads every "
        "row into memory first)",
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
    from shape.cli import gitcmds

    p = argparse.ArgumentParser(prog="shape", description="Shape as Code")
    p.add_argument("--version", "-V", action="store_true", help="print the version and exit")
    g = p.add_argument_group("run logging and metrics (before the command)")
    g.add_argument("--log-json", action="store_true", help="log JSON lines to stderr")
    g.add_argument("--log-level", default="INFO", metavar="LEVEL", help="log level (default INFO)")
    g.add_argument("--metrics", metavar="FILE", help="write the run's metrics to FILE as JSON")
    g.add_argument(
        "--debug",
        action="store_true",
        help="show the traceback of an error instead of a one-line message (also SHAPE_DEBUG=1)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    dr = sub.add_parser("doctor", help="check this installation: version, kernel, packages")
    dr.add_argument("--json", action="store_true", help="print the report as JSON")
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
    c = sub.add_parser(
        "capture",
        help="write a Shape model of a CSV (for `query`, `compatibility`, `plan`); "
        "use `shape profile` for profiles",
        description="Capture a CSV file as a Shape model: JSON, or a model .shape with -o "
        "OUT.shape. It reads CSV only and writes a model, which `shape query`, `shape "
        "compatibility` and `shape plan` read. `shape profile` is the command for profiling "
        "data: it reads CSV, Parquet, JSONL, folders and Delta tables, and its profile feeds "
        "`check`, `diff` and `generate --from`.",
    )
    c.add_argument("csv")
    c.add_argument("-o", "--output")
    c.add_argument("--sign", metavar="KEY", help="sign the written .shape; KEY: " + _KEY_HELP)
    _add_passphrase_args(c)
    pr = sub.add_parser(
        "profile",
        help="profile a file, glob, directory or Delta table",
        epilog="also: `shape profile safe PROFILE.shape -o SAFE.json` writes the share-safe "
        "form; `shape profile validate --safe ARTIFACT` scans it for leaks; "
        "`shape profile export|import|list|validate` and `shape profile registry "
        "list|save|delete|tag|diff|reindex|validate` manage profiles and the profile registry",
    )
    pr.add_argument("src", metavar="SRC")
    pr.add_argument("-o", "--output", metavar="OUT")
    pr.add_argument("--sign", metavar="KEY", help="sign the written .shape; KEY: " + _KEY_HELP)
    _add_passphrase_args(pr)
    pr.add_argument(
        "--dataset",
        action="store_true",
        help="SRC is a folder of table files: profile one table per file, named by the file name "
        "without its extension (without it a folder is one table)",
    )
    pr.add_argument(
        "--name",
        metavar="NAME",
        help="the profile's name (default: the input's file name, or, when -o overwrites a "
        "profile, that profile's name, so re-profiling a versioned file keeps a stable name)",
    )
    pr.add_argument(
        "--version",
        dest="delta_version",
        type=int,
        metavar="N",
        help="a Delta table: profile version N instead of the latest",
    )
    pr.add_argument(
        "--as-of",
        metavar="TIMESTAMP",
        help="a Delta table: profile the newest version committed at or before this ISO-8601 "
        "time (no zone means UTC), instead of the latest",
    )
    pr.add_argument(
        "--fail-on-empty",
        action="store_true",
        help="exit 2 instead of warning when a table has 0 rows",
    )
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
    _diff_policy_arguments(d)
    sh = sub.add_parser(
        "inspect",
        aliases=["show"],
        help="print a .shape artifact's manifest and contents (`show` is an alias)",
        description="Print what a .shape artifact holds (a profile or a Shape model) as one JSON "
        "line. `shape show` is an alias of `shape inspect`.",
    )
    sh.add_argument("shape", metavar="ARTIFACT.shape")
    sh.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    sh.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-printed JSON with sorted keys, one value per line (git-diffable)",
    )
    gitcmds.add_parsers(sub)
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
    kg.add_argument(
        "prefix",
        help="writes PREFIX.key (private, passphrase-protected, mode 0600 where the OS has "
        "mode bits) and PREFIX.pub",
    )
    kg.add_argument(
        "--no-passphrase",
        action="store_true",
        help="write an UNENCRYPTED private key (warns; keep the file out of reach)",
    )
    _add_passphrase_args(kg)
    sg = sub.add_parser("sign", help="sign a .shape artifact")
    sg.add_argument("shape", metavar="ARTIFACT.shape")
    sg.add_argument("--key", required=True, metavar="KEY", help=_KEY_HELP)
    _add_passphrase_args(sg)
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
    vf.add_argument(
        "--config",
        metavar="CONFIG.json",
        help="verify configuration (format shape-verify-config): ranges, date_range, no_future, "
        "ordering, baseline, file_paths; runs the range, temporal, drift and file gates",
    )
    vf.add_argument("--statistical", action="store_true", help="add KS and chi-squared tests")
    vf.add_argument("-o", "--output", metavar="REPORT", help="write a .json or .md report")
    vf.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    qu = sub.add_parser("quality")
    qu.add_argument("csv")
    qu.add_argument("--reference")
    from shape.cli.generation import add_arguments as add_generation_arguments

    add_generation_arguments(sub)
    from shape.cli.learn import add_arguments as add_learn_arguments

    add_learn_arguments(sub)
    from shape.cli.emit import add_arguments as add_emit_arguments

    add_emit_arguments(sub)
    from shape.cli.stream import add_arguments as add_stream_arguments

    add_stream_arguments(sub)
    from shape.cli.mask import add_arguments as add_mask_arguments

    add_mask_arguments(sub)
    from shape.cli.incremental import add_arguments as add_incremental_arguments

    add_incremental_arguments(sub)
    from shape.cli.chaos import add_arguments as add_chaos_arguments

    add_chaos_arguments(sub)
    from shape.cli.drift_plan import add_arguments as add_drift_plan_arguments

    add_drift_plan_arguments(sub)
    from shape.cli.pack import add_arguments as add_pack_arguments

    add_pack_arguments(sub)
    from shape.cli.transform import add_arguments as add_transform_arguments

    add_transform_arguments(sub)
    from shape.cli.jobs import add_arguments as add_jobs_arguments

    add_jobs_arguments(sub)
    fi = sub.add_parser(
        "fidelity",
        aliases=["compare"],
        help="score synthetic data against reference data, per column and per table",
        description=(
            "Compare SYNTHETIC with REFERENCE (a CSV, Parquet or JSONL file, or a directory of one "
            "file per table; a profile is not a reference, use `shape diff`) and "
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
    co = sub.add_parser(
        "compatibility",
        help="compare two Shape models (not profiles; use `shape diff` for those)",
        description="Check whether AFTER is compatible with BEFORE. Both are Shape model "
        "artifacts (`shape capture ... -o X.shape`) or model JSON; profiles written by "
        "`shape profile` are compared with `shape diff`.",
    )
    co.add_argument("before", metavar="BEFORE", help="a model .shape or model JSON")
    co.add_argument("after", metavar="AFTER", help="a model .shape or model JSON")
    co.add_argument("--mode", choices=("backward", "forward", "full"), default="backward")
    gp = sub.add_parser(
        "plan",
        help="what generating from a profile preserves, and what it does not",
        description="Fit the generation schema of a profile (`shape generate --from`) and list, "
        "for every field of the profile, whether generated data keeps it: preserved, "
        "approximate, or not_modelled (with the reason). Evidence documents are checked "
        "against what the generator can build from them.",
    )
    gp.add_argument("shape", metavar="PROFILE.shape")
    gp.add_argument(
        "--status",
        choices=("preserved", "approximate", "not_modelled", "unavailable"),
        action="append",
        help="list only items with this status (repeatable)",
    )
    gp.add_argument("--rows", type=int, metavar="N", help="plan for N rows (a one-table profile)")
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
    from shape.cli.registry import add_arguments as add_registry_arguments

    add_registry_arguments(sub)
    for rec in plugin_commands:  # listed in --help only; the plugin loads when it is run
        sub.add_parser(rec.name, help=f"(plugin {rec.source})", add_help=False)
    return p


def _version():
    from shape import __version__

    return __version__


_GLOBAL_VALUE_OPTIONS = ("--log-level", "--metrics")


def _split_global(argv):
    """Take the global options (``--log-json``, ``--log-level L``, ``--metrics FILE``, ``--debug``)
    off the front of ``argv``; they may also come from SHAPE_LOG_JSON, SHAPE_LOG_LEVEL,
    SHAPE_METRICS and SHAPE_DEBUG."""
    opts = {
        "log_json": os.environ.get("SHAPE_LOG_JSON", "") not in ("", "0"),
        "log_level": os.environ.get("SHAPE_LOG_LEVEL", "INFO"),
        "metrics": os.environ.get("SHAPE_METRICS") or None,
        "debug": False,
    }
    rest = list(argv)
    while rest and rest[0].startswith("--"):
        name, eq, value = rest[0].partition("=")
        if name == "--log-json":
            opts["log_json"] = True
            rest.pop(0)
        elif name == "--debug":
            opts["debug"] = True
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
    _VERIFIED.clear()
    from shape.cli import errors

    opts, argv = _split_global(sys.argv[1:] if argv is None else argv)
    errors.set_debug(opts["debug"])
    return errors.guarded(lambda: _logged(opts, argv))


def _logged(opts, argv):
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
        from shape.cli import profiles

        if profiles.routes(argv[1:]):
            return profiles.main(argv[1:])
        from shape.privacy.cli import main as privacy_main

        return privacy_main(argv[1:])
    if argv[:1] == ["profile"] and argv[1:2] in (["export"], ["import"], ["list"], ["registry"]):
        from shape.cli import profiles

        return profiles.main(argv[1:])
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
    if a.cmd == "emit":
        from shape.cli.emit import run as run_emit

        return _run(run_emit, a)
    if a.cmd == "stream":
        from shape.cli.stream import run as run_stream

        return _run(run_stream, a)
    if a.cmd == "from-ddl":
        return _run(_cmd_from_ddl, a)
    if a.cmd in ("continue", "time-travel"):
        from shape.cli.incremental import run as run_incremental

        return _run(run_incremental, a)
    if a.cmd == "chaos":
        from shape.cli.chaos import run as run_chaos

        return _run(run_chaos, a)
    if a.cmd == "generate-drift":
        from shape.cli.drift_plan import run as run_drift_plan

        return _run(run_drift_plan, a)
    if a.cmd == "pack":
        from shape.cli.pack import run as run_pack

        return _run(run_pack, a)
    if a.cmd == "learn":
        from shape.cli.learn import run as run_learn

        return _run(run_learn, a)
    if a.cmd == "mask":
        from shape.cli.mask import run as run_mask

        return _run(run_mask, a)
    if a.cmd == "jobs":
        from shape.cli.jobs import run as run_jobs

        return _run(run_jobs, a)
    if a.cmd == "transform":
        from shape.cli.transform import run as run_transform

        return _run(run_transform, a)
    if a.cmd in ("cat", "git-setup"):
        from shape.cli.gitcmds import run as run_gitcmds

        return _run(run_gitcmds, a)
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
        from shape.cli.doctor import run as run_doctor

        return run_doctor(a)
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
            raise ValueError("capture --sign needs -o OUT.shape")
        raw = json.dumps(obj, sort_keys=True, indent=2, default=str)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(raw + "\n")
        else:
            print(raw)
        return 0
    if a.cmd == "diff":
        from shape.drift import compare

        _dump([asdict(v) for v in compare(_load_json(a.before), _load_json(a.after))])
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
        ref = _load_json(a.reference) if a.reference else capture_rows(rows).to_dict()
        result = validate_rows(rows, infer_rules(ref))
        _dump({"passed": result.passed, "violations": [asdict(v) for v in result.violations]})
        return 0 if result.passed else 2
    if a.cmd == "drift":
        from shape.cli.tiers import run_drift

        return _run(run_drift, a)
    if a.cmd in ("fidelity", "compare") and not a.tier and _artifact_kind(a.reference) == "profile":
        print(
            f"shape: error: {a.reference} is a profile, and `shape fidelity` compares data with "
            "data: pass the reference data (CSV, Parquet, JSONL or a folder of them), or profile "
            "the synthetic data and compare the two profiles with `shape diff`",
            file=sys.stderr,
        )
        return 2
    if a.cmd in ("fidelity", "compare") and (a.tier or not str(a.reference).endswith(".json")):
        return _run(_cmd_fidelity, a)
    if a.cmd in ("fidelity", "compare"):
        from shape.generation import certify

        ref = _load_json(a.reference)
        cert = certify(ref, list(_rows(a.csv)), tolerance=a.tolerance)
        _dump(cert.to_dict())
        return 0 if cert.passed else 3
    if a.cmd == "plan" and _artifact_kind(a.shape) == "profile":
        return _run(_cmd_plan_profile, a)
    if a.cmd == "query" and _artifact_kind(a.shape) == "profile":
        print(
            "shape: error: `shape query` does not read profiles made by `shape profile` yet "
            "(planned). Use `shape check`, `shape diff` or `shape plan`.",
            file=sys.stderr,
        )
        return 2
    if a.cmd in ("query", "check", "plan"):
        from shape.artifact import read_shape

        _, s = read_shape(a.shape) if str(a.shape).endswith(".shape") else ({}, _load_json(a.shape))
        if a.cmd == "query":
            from shape.query import query as shape_query

            _dump({"result": shape_query(s, a.expression)})
            return 0
        if a.cmd == "plan":
            from shape.generation.fidelity import plan_reconstruction

            _dump(plan_reconstruction(s).to_dict())
            return 0
        from shape.contracts import evaluate_contract

        contract = _load_json(a.contract)
        r = evaluate_contract(s, contract)
        _dump(r.to_dict())
        return 0 if r.passed else 4
    if a.cmd in ("compatibility", "certify-shapes"):
        from shape.artifact import read_shape

        def LS(p):
            if _artifact_kind(p) == "profile":
                raise ValueError(
                    f"{p} is a profile (made by `shape profile`), and `shape {a.cmd}` reads Shape "
                    "model artifacts or model JSON; compare two profiles with `shape diff`"
                )
            return read_shape(p)[1] if str(p).endswith(".shape") else _load_json(p)

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
        from shape.cli.registry import run as run_registry

        return run_registry(a)
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
