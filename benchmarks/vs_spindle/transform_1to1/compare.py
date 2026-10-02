"""Schema and row comparison of Shape's star-schema and CDM output with the baseline's.

Internal harness (Shape venv). ``compare_star`` and ``compare_cdm`` take the two output
directories and return ``(failures, allowed)``: every difference that is not covered by an entry
of ``DIFFERENCES`` is a failure. An allowed difference is named, has a reason and is only matched
in the exact pattern it describes (what is the same stays checked).

Logical types: ``large_string`` is ``string``; an integer column with nulls that the baseline's
dataframe library turned into ``double`` is the ``nullable_int_float`` allowance (values must still
be equal).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

# name -> reason. Each is a deliberate fix of a baseline defect (owner standing decision,
# 2026-10-01: fix what harms user trust) or a naming change; the tests in tests/dimensional/
# pin every one.
DIFFERENCES: dict[str, str] = {
    "nullable_int_float": (
        "the baseline's dataframe turns an integer column that has nulls into double (5 becomes "
        "5.0); Shape keeps the integer type and the nulls. Values are compared as numbers."
    ),
    "date_dimension_covers_all_facts": (
        "the baseline builds dim_date from the date columns of each fact's primary table before "
        "joins, so a date that arrives through a join (fact_sale's order_date, from order) is "
        "never in dim_date and fact_sale.sk_date refers to dates that have no row. Shape builds it "
        "from the sk_date of every fact: its range is a superset, the rows inside the baseline's "
        "range are equal, and every fact sk_date has a row."
    ),
    "model_wording": (
        "model.json names the tool in its description and application fields, and its "
        "modifiedTime is the run time; Shape's own wording is used and the time is ignored."
    ),
}

_INT_FLOAT = "nullable_int_float"


def _logical(t: pa.DataType) -> str:
    if pa.types.is_large_string(t):
        return "string"
    return str(t)


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _cells(col: pa.ChunkedArray) -> list[Any]:
    out = []
    for v in col.to_pylist():
        if isinstance(v, float) and v != v:
            v = None
        out.append(v)
    return out


def compare_tables(
    name: str, shape: pa.Table, base: pa.Table, allowed: dict[str, list[str]]
) -> list[str]:
    """Differences between two tables (same names, order, logical types, equal rows)."""
    bad: list[str] = []
    if shape.column_names != base.column_names:
        return [f"{name}: columns {shape.column_names} != {base.column_names}"]
    if shape.num_rows != base.num_rows:
        return [f"{name}: {shape.num_rows} rows != {base.num_rows}"]
    for s_field, b_field in zip(shape.schema, base.schema, strict=True):
        col = f"{name}.{s_field.name}"
        s_type, b_type = _logical(s_field.type), _logical(b_field.type)
        if s_type != b_type:
            if pa.types.is_integer(s_field.type) and pa.types.is_floating(b_field.type):
                allowed.setdefault(_INT_FLOAT, []).append(col)
            else:
                bad.append(f"{col}: type {s_type} != {b_type}")
                continue
        s_cells, b_cells = _cells(shape[s_field.name]), _cells(base[b_field.name])
        if [_num(x) for x in s_cells] != [_num(x) for x in b_cells]:
            first = next(
                i
                for i, (x, y) in enumerate(zip(s_cells, b_cells, strict=True))
                if _num(x) != _num(y)
            )
            bad.append(f"{col}: row {first}: {s_cells[first]!r} != {b_cells[first]!r}")
    return bad


def _read(path: Path) -> pa.Table:
    return pq.read_table(path)


def compare_star(shape_dir: Path, base_dir: Path) -> tuple[list[str], dict[str, list[str]]]:
    bad: list[str] = []
    allowed: dict[str, list[str]] = {}
    shape_files = sorted(p.name for p in shape_dir.glob("*.parquet"))
    base_files = sorted(p.name for p in base_dir.glob("*.parquet"))
    if shape_files != base_files:
        return [f"files {shape_files} != {base_files}"], allowed
    for fname in base_files:
        name = fname.removesuffix(".parquet")
        shape, base = _read(shape_dir / fname), _read(base_dir / fname)
        if name == "dim_date":
            bad += _compare_date_dim(shape, base, shape_dir, base_dir, allowed)
        else:
            bad += compare_tables(name, shape, base, allowed)
    return bad, allowed


def _compare_date_dim(
    shape: pa.Table, base: pa.Table, shape_dir: Path, base_dir: Path, allowed: dict[str, list[str]]
) -> list[str]:
    bad: list[str] = []
    if shape.column_names != base.column_names:
        return [f"dim_date: columns {shape.column_names} != {base.column_names}"]
    lo, hi = base["sk_date"].to_pylist()[0], base["sk_date"].to_pylist()[-1]
    keys = shape["sk_date"].to_pylist()
    inside = [i for i, k in enumerate(keys) if lo <= k <= hi]
    bad += compare_tables("dim_date", shape.take(pa.array(inside)), base, allowed)
    have = set(keys)
    for fact in sorted(p.name for p in shape_dir.glob("fact_*.parquet")):
        sk = [v for v in _read(shape_dir / fact)["sk_date"].to_pylist() if v is not None]
        missing = sum(1 for v in sk if v not in have)
        if missing:
            bad.append(f"{fact}: {missing} sk_date values have no dim_date row")
        base_have = set(base["sk_date"].to_pylist())
        base_facts = _read(base_dir / fact)["sk_date"].to_pylist()
        base_missing = sum(1 for v in base_facts if v is not None and v not in base_have)
        if base_missing or shape.num_rows != base.num_rows:
            allowed.setdefault("date_dimension_covers_all_facts", []).append(
                f"{fact}: baseline {base_missing} of {len(sk)} sk_date values without a "
                f"dim_date row, Shape 0"
            )
    return bad


_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _token(v: str | None) -> Any:
    """A CSV cell as a comparable value (booleans, numbers and timestamps are normalised)."""
    if v is None or v == "":
        return None
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return float(v)
    except ValueError:
        pass
    if _TS.match(v):
        return dt.datetime.fromisoformat(v.replace("T", " "))
    return v


def _csv_rows(path: Path) -> tuple[list[str], list[list[Any]]]:
    names = pacsv.read_csv(
        path, parse_options=pacsv.ParseOptions(), read_options=pacsv.ReadOptions(skip_rows=0)
    ).column_names
    table = pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            column_types={n: pa.string() for n in names}, strings_can_be_null=True, null_values=[""]
        ),
    )
    cols = [table[n].to_pylist() for n in names]
    return names, [[_token(c[i]) for c in cols] for i in range(table.num_rows)]


def compare_star_csv(shape_dir: Path, base_dir: Path) -> tuple[list[str], dict[str, list[str]]]:
    bad: list[str] = []
    allowed: dict[str, list[str]] = {}
    shape_files = sorted(p.name for p in shape_dir.glob("*.csv"))
    base_files = sorted(p.name for p in base_dir.glob("*.csv"))
    if shape_files != base_files:
        return [f"files {shape_files} != {base_files}"], allowed
    for fname in base_files:
        s_names, s_rows = _csv_rows(shape_dir / fname)
        b_names, b_rows = _csv_rows(base_dir / fname)
        if s_names != b_names:
            bad.append(f"{fname}: columns {s_names} != {b_names}")
            continue
        if fname == "dim_date.csv":
            lo, hi = b_rows[0][0], b_rows[-1][0]
            s_rows = [r for r in s_rows if lo <= r[0] <= hi]
            allowed.setdefault("date_dimension_covers_all_facts", []).append(fname)
        if len(s_rows) != len(b_rows):
            bad.append(f"{fname}: {len(s_rows)} rows != {len(b_rows)}")
            continue
        for i, (x, y) in enumerate(zip(s_rows, b_rows, strict=True)):
            if x != y:
                j = next(k for k in range(len(x)) if x[k] != y[k])
                bad.append(f"{fname}: row {i} column {s_names[j]}: {x[j]!r} != {y[j]!r}")
                break
    return bad, allowed


def _entity_view(entity: dict[str, Any]) -> dict[str, Any]:
    view = dict(entity)
    view.pop("description", None)
    return view


def _has_int_nulls(path: Path, column: str, fmt: str) -> bool:
    """Whether the Shape file's ``column`` is an integer column that holds nulls."""
    if fmt == "parquet":
        table = _read(path)
        return pa.types.is_integer(table.schema.field(column).type) and (
            table[column].null_count > 0
        )
    names, rows = _csv_rows(path)
    values = [r[names.index(column)] for r in rows]
    return None in values and all(v is None or float(v).is_integer() for v in values)


