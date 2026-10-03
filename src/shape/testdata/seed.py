"""Seed a test database with generated tables: ``shape seed``.

Every table is written through the installed database sink of the target's scheme (``sqlserver``,
``postgres``, ``mysql``) in foreign-key order: parents before the tables that point at them. A
``sql://DIR`` target writes one INSERT script per table through the ``sql`` sink instead, named
``NN_table.sql`` so the scripts sort in the order to run them.

Modes (``mode``): ``create`` makes the tables and refuses one that exists, before anything is
written; ``truncate`` empties the tables first (a script drops and recreates its tables) and
``append`` adds rows. Rows are written with the sink's own write: a table is one transaction (the
sinks commit after each table), so a failure part way leaves the tables written before it; the
error names them. The same spec, scale, seed and ``truncate`` leave the same contents.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from shape.errors import ShapeError

if TYPE_CHECKING:
    from shape.generation.schema import GenSchema

MODES = ("create", "truncate", "append")
SCRIPT_SCHEME = "sql://"
SINK_FOR_SCHEME = {
    "mssql": "sqlserver",
    "sqlserver": "sqlserver",
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
}
_INSTALL = {
    "postgres": "pip install 'sqllocks-shape[postgres]'",
    "mysql": "pip install 'sqllocks-shape[mysql]'",
    "sqlserver": "pip install 'sqllocks-shape[sqlserver]' (or the sqllocks-shape-fabric plugin)",
}


class SeedRefused(ShapeError):
    """The seed would overwrite something (``create`` over an existing table): nothing was
    written. The command line exits 1."""


@dataclass
class SeedPlan:
    """What a seed run will do. ``target`` has its credentials hidden."""

    target: str
    sink: str
    mode: str
    seed: int
    scale: str
    tables: list[dict[str, Any]] = field(default_factory=list)  # in the order they are written

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "sink": self.sink,
            "mode": self.mode,
            "seed": self.seed,
            "scale": self.scale,
            "tables": self.tables,
            "rows": sum(t["rows"] for t in self.tables),
        }

    def lines(self) -> list[str]:
        out = [
            f"seed plan: {self.target} through the {self.sink} sink, mode {self.mode}, "
            f"seed {self.seed}, scale {self.scale}",
        ]
        out += [f"  {i:>2}. {t['table']}: {t['rows']:,} rows" for i, t in enumerate(self.tables, 1)]
        out.append(f"  {sum(t['rows'] for t in self.tables):,} rows in {len(self.tables)} tables")
        return out


@dataclass
class SeedResult:
    plan: SeedPlan
    written: dict[str, int] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "plan": self.plan.to_dict(),
            "written": self.written,
            "files": self.files,
        }


def load_spec(spec: str) -> GenSchema:
    """The generation schema of a schema file or an installed domain name."""
    from shape.generation.schema import GenSchema

    if Path(spec).is_file():
        try:
            return GenSchema.from_dict(json.loads(Path(spec).read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ShapeError(f"{spec} is not valid JSON: {exc}") from None
    from shape.generation.domains import load_domain

    return load_domain(spec).schema


def _scheme(target: str) -> str:
    return target.partition("://")[0].lower() if "://" in target else ""


def _redacted(target: str) -> str:
    from shape.security.redact import redact_text

    return redact_text(target)


def build_plan(
    spec: str, target: str, *, scale: str | None, seed: int | None, mode: str
) -> tuple[SeedPlan, GenSchema, dict[str, int] | None, str | None]:
    """The plan for a seed run, from the spec alone: nothing is generated or connected to.

    Returns ``(plan, schema, row_counts, preset)`` where the last two go to the engine."""
    from shape.generation.engine import Engine
    from shape.scenario.library.scale import resolve_scale

    if mode not in MODES:
        raise ShapeError(f"unknown mode {mode!r}; the modes are {', '.join(MODES)}")
    scheme = _scheme(target)
    sink = "sql" if target.startswith(SCRIPT_SCHEME) else SINK_FOR_SCHEME.get(scheme, "")
    if not sink:
        raise ShapeError(
            f"cannot seed {_redacted(target)!r}: the target is sql://DIR or a URI with the scheme "
            f"{', '.join(sorted(SINK_FOR_SCHEME))}"
        )
    schema = load_spec(spec)
    preset, rows = resolve_scale(schema, scale)
    engine = Engine(schema, scale=preset, seed=seed, row_counts=rows)
    plan = SeedPlan(
        target=_redacted(target),
        sink=sink,
        mode=mode,
        seed=engine.seed,
        scale=scale or schema.generation.scale,
        tables=[{"table": t, "rows": int(engine.row_counts.get(t, 0))} for t in engine.order],
    )
    return plan, schema, rows, preset


def seed_target(
    spec: str,
    target: str,
    *,
    scale: str | None = None,
    seed: int | None = None,
    mode: str = "create",
    dry_run: bool = False,
    sinks: Mapping[str, Any] | None = None,
    sink_options: Mapping[str, Any] | None = None,
) -> SeedResult:
    """Seed ``target`` with the tables of ``spec`` (a domain name or a schema file).

    ``dry_run`` returns the plan and connects to nothing. ``sinks`` maps a sink name to a sink
    object, for tests; the default is the installed ``shape.sinks`` plugin. ``sink_options`` are
    passed to the database sink as it takes them (``password``, ``user``, ``schema_name``,
    ``credential``, ``commit_rows``): the command line has none, since a secret does not belong
    on one; the sinks read their password environment variables.
    """
    given = dict(sink_options or {})
    plan, schema, rows, preset = build_plan(spec, target, scale=scale, seed=seed, mode=mode)
    if dry_run:
        return SeedResult(plan, dry_run=True)
    sink = _sink(plan.sink, target, sinks)
    names = [t["table"] for t in plan.tables]
    if plan.sink == "sql":
        directory, dialect = _script_target(target)
        scripts = _script_paths(directory, names)
        if mode == "create":
            existing = [p.name for p in scripts.values() if p.exists()]
            if existing:
                raise SeedRefused(
                    f"mode create: {_listed(existing)} already exist in {directory}; "
                    "nothing was written (use --mode truncate to write them again)"
                )
    else:
        scripts, dialect = {}, ""
        if mode == "create":
            present = _existing(sink, target, names, given)
            if present:
                raise SeedRefused(
                    f"mode create: table(s) {_listed(present)} already exist at "
                    f"{plan.target}; nothing was written (use --mode truncate or append)"
                )
    from shape.generation.engine import Engine
    from shape.generation.output import sql_options

    result = Engine(schema, scale=preset, seed=seed, row_counts=rows).generate()
    out = SeedResult(plan)
    for name in names:
        table = result.tables[name]
        options: dict[str, Any] = {**sql_options(schema, name), "schema": table.schema}
        if plan.sink == "sql":
            options.update(_script_options(mode, dialect, plan))
            uri = str(scripts[name])
        else:
            options.update(given)
            options["write_mode"] = mode
            uri = target
        try:
            out.written[name] = int(sink.write(uri, name, iter(table.to_batches()), **options))
        except SeedRefused:
            raise
        except Exception as exc:
            done = ", ".join(out.written) or "none"
            raise ShapeError(f"seeding table {name} failed: {exc}; tables written: {done}") from exc
        if plan.sink == "sql":
            out.files.append(uri)
    return out


def _listed(names: list[str], limit: int = 4) -> str:
    more = f" and {len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def _sink(name: str, target: str, sinks: Mapping[str, Any] | None) -> Any:
    if sinks is not None and name in sinks:
        return sinks[name]
    from shape.plugins.host import PluginLoadError, default_host

    try:
        return default_host().get("shape.sinks", name)
    except (KeyError, PluginLoadError) as exc:
        hint = f"; install it with {_INSTALL[name]}" if name in _INSTALL else ""
        raise ShapeError(f"no {name} sink is installed for {_redacted(target)}{hint}") from exc


def _script_target(target: str) -> tuple[Path, str]:
    """``(directory, dialect)`` of ``sql://DIR[?dialect=tsql|postgres|mysql]``."""
    body, _, query = target[len(SCRIPT_SCHEME) :].partition("?")
    if not body:
        raise ShapeError("a script target is sql://DIR: give the directory")
    params = dict(parse_qsl(query, keep_blank_values=True))
    unknown = sorted(set(params) - {"dialect"})
    if unknown:
        raise ShapeError(f"unknown parameter {unknown[0]!r} in the sql:// target; use dialect=")
    dialect = params.get("dialect", "tsql")
    if dialect not in ("tsql", "postgres", "mysql"):
        raise ShapeError(f"unknown dialect {dialect!r}; the dialects are tsql, postgres, mysql")
    return Path(body), dialect


