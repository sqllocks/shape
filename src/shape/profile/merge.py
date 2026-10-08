"""Merge profiles of partitions (or of days) into the profile of their union (W2-01).

``merge_profiles([a, b, ...])`` combines profiles without reading the data again:

* **exact statistics** combine exactly: ``row_count``, ``null_count``, ``null_rate``,
  ``min_value`` and ``max_value`` give the same result as profiling the union; ``mean`` and
  ``std`` combine by the pairwise (Chan) formula and agree with the union to floating-point
  rounding;
* **sketch statistics** (``cardinality``, ``quantiles`` and the top values) need the profile to
  carry its sketch state (``shape.profile(..., sketches=True)``) and agree with the union within
  the documented error of each sketch (``docs/PROFILE_MERGE.md``);
* **everything else** (distribution fits, enum and pattern detection, correlations, keys)
  depends on the whole data and is unknown, ``None``, in a merged profile.

A profile without sketch state merges only for its exact statistics, and only when the caller
says so (``exact_only=True``); otherwise :class:`MergeError` names what is missing.

The merged profile records its inputs' content ids (``Profile.merged_from``) and, when every
input had sketch state, carries the merged state, so merges can be chained.
"""

from __future__ import annotations

import ast
import datetime as _dt
import decimal
import math
import re
from collections.abc import Sequence
from typing import Any

from shape.errors import ShapeError

MERGE_FORMAT = "shape-profile-merge"
MERGE_VERSION = 1

EXACT_STATISTICS = (
    "row_count",
    "null_count",
    "null_rate",
    "nan_count",
    "inf_count",
    "min_value",
    "max_value",
    "mean",
    "std",
)
SKETCH_STATISTICS = ("cardinality", "cardinality_ratio", "is_unique", "quantiles")
_WHOLE_DATA_FIELDS = (
    "is_enum",
    "enum_values",
    "distribution",
    "distribution_params",
    "pattern",
    "outlier_rate",
    "fit_score",
    "value_counts_ext",
    "value_counts_ext_order",
    "string_length",
    "pattern_rates",
    "pattern_contains_rates",
    "hour_histogram",
    "dow_histogram",
    "temporal_histogram",
)
_TABLE_LEVEL = ("primary_key", "detected_fks", "correlation_matrix", "relationships")
_QUANTILE_KEYS = {
    0.01: "p1",
    0.05: "p5",
    0.25: "p25",
    0.5: "p50",
    0.75: "p75",
    0.95: "p95",
    0.99: "p99",
}
_NUMERIC = ("integer", "float")
_BOUNDED_HLL_P = 14
_BOUNDED_KLL_K = 200
_BOUNDED_TOP = 64


class MergeError(ShapeError, ValueError):
    """Profiles cannot be merged (different structure, or the sketch state is missing)."""


def merge_profiles(
    profiles: Sequence[Any], *, exact_only: bool = False, name: str | None = None
) -> Any:
    """The profile of the union of ``profiles`` (see the module docstring).

    ``exact_only=True`` merges only the exact statistics and so works for profiles without
    sketch state. ``name`` is the merged profile's name (default: the first input's).
    """
    from shape.profile.reference.profile import Profile

    items = list(profiles)
    if not items:
        raise MergeError("nothing to merge: give at least one profile")
    for p in items:
        if not isinstance(p, Profile):
            raise MergeError(f"expected Profile objects, got {type(p).__name__}")
    dataset = items[0].is_dataset
    if any(p.is_dataset != dataset for p in items):
        raise MergeError("cannot merge a table profile with a dataset profile (tables differ)")
    if dataset:
        names = list(items[0].tables)
        for p in items[1:]:
            if set(p.tables) != set(names):
                raise MergeError(
                    f"the profiles do not hold the same tables: {sorted(names)} and "
                    f"{sorted(p.tables)}"
                )
        per_table = {n: [p.tables[n] for p in items] for n in names}
    else:
        (only,) = items[0].tables
        per_table = {only: [next(iter(p.tables.values())) for p in items]}

    for p in items:
        _check_input(p)
    labels = [p.name for p in items]
    for tname, parts in per_table.items():
        _check_columns(tname, parts, labels)
    use_sketches = False
    if not exact_only:
        missing = [p.name for p in items if p.sketches is None]
        if missing:
            raise MergeError(_missing_message(missing))
        use_sketches = True

    states: dict[str, tuple[Any, Any]] = {}
    sketch_cols: dict[str, dict[str, Any]] = {}
    if use_sketches:
        states, sketch_cols = _merge_states(items, per_table)

    tables: dict[str, Any] = {}
    for tname, parts in per_table.items():
        tables[tname] = _merge_table(tname, parts, sketch_cols.get(tname))

    merge_block = _block(items, exact_only, sketch_cols)
    if dataset:
        data: dict[str, Any] = {"tables": tables, "relationships": []}
    else:
        data = dict(next(iter(tables.values())))
        data["name"] = name or items[0].name
    data["merge"] = merge_block
    sketches = _state_document(states) if use_sketches else None
    return Profile(data, name=name or items[0].name, sketches=sketches)