def _compare_entity(
    s: dict[str, Any], b: dict[str, Any], shape_dir: Path, fmt: str, allowed: dict[str, list[str]]
) -> list[str]:
    """One entity's manifest entry: equal, except a double attribute that is Shape's integer
    column with nulls (``nullable_int_float``)."""
    bad: list[str] = []
    for key in ("name", "$type", "partitions"):
        if s[key] != b[key]:
            bad.append(f"entity {b['name']}: {key} {s[key]} != {b[key]}")
    s_attrs, b_attrs = s["attributes"], b["attributes"]
    if [a["name"] for a in s_attrs] != [a["name"] for a in b_attrs]:
        return bad + [f"entity {b['name']}: attributes differ in names or order"]
    rel = b["partitions"][0]["location"]
    for sa, ba in zip(s_attrs, b_attrs, strict=True):
        if sa == ba:
            continue
        if (
            sa["dataType"] == "int64"
            and ba["dataType"] == "double"
            and _has_int_nulls(shape_dir / rel, sa["name"], fmt)
        ):
            allowed.setdefault(_INT_FLOAT, []).append(f"{b['name']}.{sa['name']}")
        else:
            bad.append(f"entity {b['name']}.{sa['name']}: {sa['dataType']} != {ba['dataType']}")
    return bad