def _script_paths(directory: Path, names: list[str]) -> dict[str, Path]:
    width = max(2, len(str(len(names))))
    return {n: directory / f"{i:0{width}d}_{n}.sql" for i, n in enumerate(names, 1)}


def _script_options(mode: str, dialect: str, plan: SeedPlan) -> dict[str, Any]:
    return {
        "sql_dialect": dialect,
        "ddl": mode != "append",
        "drop": mode == "truncate",
        "header": [f"shape seed: seed {plan.seed}, scale {plan.scale}, mode {mode}"],
    }


def _existing(sink: Any, target: str, names: list[str], given: Mapping[str, Any]) -> list[str]:
    """The tables of ``names`` that exist at the database ``target`` (one connection, then closed).
    A sink says how to look through its ``exists_sql`` and connection hooks (the shape-databases
    sinks); the SQL Server sink through its connection helper."""
    if hasattr(sink, "exists_sql") and hasattr(sink, "connect_params"):
        return _existing_by_sql(sink, target, names, given)
    if getattr(sink, "name", "") == "sqlserver":
        return _existing_sqlserver(sink, target, names, given)
    raise ShapeError(
        f"the {getattr(sink, 'name', '?')} sink cannot say which tables exist, so mode create "
        "cannot be checked first: use --mode append or truncate"
    )