def check_block(block: Any) -> None:
    """Check the ``merge`` block of a merged profile body; a newer version is refused by name."""
    if not isinstance(block, dict) or block.get("format") != MERGE_FORMAT:
        raise MergeError(f"not a profile merge record (format {MERGE_FORMAT!r} expected)")
    version = block.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MergeError("the profile merge record has no valid version")
    if version > MERGE_VERSION:
        raise MergeError(
            f"the profile merge record is version {version}, newer than the version "
            f"{MERGE_VERSION} this Shape reads: upgrade Shape to use it"
        )


def _check_input(p: Any) -> None:
    from shape.profile import sketches

    block = p._data.get("merge")  # no deep copy of the whole body
    if block is not None:
        try:
            check_block(block)
        except MergeError as exc:
            raise MergeError(f"{p.name!r}: {exc}") from exc
    if p._sketches is not None:
        try:
            sketches.validate(p._sketches)
        except sketches.SketchStateError as exc:
            raise MergeError(f"{p.name!r}: {exc}") from exc


# ------------------------------------------------------------------------------ messages


def _missing_message(missing: list[str]) -> str:
    return (
        f"cannot merge: {', '.join(repr(m) for m in missing)} carries no sketch state, and "
        f"{', '.join(SKETCH_STATISTICS)} need it. Profile with sketches "
        "(`shape.profile(..., sketches=True)`, or `shape profile --sketches`), or merge only the "
        f"exact statistics ({', '.join(EXACT_STATISTICS)}) with exact_only=True "
        "(`--exact-only`)"
    )


# --------------------------------------------------------------------------- sketch state


def _merge_states(
    items: list[Any], per_table: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, tuple[Any, Any]], dict[str, dict[str, Any]]]:
    from shape.profile import sketches

    states: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    out: dict[str, dict[str, Any]] = {}
    for tname, parts in per_table.items():
        restored = []
        for item, part in zip(items, parts, strict=True):
            entry = _sketch_entry(item, part, tname)
            try:
                schema, state = sketches.restore(entry)
            except sketches.SketchStateError as exc:
                raise MergeError(f"{item.name!r}: {exc}") from exc
            names = [f.name for f in schema]
            if set(names) != set(part["columns"]) or len(names) != len(part["columns"]):
                raise MergeError(
                    f"{item.name!r}: the sketch state of table {tname!r} has other columns than "
                    "the profile"
                )
            restored.append((entry, schema, state))
        target = _common_schema(tname, items, [schema for _, schema, _ in restored])
        merged = None
        for item, (entry, schema, state) in zip(items, restored, strict=True):
            if schema != target:
                # the same columns in another order, or a column with no values in this part
                state = _conform(item, entry, schema, target)
            if merged is None:
                merged = state
                continue
            try:
                merged.merge(state)
            except ValueError as exc:
                raise MergeError(f"table {tname!r}: {exc}") from exc
        assert merged is not None
        states[tname] = merged
        schemas[tname] = target
        out[tname] = _sketch_columns(merged)
    return {t: (schemas[t], s) for t, s in states.items()}, out