def compare_cdm(
    shape_dir: Path, base_dir: Path, fmt: str
) -> tuple[list[str], dict[str, list[str]]]:
    bad: list[str] = []
    allowed: dict[str, list[str]] = {"model_wording": ["description, application, modifiedTime"]}
    s_model = json.loads((shape_dir / "model.json").read_text())
    b_model = json.loads((base_dir / "model.json").read_text())
    for doc in (s_model, b_model):
        for k in ("description", "application", "modifiedTime"):
            doc.pop(k, None)
    s_entities = [_entity_view(e) for e in s_model.pop("entities")]
    b_entities = [_entity_view(e) for e in b_model.pop("entities")]
    if s_model != b_model:
        bad.append(f"model.json header: {s_model} != {b_model}")
    if len(s_entities) != len(b_entities):
        bad.append(f"entity count {len(s_entities)} != {len(b_entities)}")
    for s, b in zip(s_entities, b_entities, strict=False):
        bad += _compare_entity(s, b, shape_dir, fmt, allowed)
    for b in b_entities:
        rel = b["partitions"][0]["location"]
        if not (shape_dir / rel).exists():
            bad.append(f"{rel}: missing")
            continue
        if fmt == "parquet":
            bad += compare_tables(b["name"], _read(shape_dir / rel), _read(base_dir / rel), allowed)
        else:
            s_names, s_rows = _csv_rows(shape_dir / rel)
            b_names, b_rows = _csv_rows(base_dir / rel)
            if (s_names, s_rows) != (b_names, b_rows):
                bad.append(f"{rel}: rows or columns differ")
    return bad, allowed
