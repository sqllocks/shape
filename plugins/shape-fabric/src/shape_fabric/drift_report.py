"""The drift report template (``shape publish-report``): a profile history as star tables and a
Power BI semantic model.

Each profile is diffed with the one before it (the policy of ``shape diff``: the same thresholds,
column thresholds, ``--ignore``, ``--only`` and ``--policy``), and every change that passes becomes
one row of ``fact_drift_change``. The star around it:

``dim_run``
    one row per comparison: the later profile, the one before it, and the date of the later one.
``dim_column``
    every column of every profile (``table.column`` for a profile of several tables).
``dim_kind``
    every kind of change ``shape diff`` can report, with its severity and the threshold setting
    that governs it.
``dim_date``
    every day from the first run to the last.
``fact_drift_change``
    ``run_id``, ``column_id``, ``kind_id``, ``size`` (the diff's 0 to 1 score) and ``threshold``
    (the setting the change passed, for the kinds that have one).

``drift.bim`` is the model over these tables (written by
:class:`~shape_fabric.semantic_model.SemanticModelExporter`) and ``report.json`` (format
``shape-drift-report``, version 1) records the inputs, the thresholds and the row counts. See
``docs/DRIFT_REPORT.md``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from shape.generation.schema import GenSchema

from .known_answer import KnownAnswerError, check_document, dump_json, read_json
from .semantic_model import SemanticModelExporter, dax_column, dax_table

FORMAT_REPORT = "shape-drift-report"
REPORT_VERSION = 1
TABLE_FORMATS = ("parquet", "csv")

# The setting a kind of change has to pass (``shape.drift.engine.DEFAULT_THRESHOLDS``). Two names
# are the upper and the lower bound of a ratio: the upper one applies when the value rose. A kind
# that is not here has nothing to pass: it is reported whenever it happens.
THRESHOLD_KEYS: dict[str, tuple[str, ...]] = {
    "row_count_change": ("row_count_ratio_max", "row_count_ratio_min"),
    "null_rate_change": ("null_rate",),
    "cardinality_change": ("cardinality_ratio_max", "cardinality_ratio_min"),
    "uniqueness_change": ("uniqueness_rate",),
    "mean_shift": ("mean_shift_std",),
    "spread_change": ("std_ratio_max", "std_ratio_min"),
    "distribution_shift": ("ks_distance",),
    "category_shift": ("category_tvd",),
    "true_rate_change": ("true_rate",),
    "range_change": ("range_margin_std",),
    "length_change": ("length_ratio",),
    "outlier_rate_change": ("outlier_rate",),
    "hour_of_day_change": ("temporal_tvd",),
    "day_of_week_change": ("temporal_tvd",),
    "dependency_broken": ("dependency_confidence",),
    "placeholder_surge": ("placeholder_share",),
    "implausible_rate_change": ("implausible_rate",),
    "association_shift": ("association_shift",),
    "reference_match_change": ("reference_match_rate",),
    # W3-07: the univariate depth kinds (tail_change also needs alpha below tail_alpha_max)
    "zero_inflation_change": ("zero_share",),
    "heaping_change": ("heaping_ratio",),
    "benford_change": ("benford_class_steps",),
    "tail_change": ("tail_alpha_drop",),
    # W7-03: mixture and seasonality (also of the opt-in univariate depth)
    "mixture_change": ("mixture_weight",),
    "seasonality_change": ("seasonality_strength",),
    # W3-08: the multivariate kinds (reported only for profiles taken with --multivariate)
    "multivariate_outlier_rate_change": ("multivariate_outlier_rate",),
    "structure_change": ("structure_angle",),
    "cohort_shift": ("cohort_tvd",),
}
NO_THRESHOLD = (
    "measure_change",
    "role_change",
    "table_added",
    "table_removed",
    "column_added",
    "column_removed",
    "dtype_change",
    "pattern_change",
    "distribution_change",
    "new_categorical_values",
)

_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")

# the tables: (column, logical type); the Arrow types and the model's types follow from these
_TABLES: dict[str, list[tuple[str, str]]] = {
    "dim_run": [
        ("run_id", "integer"),
        ("run_label", "string"),
        ("baseline_label", "string"),
        ("profile", "string"),
        ("run_date", "date"),
        ("date_key", "integer"),
    ],
    "dim_column": [
        ("column_id", "integer"),
        ("column_key", "string"),
        ("table_name", "string"),
        ("column_name", "string"),
    ],
    "dim_kind": [
        ("kind_id", "integer"),
        ("kind", "string"),
        ("severity", "string"),
        ("threshold_key", "string"),
    ],
    "dim_date": [
        ("date_key", "integer"),
        ("date", "date"),
        ("year", "integer"),
        ("quarter", "integer"),
        ("month", "integer"),
        ("month_name", "string"),
        ("day", "integer"),
        ("day_of_week", "string"),
    ],
    "fact_drift_change": [
        ("run_id", "integer"),
        ("column_id", "integer"),
        ("kind_id", "integer"),
        ("size", "float"),
        ("threshold", "float"),
    ],
}
_PRIMARY_KEYS = {
    "dim_run": "run_id",
    "dim_column": "column_id",
    "dim_kind": "kind_id",
    "dim_date": "date_key",
}
_ARROW = {
    "integer": pa.int64(),
    "string": pa.string(),
    "date": pa.date32(),
    "float": pa.float64(),
}
_RELATIONSHIPS = (
    ("run_changes", "dim_run", "fact_drift_change", "run_id"),
    ("column_changes", "dim_column", "fact_drift_change", "column_id"),
    ("kind_changes", "dim_kind", "fact_drift_change", "kind_id"),
    ("date_runs", "dim_date", "dim_run", "date_key"),
)


def threshold_for(
    kind: str, record: Mapping[str, Any], thresholds: Mapping[str, Any]
) -> tuple[float | None, str | None]:
    """The threshold a change passed and the name of that setting; ``(None, None)`` for a kind
    that has none."""
    keys = THRESHOLD_KEYS.get(kind)
    if not keys:
        return None, None
    key = keys[0]
    if len(keys) == 2:
        before, after = record.get("baseline"), record.get("current")
        rose = (
            isinstance(before, (int, float))
            and isinstance(after, (int, float))
            and not isinstance(before, bool)
            and after > before
        )
        key = keys[0] if rose else keys[1]
    return float(thresholds[key]), key


# ---------------------------------------------------------------------------------------------
# the history


@dataclass
class Source:
    """One profile of the history."""

    label: str
    sha256: str
    date: dt.date | None
    load: Callable[[], Any]
    path: str | None = None
    content_id: str | None = None

    def document(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "path": self.path,
            "content_id": self.content_id,
            "sha256": self.sha256,
            "date": None if self.date is None else self.date.isoformat(),
        }


def _load_profile_file(path: Path, where: str) -> Any:
    import shape
    from shape.errors import ShapeError

    try:
        if path.read_bytes()[:2] == b"PK":
            return shape.load(path)
        from shape.cli.profiles import read_export

        return read_export(str(path))
    except (ShapeError, ValueError, KeyError, OSError) as exc:
        text = " ".join(str(exc).split())
        raise KnownAnswerError(f"{where} is not a Shape profile: {text}") from exc


def date_in_name(name: str) -> dt.date | None:
    """The first ``YYYY-MM-DD`` in ``name`` that is a date."""
    for m in _DATE_IN_NAME.finditer(name):
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
    return None


def file_sources(paths: Sequence[str]) -> list[Source]:
    out: list[Source] = []
    for text in paths:
        path = Path(text)
        if not path.is_file():
            raise KnownAnswerError(f"the profile {text} does not exist")
        data = path.read_bytes()
        from shape.registry.local import is_raw_profile

        if not is_raw_profile(data):
            raise KnownAnswerError(
                f"{text} is not a full profile (a safe profile holds too little to compare): the "
                "drift report diffs full profiles, so commit raw profiles (`.shape` files or "
                "`shape profile export` JSON; in a registry, `--allow-raw`)"
            )
        out.append(
            Source(
                label=path.stem,
                sha256=hashlib.sha256(data).hexdigest(),
                date=date_in_name(path.stem),
                load=lambda path=path, text=text: _load_profile_file(path, text),
                path=text,
            )
        )
    return out


def parse_date(text: str, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise KnownAnswerError(f"{what} {text!r}: use YYYY-MM-DD") from None


def registry_sources(root: str, name: str, since: str | None) -> list[Source]:
    """The commits of ``name`` in a ``shape registry``, oldest first. With ``since``, the commits
    dated that day or later and the one before them (the first run's baseline)."""
    from shape.registry import LocalRegistry
    from shape.registry.local import RegistryError, is_raw_profile

    base = Path(root)
    if not (base / "logs").is_dir():
        raise KnownAnswerError(f"{root} is not a registry (no logs folder): see `shape registry`")
    registry = LocalRegistry(base)
    try:
        entries = registry.log(name)
    except RegistryError as exc:
        raise KnownAnswerError(str(exc)) from exc
    if not entries:
        raise KnownAnswerError(f"the registry {root} has no commits named {name!r}")
    cutoff = parse_date(since, "--since") if since else None
    dated: list[tuple[dict[str, Any], dt.date]] = []
    for entry in entries:
        meta = entry.get("metadata") or {}
        when: dt.date | None = None
        if isinstance(meta.get("business_date"), str):
            when = parse_date(meta["business_date"], f"the business_date of {name}")
        if when is None:
            stamp = entry.get("created_at")
            when = dt.datetime.fromtimestamp(float(stamp), dt.UTC).date() if stamp else None
        if when is None:
            raise KnownAnswerError(f"a commit of {name!r} has no date")
        dated.append((entry, when))
    if cutoff is not None:
        first = next((i for i, (_, d) in enumerate(dated) if d >= cutoff), None)
        dated = [] if first is None else dated[max(first - 1, 0) :]
    out: list[Source] = []
    for entry, when in dated:
        cid = str(entry["content_id"])
        data = registry.checkout(name, cid)
        if not is_raw_profile(data):
            raise KnownAnswerError(
                f"{name}@{cid[:12]} in the registry is not a full profile (a safe profile holds "
                "too little to compare): the drift report diffs full profiles, so commit them "
                f"with `shape registry {root} commit {name} PROFILE --allow-raw`"
            )
        out.append(
            Source(
                label=cid[:12],
                sha256=cid,
                date=when,
                load=lambda data=data, cid=cid: _load_registry_profile(data, f"{name}@{cid[:12]}"),
                content_id=cid,
            )
        )
    return out


def _load_registry_profile(data: bytes, where: str) -> Any:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile"
        path.write_bytes(data)
        return _load_profile_file(path, where)


# ---------------------------------------------------------------------------------------------
# the tables


@dataclass
class _Column:
    key: str
    table: str | None
    column: str | None


@dataclass
class Tables:
    arrow: dict[str, pa.Table]
    runs: int
    changes: int


def _policy_document(policy: Any) -> dict[str, Any]:
    return {
        "thresholds": dict(policy.thresholds),
        "columns": {k: dict(v) for k, v in policy.columns.items()},
        "ignore": list(policy.ignore),
        "only": list(policy.only),
    }


def _arrow_table(name: str, rows: Mapping[str, list[Any]]) -> pa.Table:
    schema = pa.schema([(c, _ARROW[t]) for c, t in _TABLES[name]])
    return pa.table({c: pa.array(rows[c], type=schema.field(c).type) for c, _ in _TABLES[name]})


def _columns_of(profile: Any) -> list[_Column]:
    from shape.drift.engine import tables_of

    tables, dataset = tables_of(profile)
    out: list[_Column] = []
    for tname, view in tables.items():
        for cname in view.columns:
            if cname == "_shape_event_time":
                continue
            out.append(
                _Column(f"{tname}.{cname}" if dataset else cname, tname if dataset else None, cname)
            )
    return out


def build_tables(sources: Sequence[Source], options: Mapping[str, Any]) -> tuple[Tables, Any]:
    """Diff every profile with the one before it and lay the changes out as the star tables."""
    from shape.drift.engine import KIND_SEVERITY, diff_records, resolve_policy

    policy = resolve_policy(
        options.get("thresholds"),
        ignore_columns=options.get("ignore_columns"),
        column_thresholds=options.get("column_thresholds"),
        only_columns=options.get("only_columns"),
        policy=options.get("policy"),
    )
    profiles = [s.load() for s in sources]
    columns: dict[str, _Column] = {}
    for profile in profiles:
        for col in _columns_of(profile):
            columns.setdefault(col.key, col)
    found: list[tuple[int, str, str, float, float | None]] = []  # run, column key, kind, size, th
    severities = dict(KIND_SEVERITY)
    threshold_keys: dict[str, str] = {}
    for run, (before, after) in enumerate(zip(profiles, profiles[1:], strict=False), 1):
        for scope, col, record in diff_records(before, after, policy):
            key = record["column"] or scope or "(table)"
            columns.setdefault(key, _Column(key, scope, col))
            kind = record["kind"]
            severities.setdefault(kind, record["severity"])
            value, setting = threshold_for(kind, record, policy.for_column(scope, col))
            if setting:
                threshold_keys[kind] = setting
            found.append((run, key, kind, float(record["score"]), value))
    kind_names = sorted(severities)
    kind_id = {k: i for i, k in enumerate(kind_names, 1)}
    column_keys = sorted(columns)
    column_id = {k: i for i, k in enumerate(column_keys, 1)}
    run_dates = [s.date for s in sources[1:]]
    run_rows: dict[str, list[Any]] = {c: [] for c, _ in _TABLES["dim_run"]}
    for n, (src, prev) in enumerate(zip(sources[1:], sources[:-1], strict=True), 1):
        run_rows["run_id"].append(n)
        run_rows["run_label"].append(src.label)
        run_rows["baseline_label"].append(prev.label)
        run_rows["profile"].append(src.path or src.content_id)
        run_rows["run_date"].append(src.date)
        run_rows["date_key"].append(None if src.date is None else _date_key(src.date))
    column_rows = {
        "column_id": [column_id[k] for k in column_keys],
        "column_key": column_keys,
        "table_name": [columns[k].table for k in column_keys],
        "column_name": [columns[k].column for k in column_keys],
    }
    kind_rows = {
        "kind_id": [kind_id[k] for k in kind_names],
        "kind": kind_names,
        "severity": [severities[k] for k in kind_names],
        "threshold_key": [
            "|".join(THRESHOLD_KEYS[k]) if k in THRESHOLD_KEYS else None for k in kind_names
        ],
    }
    fact_rows = {
        "run_id": [f[0] for f in found],
        "column_id": [column_id[f[1]] for f in found],
        "kind_id": [kind_id[f[2]] for f in found],
        "size": [f[3] for f in found],
        "threshold": [f[4] for f in found],
    }
    known = [d for d in run_dates if d is not None]
    days: list[dt.date] = []
    if known:
        day = min(known)
        while day <= max(known):
            days.append(day)
            day += dt.timedelta(days=1)
    date_rows = {
        "date_key": [_date_key(d) for d in days],
        "date": days,
        "year": [d.year for d in days],
        "quarter": [(d.month - 1) // 3 + 1 for d in days],
        "month": [d.month for d in days],
        "month_name": [_MONTHS[d.month - 1] for d in days],
        "day": [d.day for d in days],
        "day_of_week": [_WEEKDAYS[d.weekday()] for d in days],
    }
    tables = {
        "dim_run": _arrow_table("dim_run", run_rows),
        "dim_column": _arrow_table("dim_column", column_rows),
        "dim_kind": _arrow_table("dim_kind", kind_rows),
        "dim_date": _arrow_table("dim_date", date_rows),
        "fact_drift_change": _arrow_table("fact_drift_change", fact_rows),
    }
    return Tables(tables, len(sources) - 1, len(found)), policy


_MONTHS = (
    "January February March April May June July August September October November December"
).split()
_WEEKDAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()


def _date_key(day: dt.date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


# ---------------------------------------------------------------------------------------------
# the model


def model_schema() -> GenSchema:
    """The star as a generation schema, which is what the exporter writes a model from."""
    tables: dict[str, Any] = {}
    for name, columns in _TABLES.items():
        cols: dict[str, Any] = {}
        for cname, ctype in columns:
            generator: dict[str, Any] = {"strategy": "native"}
            for _rel, parent, child, key in _RELATIONSHIPS:
                if child == name and key == cname:
                    generator = {"strategy": "foreign_key", "ref": f"{parent}.{key}"}
            cols[cname] = {"name": cname, "type": ctype, "generator": generator}
        table: dict[str, Any] = {"name": name, "columns": cols}
        if name in _PRIMARY_KEYS:
            table["primary_key"] = [_PRIMARY_KEYS[name]]
        tables[name] = table
    doc = {
        "schema_version": 1,
        "model": {"name": "drift", "domain": "drift", "seed": 0, "schema_mode": "star"},
        "tables": tables,
        "relationships": [
            {
                "name": rel_name,
                "parent": parent,
                "child": child,
                "parent_columns": [key],
                "child_columns": [key],
                "type": "one_to_many",
            }
            for rel_name, parent, child, key in _RELATIONSHIPS
        ],
        "generation": {"scale": "small", "scales": {"small": {n: 1 for n in tables}}},
    }
    return GenSchema.from_dict(doc)


def model_measures() -> dict[str, list[dict[str, str]]]:
    """The DAX measures of the report, on ``fact_drift_change``."""
    fact = "fact_drift_change"

    def ref(name: str) -> str:
        return f"{dax_table(fact)}[{name}]"

    def entry(name: str, expression: str, fmt: str) -> dict[str, str]:
        return {"name": name, "expression": expression, "formatString": fmt}

    return {
        fact: [
            entry("Changes", f"COUNTROWS({dax_table(fact)})", "#,0"),
            entry("Runs", f"COUNTROWS({dax_table('dim_run')})", "#,0"),
            entry("Changes per Run", f"DIVIDE({ref('Changes')}, {ref('Runs')})", "#,0.00"),
            entry("Runs with Change", f"DISTINCTCOUNT({dax_column(fact, 'run_id')})", "#,0"),
            entry(
                "Share of Runs with Change",
                f"DIVIDE({ref('Runs with Change')}, {ref('Runs')})",
                "0.0%",
            ),
            entry("Columns with Change", f"DISTINCTCOUNT({dax_column(fact, 'column_id')})", "#,0"),
            entry("Kinds with Change", f"DISTINCTCOUNT({dax_column(fact, 'kind_id')})", "#,0"),
            entry(
                "Share of Changes by Kind",
                f"DIVIDE({ref('Changes')}, "
                f"CALCULATE({ref('Changes')}, ALL({dax_table('dim_kind')})))",
                "0.0%",
            ),
            entry(
                "Share of Changes by Column",
                f"DIVIDE({ref('Changes')}, "
                f"CALCULATE({ref('Changes')}, ALL({dax_table('dim_column')})))",
                "0.0%",
            ),
            entry("Average Change Size", f"AVERAGE({dax_column(fact, 'size')})", "0.000"),
            entry("Largest Change Size", f"MAX({dax_column(fact, 'size')})", "0.000"),
        ]
    }


# ---------------------------------------------------------------------------------------------
# writing


@dataclass
class Published:
    directory: Path
    runs: int
    changes: int
    rows: dict[str, int] = field(default_factory=dict)


def publish(
    sources: Sequence[Source],
    directory: str | Path,
    *,
    fmt: str = "parquet",
    options: Mapping[str, Any] | None = None,
    source_kind: str = "files",
    registry: str | None = None,
    since: str | None = None,
) -> Published:
    """Diff the history and write ``data/``, ``drift.bim`` and ``report.json`` into ``directory``.
    Nothing is written unless every profile loads and every diff runs."""
    if fmt not in TABLE_FORMATS:
        raise KnownAnswerError(f"unknown format {fmt!r}; choose one of {', '.join(TABLE_FORMATS)}")
    if len(sources) < 2:
        raise KnownAnswerError(
            f"a drift report compares profiles with the one before: it needs at least two, "
            f"got {len(sources)}"
        )
    built, policy = build_tables(sources, options or {})
    out = Path(directory)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for name, table in built.arrow.items():
        target = out / "data" / f"{name}.{fmt}"
        if fmt == "parquet":
            pq.write_table(table, target)
        else:
            pacsv.write_csv(table, target)
    exporter = SemanticModelExporter()
    tom = exporter.to_dict(model_schema(), measures=model_measures())
    (out / "drift.bim").write_text(dump_json(tom), encoding="utf-8")
    rows = {name: table.num_rows for name, table in sorted(built.arrow.items())}
    doc = {
        "format": FORMAT_REPORT,
        "version": REPORT_VERSION,
        "source": {"kind": source_kind, "registry": registry, "since": since},
        "inputs": [s.document() for s in sources],
        "thresholds": _policy_document(policy),
        "format_of_tables": fmt,
        "tables": rows,
        "runs": built.runs,
        "changes": built.changes,
    }
    (out / "report.json").write_text(dump_json(doc), encoding="utf-8")
    return Published(out, built.runs, built.changes, rows)


def load_report(path: str | Path) -> dict[str, Any]:
    """A ``report.json``, read strictly: its format, its integer version (a newer one is refused)
    and the keys this version writes."""
    doc = read_json(path, "drift report")
    check_document(doc, FORMAT_REPORT, REPORT_VERSION, "drift report", path)

    def bad(what: str) -> KnownAnswerError:
        return KnownAnswerError(f"{path}: {what}")

    source = doc.get("source")
    if not isinstance(source, dict) or source.get("kind") not in ("files", "registry"):
        raise bad("'source' must say whether the history is 'files' or a 'registry'")
    inputs = doc.get("inputs")
    if not isinstance(inputs, list) or not all(
        isinstance(i, dict) and isinstance(i.get("sha256"), str) for i in inputs
    ):
        raise bad("'inputs' must list profiles, each with a 'sha256'")
    thresholds = doc.get("thresholds")
    if not isinstance(thresholds, dict) or not {"thresholds", "columns", "ignore", "only"} <= set(
        thresholds
    ):
        raise bad("'thresholds' must hold thresholds, columns, ignore and only")
    tables = doc.get("tables")
    if not isinstance(tables, dict) or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in tables.values()
    ):
        raise bad("'tables' must map each table to its row count")
    for key in ("runs", "changes"):
        if isinstance(doc.get(key), bool) or not isinstance(doc.get(key), int):
            raise bad(f"{key!r} must be an integer")
    if doc.get("format_of_tables") not in TABLE_FORMATS:
        raise bad(f"'format_of_tables' must be one of {', '.join(TABLE_FORMATS)}")
    return doc
