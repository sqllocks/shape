"""Reconciliation of a source and a target (W3-10): row counts per table and partition, and
aggregates per key or group, compared within tolerances.

:func:`reconcile` compares two table-shaped inputs, each read through the source layer
(:func:`shape.profile.reference.sources.load_table`: files, globs, folders, Delta tables, Arrow
tables, DataFrames, row dicts). :class:`ReconciliationGate` runs the ``reconcile`` rules of a
verify configuration, and the contract's ``reconcile`` rules use the same function.

A difference ``d`` between a source value ``s`` and a target value ``t`` is within tolerance when
``abs(d) <= abs + rel * max(abs(s), abs(t))``; both settings default to ``0`` (an exact match).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .gates import GateResult, ValidationContext, ValidationGate

AGGREGATES = ("sum", "mean", "min", "max", "count", "count_distinct")
_RULE_KEYS = (
    "name",
    "source",
    "target",
    "partition_by",
    "key",
    "aggregates",
    "tolerance",
    "count_tolerance",
)
_AGG_KEYS = ("column", "agg", "target_column", "tolerance")
_SAMPLES = 20
_COUNT = "__shape_rows"


def _only_keys(obj: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ValueError(f'{where}: unknown key "{unknown[0]}" (known: {", ".join(allowed)})')


def _require(obj: Mapping[str, Any], key: str, where: str) -> None:
    if key not in obj:
        raise ValueError(f'{where}: missing required key "{key}"')


def _names(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(c, str) and c for c in value)


def _validate_side(value: Any, which: str, where: str) -> None:
    ok = isinstance(value, str) and bool(value)
    if isinstance(value, Mapping):
        ok = set(value) == {"table"} and isinstance(value["table"], str) and bool(value["table"])
    if not ok:
        raise ValueError(f'{where}: "{which}" must be a path or {{"table": name}}, not {value!r}')


def _validate_tolerance(value: Any, key: str, where: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f'{where}: "{key}" must be an object of "abs" and/or "rel"')
    _only_keys(value, ("abs", "rel"), f"{where}.{key}")
    for k, v in value.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or v != v:
            raise ValueError(f'{where}.{key}: "{k}" must be a non-negative number, not {v!r}')


def validate_reconcile_rules(rules: Any) -> None:
    """Raise :class:`ValueError`, naming the rule and key, unless ``rules`` is a usable list."""
    if not isinstance(rules, list):
        raise ValueError('"reconcile" must be a list of rules')
    for i, rule in enumerate(rules):
        where = f"reconcile[{i}]"
        if not isinstance(rule, Mapping):
            raise ValueError(f"{where}: must be an object, not {rule!r}")
        _require(rule, "source", where)
        _require(rule, "target", where)
        _only_keys(rule, _RULE_KEYS, where)
        _validate_side(rule["source"], "source", where)
        _validate_side(rule["target"], "target", where)
        if "name" in rule and not (isinstance(rule["name"], str) and rule["name"]):
            raise ValueError(f'{where}: "name" must be text')
        if "partition_by" in rule and not _names(rule["partition_by"]):
            raise ValueError(f'{where}: "partition_by" must be a non-empty list of column names')
        if "key" in rule and not _names(rule["key"]):
            raise ValueError(f'{where}: "key" must be a non-empty list of column names')
        for tol in ("tolerance", "count_tolerance"):
            if tol in rule:
                _validate_tolerance(rule[tol], tol, where)
        if "aggregates" in rule:
            _validate_aggregates(rule["aggregates"], f"{where}.aggregates")


def _validate_aggregates(aggs: Any, where: str) -> None:
    if not isinstance(aggs, list):
        raise ValueError(f'{where}: "aggregates" must be a list of {{column, agg}} objects')
    for j, agg in enumerate(aggs):
        w = f"{where}[{j}]"
        if not isinstance(agg, Mapping):
            raise ValueError(f"{w}: must be an object, not {agg!r}")
        _require(agg, "column", w)
        _require(agg, "agg", w)
        _only_keys(agg, _AGG_KEYS, w)
        for key in ("column", "target_column"):
            if key in agg and not (isinstance(agg[key], str) and agg[key]):
                raise ValueError(f'{w}: "{key}" must be a column name')
        if agg["agg"] not in AGGREGATES:
            raise ValueError(
                f'{w}: "agg" must be one of {", ".join(AGGREGATES)}, not {agg["agg"]!r}'
            )
        if "tolerance" in agg:
            _validate_tolerance(agg["tolerance"], "tolerance", w)


@dataclass
class ReconcileResult:
    """Outcome of :func:`reconcile`. ``findings`` are dicts with ``rule`` (``reconcile.count``,
    ``reconcile.partition``, ``reconcile.key``, ``reconcile.key_count``, ``reconcile.aggregate``,
    ``reconcile.column_exists``), ``severity``, ``table`` (the reconciliation's name),
    ``column``, ``message``, ``expected`` (the source value) and ``observed``."""

    passed: bool
    findings: list[dict[str, Any]] = field(default_factory=list)
    source_rows: int = 0
    target_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "source_rows": self.source_rows,
            "target_rows": self.target_rows,
            "findings": [dict(f) for f in self.findings],
        }


def _finding(
    name: str, rule: str, column: str | None, message: str, expected: Any, observed: Any
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": "error",
        "table": name,
        "column": column,
        "message": f"{name}: {message}",
        "expected": expected,
        "observed": observed,
    }


_NAN = float("nan")  # one object, so a NaN key finds the NaN key of the other side in a dict


def _canon(v: Any) -> Any:
    return _NAN if isinstance(v, float) and v != v else v


def _number(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _compare(s: Any, t: Any, tol: Mapping[str, Any]) -> tuple[bool, Any, float]:
    """``(within tolerance, t - s or None, allowed difference)``."""
    if s is None and t is None:
        return True, 0, 0.0
    if not (_number(s) and _number(t)):
        return s == t, None, 0.0
    if isinstance(s, Decimal) and isinstance(t, Decimal):
        diff: Any = t - s
        big = max(abs(s), abs(t))
        allowed = float(tol.get("abs", 0)) + float(tol.get("rel", 0)) * float(big)
        return abs(float(diff)) <= allowed, diff, allowed
    sf, tf = float(s), float(t)
    if sf != sf and tf != tf:
        return True, 0, 0.0  # NaN reconciles with NaN, as null does with null
    allowed = float(tol.get("abs", 0)) + float(tol.get("rel", 0)) * max(abs(sf), abs(tf))
    diff = tf - sf
    if isinstance(s, int) and isinstance(t, int):
        diff = t - s
    return abs(diff) <= allowed, diff, allowed


def _resolve(side: Any, tables: Mapping[str, pa.Table] | None, which: str) -> tuple[pa.Table, str]:
    from shape.profile.reference.sources import load_table

    if isinstance(side, Mapping):
        name = side["table"]
        if tables is None or name not in tables:
            loaded = sorted(tables or ())
            raise ValueError(
                f'the {which} table "{name}" is not among the loaded tables '
                f"({', '.join(loaded) or 'none'})"
            )
        return tables[name], name
    label = str(side) if isinstance(side, (str, bytes)) or hasattr(side, "__fspath__") else which
    return load_table(side)[1], label


def _group(
    table: pa.Table, columns: list[str], aggs: list[tuple[str, str]]
) -> dict[tuple[Any, ...], dict[Any, Any]]:
    """``{key values: {"__n": rows, (column, agg): value}}`` for a grouped table; the whole
    table is one group with the key ``()`` when ``columns`` is empty."""
    n = table.num_rows
    if not columns:
        stats: dict[Any, Any] = {_COUNT: n}
        for column, agg in aggs:
            stats[(column, agg)] = _scalar(table.column(column), agg)
        return {(): stats}
    data = table.select([*columns, *dict.fromkeys(c for c, _ in aggs)])
    data = data.append_column(_COUNT, pa.array([1] * n, pa.int64()))
    spec = [(_COUNT, "sum")] + [(c, a) for c, a in dict.fromkeys(aggs)]
    out = data.group_by(columns, use_threads=False).aggregate(spec)
    key_cols = [out.column(c).to_pylist() for c in columns]
    value_cols = {(c, a): out.column(f"{c}_{a}").to_pylist() for c, a in dict.fromkeys(aggs)}
    counts = out.column(f"{_COUNT}_sum").to_pylist()
    result: dict[tuple[Any, ...], dict[Any, Any]] = {}
    for i, key in enumerate(zip(*([_canon(v) for v in col] for col in key_cols), strict=True)):
        row: dict[Any, Any] = {_COUNT: counts[i]}
        for pair, values in value_cols.items():
            row[pair] = values[i]
        result[key] = row
    return result


def _scalar(col: pa.ChunkedArray, agg: str) -> Any:
    if agg == "sum":
        return pc.sum(col).as_py()
    if agg == "mean":
        return pc.mean(col).as_py()
    if agg == "min":
        return pc.min_max(col)["min"].as_py()
    if agg == "max":
        return pc.min_max(col)["max"].as_py()
    if agg == "count":
        return int(pc.count(col).as_py())
    return int(pc.count_distinct(col).as_py())


def _label(columns: list[str], key: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(columns, key, strict=True))


def _count_findings(
    name: str,
    rule: str,
    columns: list[str],
    src: Mapping[tuple[Any, ...], Mapping[Any, Any]],
    tgt: Mapping[tuple[Any, ...], Mapping[Any, Any]],
    tol: Mapping[str, Any],
    noun: str,
    label_key: str,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    differing = 0
    for key in [*src, *(k for k in tgt if k not in src)]:
        s = src[key][_COUNT] if key in src else 0
        t = tgt[key][_COUNT] if key in tgt else 0
        ok, diff, allowed = _compare(s, t, tol)
        if ok:
            continue
        differing += 1
        if len(samples) < _SAMPLES:
            sample = {
                label_key: _label(columns, key),
                "source": s,
                "target": t,
                "difference": diff,
                "allowed": allowed,
            }
            if key not in src or key not in tgt:
                sample["only_in"] = "target" if key not in src else "source"
            samples.append(sample)
    if not differing:
        return []
    compared = len(set(src) | set(tgt))
    return [
        _finding(
            name,
            rule,
            None,
            f"row counts differ in {differing:,} of {compared:,} {noun} ({', '.join(columns)})",
            None,
            {"compared": compared, "differing": differing, "samples": samples},
        )
    ]


def reconcile(
    source: Any,
    target: Any,
    *,
    partition_by: list[str] | None = None,
    key: list[str] | None = None,
    aggregates: list[Mapping[str, Any]] | None = None,
    tolerance: Mapping[str, Any] | None = None,
    count_tolerance: Mapping[str, Any] | None = None,
    name: str | None = None,
    tables: Mapping[str, pa.Table] | None = None,
) -> ReconcileResult:
    """Compare ``source`` and ``target``.

    Always: the row counts. ``partition_by``: row counts per value combination of those columns.
    ``key``: row counts per key, keys on one side only, and each aggregate per key; without it
    the aggregates cover the whole table. ``aggregates`` are ``{"column", "agg", "target_column"?,
    "tolerance"?}`` with ``agg`` one of ``sum``, ``mean``, ``min``, ``max``, ``count`` (non-null
    values) and ``count_distinct``. ``tolerance`` (``{"abs", "rel"}``) is the default for counts
    and aggregates, ``count_tolerance`` overrides it for counts. A side is anything
    :func:`~shape.profile.reference.sources.load_table` reads, or ``{"table": name}`` to take a
    table from ``tables``. Raises the source layer's errors for a side that cannot be read.
    """
    rule: dict[str, Any] = {"source": source, "target": target}
    for k, v in (
        ("partition_by", partition_by),
        ("key", key),
        ("aggregates", list(aggregates) if aggregates is not None else None),
        ("tolerance", tolerance),
        ("count_tolerance", count_tolerance),
    ):
        if v is not None:
            rule[k] = v
    validate_reconcile_rules([{**rule, "source": "-", "target": "-"}])
    src, src_label = _resolve(source, tables, "source")
    tgt, tgt_label = _resolve(target, tables, "target")
    return _reconcile_tables(src, tgt, rule, name or f"{src_label} vs {tgt_label}")


def _reconcile_tables(
    src: pa.Table, tgt: pa.Table, rule: Mapping[str, Any], name: str
) -> ReconcileResult:
    default_tol = rule.get("tolerance", {})
    count_tol = rule.get("count_tolerance", default_tol)
    partition = list(rule.get("partition_by", []))
    key = list(rule.get("key", []))
    aggs: list[dict[str, Any]] = [dict(a) for a in rule.get("aggregates", [])]
    findings: list[dict[str, Any]] = []

    def missing(side: pa.Table, which: str, columns: list[str]) -> bool:
        bad = False
        for c in columns:
            if c not in side.column_names:
                findings.append(
                    _finding(
                        name,
                        "reconcile.column_exists",
                        c,
                        f"column {c} is missing from the {which}",
                        "present",
                        {"side": which, "column": c},
                    )
                )
                bad = True
        return bad

    bad_part = missing(src, "source", partition)
    bad_part |= missing(tgt, "target", partition)
    bad_key = missing(src, "source", key)
    bad_key |= missing(tgt, "target", key)
    good_aggs = []
    for a in aggs:
        a.setdefault("target_column", a["column"])
        bad_a = missing(src, "source", [a["column"]])
        bad_a |= missing(tgt, "target", [a["target_column"]])
        if not bad_a:
            good_aggs.append(a)

    ok, diff, allowed = _compare(src.num_rows, tgt.num_rows, count_tol)
    if not ok:
        findings.append(
            _finding(
                name,
                "reconcile.count",
                None,
                f"row counts differ: source {src.num_rows:,}, target {tgt.num_rows:,}",
                src.num_rows,
                {"target": tgt.num_rows, "difference": diff, "allowed": allowed},
            )
        )

    if partition and not bad_part:
        findings.extend(
            _count_findings(
                name,
                "reconcile.partition",
                partition,
                _group(src, partition, []),
                _group(tgt, partition, []),
                count_tol,
                "partitions",
                "partition",
            )
        )

    src_pairs = [(a["column"], a["agg"]) for a in good_aggs]
    tgt_pairs = [(a["target_column"], a["agg"]) for a in good_aggs]
    by = [] if bad_key else key
    s_stats = _group(src, by, src_pairs)
    t_stats = _group(tgt, by, tgt_pairs)

    if by:
        only_s = [k for k in s_stats if k not in t_stats]
        only_t = [k for k in t_stats if k not in s_stats]
        if only_s or only_t:
            samples = [{"key": _label(by, k), "only_in": "source"} for k in only_s[:_SAMPLES]] + [
                {"key": _label(by, k), "only_in": "target"}
                for k in only_t[: _SAMPLES - len(only_s[:_SAMPLES])]
            ]
            findings.append(
                _finding(
                    name,
                    "reconcile.key",
                    None,
                    f"keys ({', '.join(by)}) on one side only: {len(only_s):,} in the source, "
                    f"{len(only_t):,} in the target",
                    None,
                    {
                        "only_in_source": len(only_s),
                        "only_in_target": len(only_t),
                        "samples": samples,
                    },
                )
            )
        both = [k for k in s_stats if k in t_stats]
        findings.extend(
            _count_findings(
                name,
                "reconcile.key_count",
                by,
                {k: s_stats[k] for k in both},
                {k: t_stats[k] for k in both},
                count_tol,
                "keys",
                "key",
            )
        )
    else:
        both = [()]

    for a, sp, tp in zip(good_aggs, src_pairs, tgt_pairs, strict=True):
        tol = a.get("tolerance", default_tol)
        samples = []
        differing = 0
        for k in both:
            s, t = s_stats[k][sp], t_stats[k][tp]
            ok, diff, allowed = _compare(s, t, tol)
            if ok:
                continue
            differing += 1
            if len(samples) < _SAMPLES:
                sample = {"source": s, "target": t, "difference": diff, "allowed": allowed}
                samples.append({"key": _label(by, k), **sample} if by else sample)
        if not differing:
            continue
        what = f"{a['agg']}({a['column']})"
        if by:
            findings.append(
                _finding(
                    name,
                    "reconcile.aggregate",
                    a["column"],
                    f"{what} differs for {differing:,} of {len(both):,} keys ({', '.join(by)})",
                    {"agg": a["agg"], "per": by},
                    {"compared": len(both), "differing": differing, "samples": samples},
                )
            )
        else:
            only = samples[0]
            findings.append(
                _finding(
                    name,
                    "reconcile.aggregate",
                    a["column"],
                    f"{what} differs: source {only['source']}, target {only['target']}",
                    only["source"],
                    {
                        "agg": a["agg"],
                        "target": only["target"],
                        "difference": only["difference"],
                        "allowed": only["allowed"],
                    },
                )
            )
    return ReconcileResult(not findings, findings, src.num_rows, tgt.num_rows)


class ReconciliationGate(ValidationGate):
    """Runs the ``config["reconcile"]`` rules; every difference beyond tolerance is an error. A
    side given as ``{"table": name}`` is a table that was loaded, any other side is read through
    the source layer."""

    name = "reconciliation"

    def check(self, context: ValidationContext) -> GateResult:
        rules = context.config.get("reconcile") or []
        validate_reconcile_rules(list(rules))
        errors: list[str] = []
        findings: list[dict[str, Any]] = []
        for rule in rules:
            label = (
                rule.get("name")
                or f"{_side_label(rule['source'])} vs {_side_label(rule['target'])}"
            )
            try:
                src, _ = _resolve(rule["source"], context.tables, "source")
                tgt, _ = _resolve(rule["target"], context.tables, "target")
            except Exception as exc:  # noqa: BLE001 - any unreadable side is a finding
                f = _finding(
                    label,
                    "reconcile.unreadable",
                    None,
                    f"cannot read the sides: {exc}",
                    "readable",
                    str(exc),
                )
                findings.append(f)
                errors.append(f["message"])
                continue
            result = _reconcile_tables(src, tgt, rule, label)
            findings.extend(result.findings)
            errors.extend(f["message"] for f in result.findings)
        return GateResult(
            self.name, not errors, errors, [], {"rules": len(rules), "findings": findings}
        )


def _side_label(side: Any) -> str:
    return side["table"] if isinstance(side, Mapping) else str(side)


__all__ = [
    "AGGREGATES",
    "ReconcileResult",
    "ReconciliationGate",
    "reconcile",
    "validate_reconcile_rules",
]
