"""The generation commands: ``generate``, ``describe``, ``list`` and ``presets`` (P4-10).

This module imports nothing heavy at load: the argument definitions are plain argparse, and every
command imports numpy, pyarrow and the engine when it runs, so ``shape version`` stays under the
start-up budget (T-18). ``from-ddl`` and ``validate`` live in ``main`` and ``validate``.

A *target* is the name of an installed domain (``retail``) or the path of a generation schema file
(what ``shape from-ddl`` writes). A domain may offer a ``star`` schema next to ``3nf``
(``--mode``); a schema file has the one mode it was written in.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shape.generation.schema import GenSchema

DEFAULT_TEMPLATE = "{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}"
SQL_DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")
MODES = ("3nf", "star")


def _format(text: str) -> str:
    """The ``--format`` type: any installed sink, checked when the option is given."""
    if text == "summary":
        return text
    from shape.generation.output import format_argument

    try:
        return format_argument(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


_TARGET_HELP = (
    "an installed domain (see `shape list`) or a generation schema file "
    "(`shape from-ddl` writes one)"
)


def add_arguments(sub: Any) -> None:
    """Register ``generate``, ``describe``, ``list`` and ``presets`` on the subparsers."""
    ge = sub.add_parser(
        "generate",
        help="generate data for a domain or a generation schema",
        description="Generate every table of a domain (or of a generation schema file) and write "
        "it in the chosen format. `--dry-run` plans the run and generates nothing. With no "
        "target, `--rows N` prints N demo rows as JSON lines (not from any schema). "
        "`--rows TABLE=N` (repeatable) sets the row count of one table of a schema.",
    )
    ge.add_argument("target", nargs="?", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    ge.add_argument(
        "--mode", choices=MODES, help="the schema mode of a domain (default: its own default)"
    )
    ge.add_argument("--scale", metavar="PRESET", help="the scale preset (see `shape presets`)")
    ge.add_argument("--seed", type=int, help="the seed (default: the schema's)")
    ge.add_argument(
        "--format",
        "-f",
        type=_format,
        default="summary",
        metavar="FORMAT",
        help="summary (default: print the plan result, write nothing), or csv, tsv, jsonl, "
        "parquet, ipc, excel, sql, delta, or any installed sink (see `shape plugins list`)",
    )
    ge.add_argument(
        "-o", "--output", "--out", metavar="DIR", help="the output directory (needed to write)"
    )
    ge.add_argument("--dry-run", action="store_true", help="plan the run; generate nothing")
    ge.add_argument("--json", action="store_true", help="print the result or the plan as JSON")
    ge.add_argument(
        "--chunk-rows", type=int, metavar="N", help="rows per chunk (output does not depend on it)"
    )
    ge.add_argument(
        "--from",
        dest="from_profile",
        metavar="X.shape",
        help="generate from a profile (a .shape file): fits strategies to it",
    )
    ge.add_argument(
        "--decisions",
        metavar="DECISIONS.json",
        help="with --from: apply a decision file (`shape proposals`)",
    )
    ge.add_argument(
        "--rows",
        action="append",
        metavar="N|TABLE=N",
        help="TABLE=N (repeatable): rows of one table of the schema; N: with no target, print "
        "N demo rows as JSON lines; with --from, the rows of a one-table profile",
    )
    from shape.cli.scale import add_arguments as add_scale_arguments

    add_scale_arguments(ge)
    sql = ge.add_argument_group("sql output (--format sql)")
    sql.add_argument("--sql-dialect", choices=SQL_DIALECTS, default="tsql", help="default: tsql")
    sql.add_argument("--schema-name", metavar="NAME", help="qualify tables with this schema")
    sql.add_argument(
        "--batch-size", type=int, metavar="N", help="rows per INSERT (T-SQL: at most 1000)"
    )
    for flag, what in (
        ("sql-ddl", "CREATE TABLE"),
        ("sql-drop", "DROP TABLE IF EXISTS"),
        ("sql-go", "GO"),
    ):
        sql.add_argument(
            f"--{flag}",
            action=argparse.BooleanOptionalAction,
            default=None,
            help=f"write {what} statements (default: yes)",
        )
    xl = ge.add_argument_group("excel output (--format excel: one workbook, a sheet per table)")
    xl.add_argument(
        "--chaos-log",
        metavar="FILE",
        help="a chaos ground-truth log (`shape chaos`): the _README sheet lists what it planted",
    )
    xl.add_argument(
        "--drift-plan",
        metavar="FILE",
        help="a drift plan or its answer key: the _README sheet lists the planted drift",
    )
    ge.add_argument(
        "--fingerprint",
        action="store_true",
        help="with --format parquet or delta: write the shape.fingerprint footer/property "
        "(table_id, run dataset_id, reproducibility tuple); the tables are generated whole "
        "first (`shape fingerprint verify` checks it, docs/FINGERPRINT.md)",
    )
    dl = ge.add_argument_group("delta output (--format delta)")
    dl.add_argument("--delta-mode", choices=("overwrite", "append"), default="overwrite")
    dl.add_argument("--partition-by", metavar="COLUMN", action="append", help="repeatable")
    from shape.cli.landing import add_landing_arguments

    add_landing_arguments(ge, default_template=DEFAULT_TEMPLATE)
    from shape.cli.to import add_to_arguments

    add_to_arguments(ge)

    de = sub.add_parser(
        "describe",
        help="show a domain's or schema's tables, relationships and rules",
        description="Describe the tables, columns, relationships, business rules and scale "
        "presets of a domain or generation schema.",
    )
    de.add_argument("target", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    de.add_argument("--mode", choices=MODES, help="the schema mode of a domain")
    de.add_argument("--scale", metavar="PRESET", help="show the row counts of this preset")
    de.add_argument("--json", action="store_true", help="print the description as JSON")

    li = sub.add_parser("list", help="list the installed domains")
    li.add_argument("--json", action="store_true", help="print the list as JSON")

    pr = sub.add_parser(
        "presets",
        help="show the scale presets of a domain (rows per table)",
        description="Show the scale presets of one domain or schema file, or the preset names of "
        "every installed domain.",
    )
    pr.add_argument("target", nargs="?", metavar="DOMAIN|SCHEMA.json", help=_TARGET_HELP)
    pr.add_argument("--mode", choices=MODES, help="the schema mode of a domain")
    pr.add_argument("--json", action="store_true", help="print the presets as JSON")


# ---- targets ------------------------------------------------------------------------------


def _is_file(target: str) -> bool:
    return Path(target).is_file() or target.lower().endswith(".json")


def load_target(target: str, mode: str | None = None) -> GenSchema:
    """The generation schema of a domain name or schema file."""
    from shape.generation.schema import GenSchema

    if _is_file(target):
        document = json.loads(Path(target).read_text(encoding="utf-8"))
        schema = GenSchema.from_dict(document)
        if mode is not None and mode != schema.model.schema_mode:
            raise ValueError(
                f"{target} is a {schema.model.schema_mode!r} schema; a schema file has one mode "
                f"(--mode {mode} is for domains)"
            )
        return schema
    from shape.generation.domains import load_domain

    return load_domain(target, mode=mode).schema


def _check_scale(schema: GenSchema, scale: str | None) -> None:
    presets = schema.generation.scales
    if scale is not None and presets and scale not in presets:
        raise ValueError(f"unknown scale {scale!r}; the presets are: {', '.join(presets)}")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


# ---- generate -----------------------------------------------------------------------------


def _sink_options(a: argparse.Namespace) -> dict[str, Any]:
    """The writer options the command line sets (only those given)."""
    fmt = a.format
    options: dict[str, Any] = {}
    if fmt == "sql":
        options["sql_dialect"] = a.sql_dialect
        for key, value in (
            ("schema_name", a.schema_name),
            ("batch_size", a.batch_size),
            ("ddl", a.sql_ddl),
            ("drop", a.sql_drop),
            ("go", a.sql_go),
        ):
            if value is not None:
                options[key] = value
    elif fmt == "excel":
        for key, value in (("chaos_log", a.chaos_log), ("drift_plan", a.drift_plan)):
            if value is not None:
                options[key] = value
    elif fmt == "delta":
        options["mode"] = a.delta_mode
        if a.partition_by:
            options["partition_by"] = list(a.partition_by)
    return options


def _rows_arg(a: argparse.Namespace) -> tuple[int | None, dict[str, int]]:
    """``--rows`` as ``(N, {table: N})``: at most one bare count, any number of ``TABLE=N``."""
    bare: int | None = None
    per_table: dict[str, int] = {}
    for item in a.rows or ():
        name, eq, text = item.partition("=")
        try:
            count = int(text if eq else name)
        except ValueError:
            raise ValueError(f"--rows takes N or TABLE=N, not {item!r}") from None
        if count < 0:
            raise ValueError(f"--rows counts cannot be negative: {item!r}")
        if eq:
            per_table[name] = count
        elif bare is not None:
            raise ValueError("--rows N can be given once")
        else:
            bare = count
    return bare, per_table


def _demo_rows(a: argparse.Namespace, n: int) -> int:
    from shape.generation import Choice, GenerationPlan, SequenceStrategy

    plan = GenerationPlan(
        (("id", SequenceStrategy()), ("segment", Choice(("A", "B", "C"), (0.7, 0.2, 0.1)))),
        a.seed or 0,
    )
    print("shape: demo rows, not generated from any schema", file=sys.stderr)
    for row in plan.rows(n):
        print(json.dumps(row, sort_keys=True))
    return 0


def cmd_generate(a: argparse.Namespace) -> int:
    """``shape generate``: 0 generated (or the plan is sound), 1 a dry run found problems."""
    bare, per_table = _rows_arg(a)
    if a.fingerprint:
        if a.format not in ("parquet", "delta"):
            raise ValueError("--fingerprint is for --format parquet or delta")
        for flag, given in (("--scale-mode", a.scale_mode), ("--to", a.to)):
            if given:
                raise ValueError(f"--fingerprint does not combine with {flag}")
    if a.scale_mode and a.from_profile:
        raise ValueError("--scale-mode does not combine with --from")
    if a.decisions and not a.from_profile:
        raise ValueError("--decisions goes with --from PROFILE.shape")
    if a.to and a.scale_mode:
        raise ValueError("--to does not combine with --scale-mode (use --sink there)")
    if a.from_profile:
        if per_table:
            raise ValueError("--rows TABLE=N is for a schema; with --from give --rows N")
        return _generate_from_profile(a, bare)
    if a.target is None:
        if bare is None or per_table:
            raise ValueError("name a domain or a schema file (see `shape list`), or give --rows N")
        return _demo_rows(a, bare)
    if bare is not None:
        raise ValueError(
            "--rows N prints demo rows and takes no target; give --rows TABLE=N to set the rows "
            "of one table"
        )
    if a.scale_mode:
        from shape.cli.scale import run_scale

        return run_scale(a)
    from shape.cli.lifecycle import quick_exit_allowed

    if quick_exit_allowed:
        # A process that ends when the files are written: the collector would only walk the
        # objects the imports make (about 10 ms), and generation makes no reference cycles.
        gc.disable()
    from shape.generation.engine import Engine
    from shape.runlog import current

    run = current()
    schema = load_target(a.target, a.mode)
    _check_scale(schema, a.scale)
    kwargs: dict[str, Any] = {}
    if a.chunk_rows:
        kwargs["chunk_rows"] = a.chunk_rows
    unknown = sorted(set(per_table) - set(schema.tables))
    if unknown:
        raise ValueError(
            f"--rows names no such table: {', '.join(unknown)}; "
            f"the tables are: {', '.join(schema.tables)}"
        )
    engine = Engine(schema, scale=a.scale, seed=a.seed, row_counts=per_table, **kwargs)
    run.set(
        domain=schema.model.domain or schema.model.name,
        mode=schema.model.schema_mode,
        scale=engine.schema.generation.scale,
        seed=engine.seed,
        format=a.format,
    )
    if a.dry_run:
        plan = engine.dry_run()
        run.set(rows=plan.total_rows, tables=len(plan.order))
        if a.json:
            _dump(plan.to_dict())
        else:
            print(plan.render())
        return 0 if plan.ok else 1
    return _generate(a, engine)


def _generate_from_profile(a: argparse.Namespace, rows: int | None) -> int:
    """``shape generate --from X.shape``: fit a schema to the profile and generate it."""
    if a.target is not None:
        raise ValueError("--from takes a profile: give no domain or schema file")
    if a.mode is not None:
        raise ValueError("--mode is for domains; a profile has one schema")
    import shape
    from shape.generation.engine import Engine
    from shape.generation.fit import PRESET, fit_schema
    from shape.runlog import current

    run = current()
    from shape.cli.proposals import load_decisions

    fitted = fit_schema(
        shape.load(a.from_profile), rows=rows, decisions=load_decisions(a.decisions)
    )
    schema = fitted.schema
    _check_scale(schema, a.scale)
    counts = fitted.plan.counts()
    print(
        "profile fit: "
        + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in sorted(counts.items()))
        + " (see `shape plan`)",
        file=sys.stderr,
    )
    kwargs: dict[str, Any] = {}
    if a.chunk_rows:
        kwargs["chunk_rows"] = a.chunk_rows
    engine = Engine(schema, scale=a.scale or PRESET, seed=a.seed, **kwargs)
    run.set(
        domain=schema.model.domain,
        mode=schema.model.schema_mode,
        scale=engine.schema.generation.scale,
        seed=engine.seed,
        format=a.format,
    )
    if a.dry_run:
        plan = engine.dry_run()
        run.set(rows=plan.total_rows, tables=len(plan.order))
        if a.json:
            _dump(plan.to_dict())
        else:
            print(plan.render())
        return 0 if plan.ok else 1
    return _generate(a, engine)


def _generate(a: argparse.Namespace, engine: Any) -> int:
    from shape.generation.output import format_summary, write_engine
    from shape.runlog import current

    run = current()
    started = time.perf_counter()
    if a.to:
        from shape.cli.to import run_to

        return run_to(a, engine, started)
    from shape.cli.landing import landing_requested

    if landing_requested(a) and a.format == "summary":
        raise ValueError(
            "the landing options write files: give --format (csv, parquet, jsonl, ...)"
        )
    if a.format == "summary":
        result = engine.generate()
        seconds = time.perf_counter() - started
        counts = {n: result.tables[n].num_rows for n in result.generation_order}
        run.set(rows=sum(counts.values()), tables=len(counts))
        if a.json:
            _dump({"counts": counts, "seconds": round(seconds, 3), "seed": engine.seed})
        else:
            print(format_summary(result))
        return 0
    if not a.output:
        raise ValueError(f"--format {a.format} writes files: give -o DIR")
    if landing_requested(a):
        if a.fingerprint:
            raise ValueError("--fingerprint does not combine with the landing options")
        return _generate_landing(a, engine, started)
    if a.fingerprint:
        from shape.cli.fingerprint import generate_fingerprinted

        paths = generate_fingerprinted(engine, a)
    else:
        paths = write_engine(engine, a.format, a.output, **_sink_options(a))
    seconds = time.perf_counter() - started
    counts = {name: int(rows) for name, rows in engine.row_counts.items() if name in engine.order}
    total = sum(counts.values())
    run.set(rows=total, tables=len(counts), files=len(paths))
    if a.json:
        _dump(
            {
                "format": a.format,
                "output": str(a.output),
                "files": [str(p) for p in paths],
                "counts": counts,
                "seconds": round(seconds, 3),
                "seed": engine.seed,
            }
        )
    else:
        print(
            f"Wrote {len(paths)} {a.format} {'directories' if a.format == 'delta' else 'files'} "
            f"to {a.output}: {total:,} rows in {len(counts)} tables ({seconds:.2f}s)"
        )
    from shape.cli.lifecycle import exit_now

    exit_now(0)  # as the program, nothing is left to do: skip freeing the tables
    return 0


def _generate_landing(a: argparse.Namespace, engine: Any, started: float) -> int:
    """``generate`` with a landing layout: the tables are generated whole, then each is written at
    the path template for the batch date, in its own format."""
    from shape.cli.landing import landing_options
    from shape.generation.landing import write_landing
    from shape.runlog import current

    if a.format in ("delta", "summary"):
        raise ValueError(f"--format {a.format} cannot be combined with a landing layout")
    options = landing_options(a, DEFAULT_TEMPLATE)
    result = engine.generate()
    landed = write_landing(result.tables, a.output, default_format=a.format, **options)
    seconds = time.perf_counter() - started
    counts = {f.table: f.rows for f in landed}
    current().set(rows=sum(counts.values()), tables=len(counts), files=len(landed))
    if a.json:
        _dump(
            {
                "format": a.format,
                "output": str(a.output),
                "batch_date": a.batch_date,
                "files": [
                    {"table": f.table, "format": f.format, "path": str(f.path), "rows": f.rows}
                    for f in landed
                ],
                "counts": counts,
                "seconds": round(seconds, 3),
                "seed": engine.seed,
            }
        )
    else:
        print(
            f"Landed {len(landed)} files under {a.output}: {sum(counts.values()):,} rows in "
            f"{len(counts)} tables ({seconds:.2f}s)"
        )
    return 0


# ---- describe, list, presets --------------------------------------------------------------


def _describe(schema: GenSchema, scale: str | None) -> dict[str, Any]:
    from shape.generation.engine import Engine, order_columns

    engine = Engine(schema, scale=scale)
    tables: dict[str, Any] = {}
    for name in engine.order:
        t = engine.schema.tables[name]
        tables[name] = {
            "rows": engine.row_counts.get(name),
            "primary_key": list(t.primary_key),
            "columns": [
                {
                    "name": c,
                    "type": t.columns[c].type,
                    "strategy": t.columns[c].strategy,
                    "nullable": t.columns[c].nullable or t.columns[c].null_rate > 0,
                }
                for c in order_columns(t)
            ],
        }
    s = engine.schema
    return {
        "name": s.model.name,
        "domain": s.model.domain,
        "mode": s.model.schema_mode,
        "description": s.model.description,
        "scale": s.generation.scale,
        "tables": tables,
        "relationships": [
            {
                "name": r.name,
                "parent": r.parent,
                "parent_columns": list(r.parent_columns),
                "child": r.child,
                "child_columns": list(r.child_columns),
            }
            for r in s.relationships
        ],
        "business_rules": [r.name for r in s.business_rules],
        "presets": list(s.generation.scales),
    }


def cmd_describe(a: argparse.Namespace) -> int:
    schema = load_target(a.target, a.mode)
    _check_scale(schema, a.scale)
    d = _describe(schema, a.scale)
    if a.json:
        _dump(d)
        return 0
    print(f"{d['name']}  domain={d['domain'] or '-'}  mode={d['mode']}  scale={d['scale']}")
    if d["description"]:
        print(d["description"])
    print()
    print(f"{'table':<28}{'rows':>12}{'columns':>9}  primary key")
    for name, t in d["tables"].items():
        print(f"{name:<28}{t['rows']:>12,}{len(t['columns']):>9}  {', '.join(t['primary_key'])}")
    for name, t in d["tables"].items():
        print(f"\n{name}")
        for c in t["columns"]:
            null = " (nullable)" if c["nullable"] else ""
            print(f"  {c['name']:<28}{c['type']:<14}{c['strategy'] or '-'}{null}")
    if d["relationships"]:
        print("\nrelationships")
        for r in d["relationships"]:
            print(
                f"  {r['child']}({', '.join(r['child_columns'])}) -> "
                f"{r['parent']}({', '.join(r['parent_columns'])})"
            )
    if d["business_rules"]:
        print(f"\nbusiness rules: {', '.join(d['business_rules'])}")
    print(f"\nscale presets: {', '.join(d['presets']) or '-'}")
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    from shape.generation.domains import domain_modes, domain_names

    names = domain_names()
    rows = [{"name": n, "modes": list(domain_modes(n))} for n in names]
    if a.json:
        _dump(rows)
        return 0
    if not rows:
        print("no domains installed: pip install 'sqllocks-shape[domains]'")
        return 0
    print(f"{'domain':<24}modes")
    for r in rows:
        print(f"{r['name']:<24}{', '.join(r['modes'])}")
    return 0


def cmd_presets(a: argparse.Namespace) -> int:
    if a.target is None:
        from shape.generation.domains import domain_names, load_domain

        every = {n: list(load_domain(n).schema.generation.scales) for n in domain_names()}
        if a.json:
            _dump(every)
        else:
            for name, presets in every.items():
                print(f"{name:<24}{', '.join(presets)}")
        return 0
    schema = load_target(a.target, a.mode)
    scales = schema.generation.scales
    from shape.generation.engine import Engine

    counts = {p: Engine(schema, scale=p).row_counts for p in scales}
    if a.json:
        _dump(counts)
        return 0
    names = [n for n in schema.tables]
    print(f"{'table':<28}" + "".join(f"{p:>14}" for p in scales))
    for t in names:
        print(f"{t:<28}" + "".join(f"{counts[p].get(t, 0):>14,}" for p in scales))
    print(f"{'total':<28}" + "".join(f"{sum(counts[p].values()):>14,}" for p in scales))
    return 0


COMMANDS = {
    "generate": cmd_generate,
    "describe": cmd_describe,
    "list": cmd_list,
    "presets": cmd_presets,
}


def run(a: argparse.Namespace) -> int:
    return COMMANDS[a.cmd](a)


__all__ = ["COMMANDS", "add_arguments", "load_target", "run"]