def _sketch_entry(item: Any, part: dict[str, Any], tname: str) -> Any:
    held = (item.sketches or {}).get("tables", {})
    # a table profile's sketch state is keyed by that profile's own table name, which differs
    # between partitions (file stems); a dataset's by the shared table names
    entry = held.get(tname) if item.is_dataset else next(iter(held.values()), None)
    if entry is None or (not item.is_dataset and len(held) != 1):
        raise MergeError(f"{item.name!r}: its sketch state has no table {tname!r}")
    if entry["rows"] != part["row_count"]:
        raise MergeError(
            f"{item.name!r}: the sketch state of table {tname!r} covers {entry['rows']} "
            f"rows but the profile has {part['row_count']}: it does not belong to it"
        )
    return entry


def _common_schema(tname: str, items: list[Any], schemas: list[Any]) -> Any:
    """The schema the states merge in: the first input's column order, each column typed by
    the first input that has values in it (a column with no values in a partition is typed
    ``null`` there). Two different value types for one column are a :class:`MergeError`."""
    import pyarrow as pa  # type: ignore[import-untyped]

    fields = []
    for name in schemas[0].names:
        owner, typ = items[0].name, schemas[0].field(name).type
        for item, schema in zip(items, schemas, strict=True):
            t = schema.field(name).type
            if pa.types.is_null(typ) and not pa.types.is_null(t):
                owner, typ = item.name, t
        fields.append(pa.field(name, typ))
        for item, schema in zip(items, schemas, strict=True):
            t = schema.field(name).type
            if not pa.types.is_null(t) and t != typ:
                diffs = [
                    f"{n}: {_value_type(schemas, n)} vs {schema.field(n).type}"
                    for n in schemas[0].names
                    if not pa.types.is_null(schema.field(n).type)
                    and schema.field(n).type != _value_type(schemas, n)
                ]
                raise MergeError(
                    f"table {tname!r}: the column types differ between {owner!r} and "
                    f"{item.name!r} ({'; '.join(diffs[:3])}), so their sketches cannot be "
                    "combined: profile them with a common schema"
                )
    return pa.schema(fields)


def _value_type(schemas: list[Any], name: str) -> Any:
    import pyarrow as pa

    types = [s.field(name).type for s in schemas]
    return next((t for t in types if not pa.types.is_null(t)), types[0])


def _conform(item: Any, entry: Any, schema: Any, target: Any) -> Any:
    """``item``'s state with ``target``'s columns: reordered by name, and a column that had no
    values (typed ``null``) replaced by an all-null column of the target type. The snapshot
    format is shared by both kernels, so the reference twin rebuilds it and the active kernel
    reads the result."""

    from shape.kernel.dispatch import get_kernel
    from shape.kernel.reference import profile as twin
    from shape.profile import sketches

    try:
        source = twin.ProfileState.from_snapshot(schema, sketches._unb64(entry["state"]))
    except (ValueError, sketches.SketchStateError) as exc:
        raise MergeError(f"{item.name!r}: the sketch state cannot be read: {exc}") from exc
    by_name = {f.name: col for f, col in zip(schema, source._cols, strict=True)}
    state = twin.ProfileState(target, "bounded")
    state._rows = source.rows
    columns = []
    for f in target:
        col = by_name[f.name]
        if schema.field(f.name).type != f.type:  # no values here: all null, of the target type
            col = twin._Column(f.name, f.type, "bounded")
            col.count = col.nulls = source.rows
        columns.append(col)
    state._cols = columns
    return get_kernel().ProfileState.from_snapshot(target, state.snapshot())


def _state_document(states: dict[str, tuple[Any, Any]]) -> dict[str, Any]:
    from shape.profile import sketches

    tables = {}
    for tname, (schema, state) in states.items():
        tables[tname] = {
            "rows": int(state.rows),
            "schema": sketches._b64(schema.serialize().to_pybytes()),
            "state": sketches._b64(bytes(state.snapshot())),
        }
    return {
        "format": sketches.FORMAT,
        "version": sketches.VERSION,
        "snapshot_version": sketches.SNAPSHOT_VERSION,
        "tables": tables,
    }