def _existing_by_sql(
    sink: Any, target: str, names: list[str], given: Mapping[str, Any]
) -> list[str]:
    plans = {name: sink.plan(target, name, dict(given)) for name in names}
    opener = getattr(sink, "_connect", None) or sink.default_connect  # the sink's test seam
    conn = opener(**sink.connect_params(plans[names[0]]))
    try:
        found = []
        for name, plan in plans.items():
            cur = conn.cursor()
            try:
                cur.execute(sink.exists_sql, (plan.schema_name, plan.table))
                if cur.fetchone() is not None:
                    found.append(name)
            finally:
                cur.close()
        return found
    finally:
        conn.close()


def _existing_sqlserver(
    sink: Any, target: str, names: list[str], given: Mapping[str, Any]
) -> list[str]:
    module = importlib.import_module(type(sink).__module__.rsplit(".", 1)[0] + ".sqldb")
    query, parts = sink._parse(target)
    keys = ("user", "password", "driver", "encrypt", "trust_server_certificate", "timeout", "auth")
    conn_opts = {k: query[k] for k in keys if k in query} | {
        k: given[k] for k in keys if k in given
    }
    credential = given.get("credential")
    conn_string = given.get("connection_string") or sink._connection_string(
        parts, conn_opts, credential
    )
    schema_name = given.get("schema_name") or query.get("schema", "dbo")
    db = module.SqlConnection(
        conn_string, credential, given.get("connection"), getattr(sink, "_connect", None), None
    )
    try:
        return [n for n in names if db.table_exists(schema_name, n)]
    finally:
        db.close()
