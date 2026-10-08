"""Time-series quality checks: gaps in a regular series, stuck values and daylight-saving
transitions (W3-10).

A rule names a table and its time column, and any of three checks::

    {"table": "readings", "time": "ts", "by": ["sensor"], "every": "1h",
     "gaps":  {"max_missing": 0},
     "stuck": {"column": "value", "max_run": 5},
     "dst":   {"time_zone": "Europe/Berlin", "fall_back": "repeat"}}

:func:`check_timeseries` returns findings (dicts with ``rule``, ``severity``, ``table``,
``column``, ``message``, ``expected`` and ``observed``); :class:`TimeSeriesGate` is the gate that
``shape verify --config`` runs, and the contract's ``timeseries`` rules use the same function.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .gates import GateResult, ValidationContext, ValidationGate

_UNITS = {
    "us": 1,
    "ms": 1_000,
    "s": 1_000_000,
    "min": 60_000_000,
    "h": 3_600_000_000,
    "d": 86_400_000_000,
    "w": 7 * 86_400_000_000,
}
_EVERY = re.compile(r"([0-9]+)(us|ms|s|min|h|d|w)")
_RULE_KEYS = ("table", "time", "by", "every", "gaps", "stuck", "dst")
_CHECK_KEYS = ("gaps", "stuck", "dst")
_SAMPLES = 20
_EPOCH = datetime(1970, 1, 1)
_MAX_SCAN_DAYS = 366 * 150


def parse_every(value: Any) -> int:
    """An interval such as ``"15min"`` in microseconds. Units: ``us``, ``ms``, ``s``, ``min``,
    ``h``, ``d``, ``w``; a calendar month has no fixed length and is not accepted."""
    m = _EVERY.fullmatch(value) if isinstance(value, str) else None
    if m is None or int(m.group(1)) == 0:
        raise ValueError(
            "every must be a positive whole number and a unit (us, ms, s, min, h, d, w), "
            f'like "15min": got {value!r}'
        )
    return int(m.group(1)) * _UNITS[m.group(2)]


def _zone(name: Any, where: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f'{where}: unknown time zone "{name}"') from exc


def _require(rule: Mapping[str, Any], key: str, where: str) -> None:
    if key not in rule:
        raise ValueError(f'{where}: missing required key "{key}"')


def _only_keys(obj: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ValueError(f'{where}: unknown key "{unknown[0]}" (known: {", ".join(allowed)})')


def validate_timeseries_rules(rules: Any) -> None:
    """Raise :class:`ValueError`, naming the rule and key, unless ``rules`` is a usable list."""
    if not isinstance(rules, list):
        raise ValueError('"timeseries" must be a list of rules')
    for i, rule in enumerate(rules):
        where = f"timeseries[{i}]"
        if not isinstance(rule, Mapping):
            raise ValueError(f"{where}: must be an object, not {rule!r}")
        _require(rule, "table", where)
        _require(rule, "time", where)
        _only_keys(rule, _RULE_KEYS, where)
        for key in ("table", "time"):
            if not isinstance(rule[key], str) or not rule[key]:
                raise ValueError(f'{where}: "{key}" must be a name')
        by = rule.get("by", [])
        if not isinstance(by, list) or not all(isinstance(c, str) and c for c in by):
            raise ValueError(f'{where}: "by" must be a list of column names')
        if not any(k in rule for k in _CHECK_KEYS):
            raise ValueError(f"{where}: needs at least one of {', '.join(_CHECK_KEYS)}")
        if "gaps" in rule or "dst" in rule:
            _require(rule, "every", where)
        if "every" in rule:
            try:
                parse_every(rule["every"])
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
        if "gaps" in rule:
            _validate_gaps(rule["gaps"], f"{where}.gaps")
        if "stuck" in rule:
            _validate_stuck(rule["stuck"], f"{where}.stuck")
        if "dst" in rule:
            _validate_dst(rule["dst"], f"{where}.dst")


def _validate_gaps(gaps: Any, where: str) -> None:
    if not isinstance(gaps, Mapping):
        raise ValueError(f"{where}: must be an object")
    _only_keys(gaps, ("max_missing",), where)
    v = gaps.get("max_missing", 0)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f'{where}: "max_missing" must be a non-negative integer, not {v!r}')


def _validate_stuck(stuck: Any, where: str) -> None:
    if not isinstance(stuck, Mapping):
        raise ValueError(f"{where}: must be an object")
    _require(stuck, "column", where)
    _require(stuck, "max_run", where)
    _only_keys(stuck, ("column", "max_run"), where)
    if not isinstance(stuck["column"], str) or not stuck["column"]:
        raise ValueError(f'{where}: "column" must be a name')
    v = stuck["max_run"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f'{where}: "max_run" must be an integer of at least 1, not {v!r}')


def _validate_dst(dst: Any, where: str) -> None:
    if not isinstance(dst, Mapping):
        raise ValueError(f"{where}: must be an object")
    _require(dst, "time_zone", where)
    _only_keys(dst, ("time_zone", "fall_back"), where)
    _zone(dst["time_zone"], where)
    if dst.get("fall_back", "repeat") not in ("repeat", "once"):
        raise ValueError(f'{where}: "fall_back" must be "repeat" or "once"')


def _finding(
    rule: str,
    severity: str,
    table: str,
    column: str | None,
    message: str,
    expected: Any,
    observed: Any,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "table": table,
        "column": column,
        "message": f"{table}.{column}: {message}" if column else f"{table}: {message}",
        "expected": expected,
        "observed": observed,
    }


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _iso(us: int, aware: bool) -> str:
    when = _EPOCH + timedelta(microseconds=int(us))
    return (when.replace(tzinfo=UTC) if aware else when).isoformat()


def _time_array(col: pa.ChunkedArray) -> tuple[pa.Array, bool] | None:
    """The column as microsecond timestamps and whether they are instants, or ``None``."""
    arr = col.combine_chunks() if isinstance(col, pa.ChunkedArray) else col
    t = arr.type
    if pa.types.is_dictionary(t):
        arr = arr.dictionary_decode()
        t = arr.type
    if pa.types.is_timestamp(t):
        return pc.cast(arr, pa.timestamp("us", tz=t.tz), safe=False), t.tz is not None
    if pa.types.is_string(t) or pa.types.is_large_string(t):
        for target in (pa.timestamp("us"), pa.timestamp("us", tz="UTC")):
            try:
                return pc.cast(arr, target), target.tz is not None
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                continue
    return None


def _offset(zone: ZoneInfo, seconds: int) -> timedelta:
    off = datetime.fromtimestamp(seconds, zone).utcoffset()
    return off if off is not None else timedelta(0)


def _transitions(zone: ZoneInfo, lo_us: int, hi_us: int) -> list[tuple[str, int, int]]:
    """The offset changes of ``zone`` between two wall-clock times, as ``(kind, start, end)``:
    ``skip`` for the wall-clock interval that does not exist, ``repeat`` for the one that occurs
    twice. Times are microseconds."""
    day = 86_400
    first = lo_us // 1_000_000 // day * day - day
    last = hi_us // 1_000_000 + day
    out: list[tuple[str, int, int]] = []
    try:
        steps = min((last - first) // day, _MAX_SCAN_DAYS)
        prev = _offset(zone, first)
        for i in range(1, int(steps) + 2):
            now = first + i * day
            cur = _offset(zone, now)
            if cur == prev:
                continue
            lo, hi = now - day, now  # the offset changes in (lo, hi]
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if _offset(zone, mid) == prev:
                    lo = mid
                else:
                    hi = mid
            before, after = _offset(zone, lo), _offset(zone, hi)
            at = hi * 1_000_000
            b_us = int(before.total_seconds()) * 1_000_000
            a_us = int(after.total_seconds()) * 1_000_000
            if a_us > b_us:
                out.append(("skip", at + b_us, at + a_us))
            else:
                out.append(("repeat", at + a_us, at + b_us))
            prev = cur
    except (OverflowError, OSError, ValueError):
        pass  # beyond the dates the platform can convert: no more transitions to find
    return out


def _check_rule(tables: Mapping[str, pa.Table], rule: Mapping[str, Any]) -> list[dict[str, Any]]:
    tname, tcol = rule["table"], rule["time"]
    by: list[str] = list(rule.get("by", []))
    if tname not in tables:
        return [
            _finding(
                "timeseries.table_exists", "error", tname, None, "table not found", tname, "missing"
            )
        ]
    table = tables[tname]
    wanted = [tcol, *by] + ([rule["stuck"]["column"]] if "stuck" in rule else [])
    for name in wanted:
        if name not in table.column_names:
            return [
                _finding(
                    "timeseries.column_exists",
                    "error",
                    tname,
                    name,
                    "column not found",
                    name,
                    "missing",
                )
            ]
    parsed = _time_array(table.column(tcol))
    if parsed is None:
        return [
            _finding(
                "timeseries.time_column",
                "error",
                tname,
                tcol,
                f"not a time column ({table.schema.field(tcol).type}): give a timestamp column or "
                "ISO 8601 text",
                "timestamp",
                str(table.schema.field(tcol).type),
            )
        ]
    arr, aware = parsed
    out: list[dict[str, Any]] = []
    nulls = int(arr.null_count)
    if nulls:
        out.append(
            _finding(
                "timeseries.null_times",
                "warning",
                tname,
                tcol,
                f"{_plural(nulls, 'row')} with no time are not checked",
                0,
                {"null_times": nulls},
            )
        )
        table = table.filter(pc.is_valid(arr))
        arr = arr.filter(pc.is_valid(arr))
    zone = _zone(rule["dst"]["time_zone"], "dst") if "dst" in rule else None
    every = parse_every(rule["every"]) if "every" in rule else 0
    instants = pc.cast(arr, pa.int64()).to_numpy(zero_copy_only=False)
    wall = instants
    if aware and zone is not None:
        local = pc.local_timestamp(pc.cast(arr, pa.timestamp("us", tz=str(zone.key))))
        wall = pc.cast(local, pa.int64()).to_numpy(zero_copy_only=False)

    n = len(instants)
    gid = np.zeros(n, dtype=np.int64)
    labels: list[dict[str, Any]] = [{}]
    if by:
        keys = [
            pc.fill_null(pc.dictionary_encode(table.column(c)).combine_chunks().indices, -1)
            for c in by
        ]
        stacked = np.stack(
            [k.to_numpy(zero_copy_only=False).astype(np.int64) for k in keys], axis=1
        )
        uniq, first_idx, gid = np.unique(stacked, axis=0, return_index=True, return_inverse=True)
        gid = gid.reshape(-1).astype(np.int64)
        labels = [{c: table.column(c)[int(i)].as_py() for c in by} for i in first_idx]
    order = np.lexsort((instants, gid))
    t = instants[order]
    g = gid[order]
    w = wall[order]
    same = g[1:] == g[:-1] if n > 1 else np.zeros(0, dtype=bool)

    def at(us: int) -> str:
        return _iso(us, aware)

    def group_of(i: int) -> dict[str, Any]:
        return {"group": labels[int(g[i])]} if by else {}

    # naive local times and a zone: the wall-clock interval that does not exist is not missing
    # (the zone gives wall-clock time, so aware times are compared as instants)
    skips: list[tuple[int, int]] = []
    repeats: list[tuple[int, int]] = []
    transitions: list[tuple[str, int, int]] = []
    if zone is not None and n:
        transitions = _transitions(zone, int(w.min()), int(w.max()))
        skips = [(a, b) for kind, a, b in transitions if kind == "skip"]
        repeats = [(a, b) for kind, a, b in transitions if kind == "repeat"]
    naive_local = zone is not None and not aware

    if "gaps" in rule:
        diff = t[1:] - t[:-1] if n > 1 else np.zeros(0, dtype=np.int64)
        dup = same & (diff == 0)
        if naive_local and repeats:
            inside = np.zeros(len(dup), dtype=bool)
            for a, b in repeats:
                inside |= (t[1:] >= a) & (t[1:] < b)
            dup &= ~inside
        pos = same & (diff > 0)
        off_grid = pos & (diff % every != 0)
        gap_idx = np.flatnonzero(pos & (diff > every) & (diff % every == 0))
        gaps: list[dict[str, Any]] = []
        missing = 0
        for i in gap_idx:
            lo, hi = int(t[i]), int(t[i + 1])
            m = (hi - lo) // every - 1
            if naive_local:
                for a, b in skips:
                    k_lo = max(1, -(-(a - lo) // every))
                    k_hi = -(-(min(b, hi) - lo) // every) - 1
                    m -= max(0, k_hi - k_lo + 1)
            if m <= 0:
                continue
            missing += m
            gaps.append({**group_of(int(i)), "after": at(lo), "before": at(hi), "missing": m})
        limit = int(rule["gaps"].get("max_missing", 0))
        if missing > limit:
            out.append(
                _finding(
                    "timeseries.gaps",
                    "error",
                    tname,
                    tcol,
                    f"{_plural(missing, 'missing step')} (every {rule['every']}, allowed {limit})",
                    {"max_missing": limit},
                    {"missing": missing, "gaps": gaps[:_SAMPLES]},
                )
            )
        if int(dup.sum()):
            out.append(
                _finding(
                    "timeseries.duplicates",
                    "warning",
                    tname,
                    tcol,
                    f"{_plural(int(dup.sum()), 'row')} repeat an earlier time",
                    0,
                    {"duplicates": int(dup.sum())},
                )
            )
        if int(off_grid.sum()):
            out.append(
                _finding(
                    "timeseries.off_grid",
                    "warning",
                    tname,
                    tcol,
                    f"{_plural(int(off_grid.sum()), 'step')} are not a multiple of {rule['every']}",
                    0,
                    {"off_grid": int(off_grid.sum())},
                )
            )

    if "stuck" in rule:
        out.extend(_stuck(table, rule, order, same, g, t, labels, by, at))

    if "dst" in rule and zone is not None:
        out.extend(_dst(rule, tname, tcol, zone, transitions, w, g, labels, every, bool(by)))
    return out


def _stuck(
    table: pa.Table,
    rule: Mapping[str, Any],
    order: np.ndarray[Any, Any],
    same: np.ndarray[Any, Any],
    g: np.ndarray[Any, Any],
    t: np.ndarray[Any, Any],
    labels: list[dict[str, Any]],
    by: list[str],
    at: Any,
) -> list[dict[str, Any]]:
    column, max_run = rule["stuck"]["column"], int(rule["stuck"]["max_run"])
    n = len(order)
    if n < 2:
        return []
    vals = table.column(column).combine_chunks().take(pa.array(order))
    try:
        eq = pc.fill_null(pc.equal(vals.slice(1), vals.slice(0, n - 1)), False)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return [
            _finding(
                "timeseries.column_type",
                "error",
                rule["table"],
                column,
                f"values of type {vals.type} cannot be compared, so the stuck check did not run",
                "comparable",
                str(vals.type),
            )
        ]
    link = np.asarray(eq.to_numpy(zero_copy_only=False), dtype=bool) & same
    edges = np.diff(np.concatenate(([0], link.astype(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)  # exclusive, in link positions
    lengths = ends - starts + 1
    long = np.flatnonzero(lengths > max_run)
    if not len(long):
        return []
    runs = []
    for j in long[:_SAMPLES]:
        s, e = int(starts[j]), int(ends[j])
        run: dict[str, Any] = {
            "start": at(int(t[s])),
            "end": at(int(t[e])),
            "length": int(lengths[j]),
        }
        if by:
            run["group"] = labels[int(g[s])]
        run["value"] = vals[s].as_py()
        runs.append(run)
    longest = int(lengths[long].max())
    return [
        _finding(
            "timeseries.stuck",
            "error",
            rule["table"],
            column,
            f"{_plural(len(long), 'stuck run')}, longest {longest:,} rows (allowed {max_run})",
            {"max_run": max_run},
            {"count": int(len(long)), "longest": longest, "runs": runs},
        )
    ]


def _dst(
    rule: Mapping[str, Any],
    tname: str,
    tcol: str,
    zone: ZoneInfo,
    transitions: list[tuple[str, int, int]],
    w: np.ndarray[Any, Any],
    g: np.ndarray[Any, Any],
    labels: list[dict[str, Any]],
    every: int,
    grouped: bool,
) -> list[dict[str, Any]]:
    expected = 2 if rule["dst"].get("fall_back", "repeat") == "repeat" else 1
    out: list[dict[str, Any]] = []
    # w is in (group, instant) order, so wall-clock times are sorted inside each group except
    # across a fall-back; sort them per group for the counts
    bounds: Any = (
        np.flatnonzero(np.concatenate(([True], g[1:] != g[:-1], [True]))) if len(g) else []
    )
    for kind, a, b in transitions:
        label = _iso(a, False)
        if kind == "skip":
            rows = int(((w >= a) & (w < b)).sum())
            if rows:
                out.append(
                    _finding(
                        "timeseries.dst",
                        "error",
                        tname,
                        tcol,
                        f"{_plural(rows, 'row')} at local times that do not exist in "
                        f"{zone.key} (clocks skip an hour at {label})",
                        {"nonexistent_local_times": 0},
                        {"nonexistent_local_times": {"transition": label, "rows": rows}},
                    )
                )
            continue
        wrong = 0
        samples: list[dict[str, Any]] = []
        for lo_i, hi_i in zip(bounds[:-1], bounds[1:], strict=True):
            ws = np.sort(w[lo_i:hi_i])
            if ws[0] >= a or ws[-1] < b:
                continue  # the series does not cover the repeated hour
            first = int(ws[0])
            p0 = a + (-(a - first) % every)
            points = np.arange(p0, b, every, dtype=np.int64)
            counts = np.searchsorted(ws, points, side="right") - np.searchsorted(
                ws, points, side="left"
            )
            for p, c in zip(points[counts != expected], counts[counts != expected], strict=True):
                wrong += 1
                if len(samples) < _SAMPLES:
                    sample: dict[str, Any] = {"at": _iso(int(p), False), "found": int(c)}
                    if grouped:
                        sample["group"] = labels[int(g[lo_i])]
                    samples.append(sample)
        if wrong:
            out.append(
                _finding(
                    "timeseries.dst",
                    "error",
                    tname,
                    tcol,
                    f"{_plural(wrong, 'local time')} of the repeated hour in {zone.key} "
                    f"({label}) do not occur {expected} time{'s' if expected != 1 else ''}",
                    {"repeated_local_hour": expected},
                    {
                        "repeated_local_hour": {
                            "transition": label,
                            "expected": expected,
                            "wrong": wrong,
                        },
                        "samples": samples,
                    },
                )
            )
    return out


def check_timeseries(
    tables: Mapping[str, pa.Table], rules: list[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Run time-series ``rules`` (see the module docstring) over Arrow ``tables``; the findings,
    errors and warnings, in rule order. Raises :class:`ValueError` for a malformed rule."""
    validate_timeseries_rules(list(rules))
    findings: list[dict[str, Any]] = []
    for rule in rules:
        findings.extend(_check_rule(tables, rule))
    return findings


class TimeSeriesGate(ValidationGate):
    """Runs the ``config["timeseries"]`` rules; errors fail the gate, warnings do not."""

    name = "timeseries_quality"

    def check(self, context: ValidationContext) -> GateResult:
        rules = context.config.get("timeseries") or []
        findings = check_timeseries(context.tables, rules)
        errors = [f["message"] for f in findings if f["severity"] == "error"]
        warnings = [f["message"] for f in findings if f["severity"] == "warning"]
        return GateResult(
            self.name, not errors, errors, warnings, {"rules": len(rules), "findings": findings}
        )


__all__ = [
    "TimeSeriesGate",
    "check_timeseries",
    "parse_every",
    "validate_timeseries_rules",
]