def _json_number(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return "NaN" if v != v else ("inf" if v > 0 else "-inf")
    return v


def _sketch_columns(state: Any) -> dict[str, Any]:
    """What the merged sketches say about each column, by name."""
    from shape.profile.error import hll_error, kll_error, space_saving_error

    result = state.finalize(_BOUNDED_TOP)
    out: dict[str, Any] = {}
    for col in result["columns"]:
        kind = col["kind"]
        entry: dict[str, Any] = {"kind": kind, "count": col["count"] - col["null_count"]}
        models: dict[str, Any] = {}
        if kind == "bool":
            entry["distinct"] = int(col["true_count"] > 0) + int(col["false_count"] > 0)
        elif "distinct" in col:
            entry["distinct"] = col["distinct"]
            entry["top"] = [
                [_json_number(v), int(c), int(e)] for v, c, e, _first in col.get("top", [])
            ]
            models["cardinality"] = hll_error(_BOUNDED_HLL_P).to_dict()
            models["top"] = space_saving_error(_BOUNDED_TOP).to_dict()
        if kind in ("int", "float"):
            entry["finite_count"] = col["finite_count"]
            entry["inf_distinct"] = int(col["pos_inf_count"] > 0) + int(col["neg_inf_count"] > 0)
            entry["quantiles"] = {
                str(q): v for q, v in (col.get("quantiles") or {}).items() if q in _QUANTILE_KEYS
            }
            models["quantiles"] = kll_error(_BOUNDED_KLL_K).to_dict()
        entry["error_models"] = models
        out[col["name"]] = entry
    return out


# ---------------------------------------------------------------------------- the lineage


def _block(items: list[Any], exact_only: bool, sketch_cols: dict[str, dict[str, Any]]) -> Any:
    dropped = list(_WHOLE_DATA_FIELDS) + (list(SKETCH_STATISTICS) if exact_only else [])
    block: dict[str, Any] = {
        "format": MERGE_FORMAT,
        "version": MERGE_VERSION,
        "mode": "exact-only" if exact_only else "sketched",
        "inputs": [
            {
                "name": p.name,
                "shape_content_id": p.content_id,
                "row_count": sum(t["row_count"] for t in p.tables.values()),
                "sketches": p.sketches is not None,
            }
            for p in items
        ],
        "exact_statistics": list(EXACT_STATISTICS),
        "unavailable": dropped + list(_TABLE_LEVEL),
    }
    if not exact_only:
        block["sketch_columns"] = _flatten_sketch_columns(items, sketch_cols)
    return block


def _flatten_sketch_columns(items: list[Any], sketch_cols: dict[str, dict[str, Any]]) -> Any:
    """``{column: entry}`` for a table profile, ``{table: {column: entry}}`` for a dataset."""
    if items[0].is_dataset:
        return sketch_cols
    return next(iter(sketch_cols.values()))


# ------------------------------------------------------------------------------ the tables


def _check_columns(tname: str, parts: list[dict[str, Any]], labels: list[str]) -> None:
    first = list(parts[0]["columns"])
    for label, part in zip(labels, parts, strict=True):
        if set(part["columns"]) != set(first):
            raise MergeError(
                f"table {tname!r}: the columns differ between {labels[0]!r} "
                f"({', '.join(first)}) and {label!r} ({', '.join(part['columns'])})"
            )


def _merge_table(
    tname: str,
    parts: list[dict[str, Any]],
    sketch_cols: dict[str, Any] | None,
) -> dict[str, Any]:
    first_names = list(parts[0]["columns"])
    rows = sum(int(p["row_count"]) for p in parts)
    columns = {
        cname: _merge_column(
            tname,
            cname,
            [p["columns"][cname] for p in parts],
            [int(p["row_count"]) for p in parts],
            None if sketch_cols is None else sketch_cols.get(cname),
            rows,
        )
        for cname in first_names
    }
    return {
        "name": tname,
        "row_count": rows,
        "primary_key": [],
        "detected_fks": {},
        "correlation_matrix": None,
        "columns": columns,
    }


def _merge_column(
    tname: str,
    cname: str,
    cols: list[dict[str, Any]],
    rows: list[int],
    sketch: dict[str, Any] | None,
    total_rows: int,
) -> dict[str, Any]:
    from shape.profile.reference.profile import _clean

    where = f"table {tname!r} column {cname!r}" if tname else f"column {cname!r}"
    nulls = [int(c["null_count"]) for c in cols]
    nans = [int(c.get("nan_count") or 0) for c in cols]
    infs = [int(c.get("inf_count") or 0) for c in cols]
    nn = [r - n for r, n in zip(rows, nulls, strict=True)]
    finite = [v - a - b for v, a, b in zip(nn, nans, infs, strict=True)]
    dtype = _unify_dtype(where, cols, nn)
    null_count = sum(nulls)
    unknown_rates = any(c.get("null_rate") is None for c in cols)
    null_rate = (
        None if unknown_rates else (round(null_count / total_rows, 6) if total_rows else 0.0)
    )
    mean = std = None
    if dtype in _NUMERIC:
        mean, std = _moments(where, cols, finite)
    out: dict[str, Any] = {
        "name": cname,
        "dtype": dtype,
        "null_count": null_count,
        "null_rate": null_rate,
        "cardinality": None,
        "cardinality_ratio": None,
        "is_unique": None,
        "is_enum": None,
        "enum_values": None,
        "min_value": _promote(_extreme(where, cols, "min_value", min), dtype, cols, nn),
        "max_value": _promote(_extreme(where, cols, "max_value", max), dtype, cols, nn),
        "mean": _clean(mean),
        "std": _clean(std),
        "distribution": None,
        "distribution_params": None,
        "pattern": None,
        "pattern_rates": None,
        "pattern_contains_rates": None,
        "precision": _shared(cols, "precision"),
        "scale": _shared(cols, "scale"),
        "is_primary_key": False,
        "is_foreign_key": False,
        "fk_ref_table": None,
        "quantiles": None,
        "hour_histogram": None,
        "dow_histogram": None,
        "temporal_histogram": None,
        "string_length": None,
        "outlier_rate": None,
        "fit_score": None,
        "value_counts_ext": None,
        "value_counts_ext_order": None,
    }
    for field, counts in (("nan_count", nans), ("inf_count", infs)):
        if sum(counts):  # absent when zero, as in any profile
            out[field] = sum(counts)
    if sketch is not None:
        _apply_sketch(out, sketch, dtype, sum(finite), total_rows, unknown_rates)
    return out


def _shared(cols: list[dict[str, Any]], field: str) -> Any:
    """A value every profile that has one agrees on (a decimal's precision), else unknown."""
    values = {repr(c.get(field)) for c in cols if c.get(field) is not None}
    if len(values) != 1:
        return None
    return next(c[field] for c in cols if c.get(field) is not None)


def _apply_sketch(
    out: dict[str, Any],
    sketch: dict[str, Any],
    dtype: str,
    non_null: int,
    total_rows: int,
    unknown_rates: bool,
) -> None:
    distinct = sketch.get("distinct")
    if distinct is not None:
        # the sketch counts +inf and -inf as values; a profile's cardinality does not
        distinct -= sketch.get("inf_distinct", 0)
        card = max(0, min(int(round(distinct)), non_null))
        out["cardinality"] = card
        if not unknown_rates:
            out["cardinality_ratio"] = round(card / total_rows, 6) if total_rows else 0.0
    quantiles = sketch.get("quantiles")
    if dtype in _NUMERIC and quantiles and sketch.get("finite_count", 0) > 1:
        out["quantiles"] = {
            _QUANTILE_KEYS[float(q)]: round(float(v), 6) for q, v in quantiles.items()
        }


def _unify_dtype(where: str, cols: list[dict[str, Any]], nn: list[int]) -> str:
    seen = {c["dtype"] for c, n in zip(cols, nn, strict=True) if n > 0}
    if not seen:
        return str(cols[0]["dtype"])  # no values anywhere: the type of an empty column is moot
    if len(seen) == 1:
        return str(next(iter(seen)))
    if seen <= {"integer", "float"}:
        return "float"
    if seen <= {"date", "datetime"}:
        return "datetime"
    raise MergeError(f"{where}: the types differ between the profiles ({', '.join(sorted(seen))})")


def _promote(tagged: Any, dtype: str, cols: list[dict[str, Any]], nn: list[int]) -> Any:
    """An extreme of a partition whose type was promoted (integer to float, date to datetime),
    tagged as the profile of the union tags it."""
    if tagged is None or tagged[1] is None:
        return tagged
    tag, value = tagged
    if dtype == "float" and tag == "int":
        return ["float", float(value)]
    if dtype == "datetime" and tag == "date":
        tags = [
            c[f][0]
            for c, n in zip(cols, nn, strict=True)
            if n > 0 and c["dtype"] == "datetime"
            for f in ("min_value", "max_value")
            if c.get(f) and c[f][0] in ("timestamp", "datetime")
        ]
        try:
            moment = _dt.datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return tagged
        return [tags[0] if tags else "timestamp", str(moment)]
    return tagged


# ------------------------------------------------------------------------ exact statistics


def _num(v: Any) -> float:
    return float(v)  # accepts the "NaN" / "inf" / "-inf" strings of a saved profile


def _moments(where: str, cols: list[dict[str, Any]], finite: list[int]) -> tuple[Any, Any]:
    """Mean and sample standard deviation of the union, from each part's count of finite values,
    mean and std. As in a profile, NaN and infinite values are not part of either; the std of
    fewer than two values is unknown."""
    n = 0
    mean = m2 = 0.0
    for col, ni in zip(cols, finite, strict=True):
        if ni == 0:
            continue
        if col.get("mean") is None or (ni > 1 and col.get("std") is None):
            raise MergeError(f"{where}: a profile has {ni} numbers but no mean or std")
        mi = float(col["mean"])
        part_m2 = 0.0 if ni == 1 else float(col["std"]) ** 2 * (ni - 1)
        delta = mi - mean
        total = n + ni
        m2 += part_m2 + delta * delta * n * ni / total
        mean += delta * ni / total
        n = total
    if n == 0:
        return None, None
    return mean, (math.sqrt(m2 / (n - 1)) if n > 1 else None)


_TIMEDELTA = re.compile(r"(-?\d+) days \+?(\d+):(\d\d):(\d\d)(?:\.(\d{1,6}))?")


def _key(where: str, tagged: Any) -> tuple[str, Any]:
    """A comparable key for a tagged min/max value. Values that cannot be read back (a text
    cut to its first characters) compare as their text, within their own kind."""
    tag, value = tagged
    if tag in ("int", "float", "bool"):
        return "number", value
    if tag == "str":
        return "text", value
    if tag in ("timestamp", "datetime", "date"):
        try:
            return "time", _dt.datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return "time-text", value
    if tag == "Decimal":
        try:
            return "number", decimal.Decimal(value)
        except (decimal.InvalidOperation, TypeError):
            return "decimal-text", value
    if tag == "time":
        try:
            return "clock", _dt.time.fromisoformat(value)
        except (ValueError, TypeError):
            return "clock-text", value
    if tag == "Timedelta":
        m = _TIMEDELTA.fullmatch(value) if isinstance(value, str) else None
        if m is None:
            return "duration-text", value
        days, hours, minutes, seconds, frac = m.groups()
        micros = int((frac or "0").ljust(6, "0"))
        return "duration", _dt.timedelta(
            days=int(days),
            hours=int(hours),
            minutes=int(minutes),
            seconds=int(seconds),
            microseconds=micros,
        )
    if tag == "bytes":
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            parsed = None
        if isinstance(parsed, bytes):
            return "bytes", parsed
        return "bytes-text", value
    raise MergeError(f"{where}: the {tag} extremes cannot be combined")


def _extreme(where: str, cols: list[dict[str, Any]], field: str, pick: Any) -> Any:
    best: Any = None
    best_key: tuple[str, Any] | None = None
    for col in cols:
        tagged = col.get(field)
        if tagged is None or tagged[1] is None:
            continue
        key = _key(where, tagged)
        if best_key is None:
            best, best_key = tagged, key
            continue
        if key[0] != best_key[0]:
            raise MergeError(f"{where}: {field} has different kinds ({best[0]} and {tagged[0]})")
        try:
            wins = pick(key[1], best_key[1]) == key[1] and key[1] != best_key[1]
        except TypeError as exc:  # a timestamp with a zone against one without
            raise MergeError(f"{where}: {field} mixes time zones: {exc}") from exc
        except decimal.InvalidOperation as exc:  # a decimal NaN
            raise MergeError(f"{where}: {field} cannot be compared: {exc!r}") from exc
        if wins:
            best, best_key = tagged, key
    return best
