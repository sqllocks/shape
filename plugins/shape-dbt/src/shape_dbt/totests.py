"""``shape to-dbt-tests``: a contract (v1, plan section 12.3) or a profile as dbt tests.

The tests are plain ``schema.yml`` data tests, so they run inside a dbt job with no Python:
``not_null``, ``unique`` and ``accepted_values`` from dbt itself, and ``dbt_utils.accepted_range``
and the ``dbt_expectations`` tests for the rest. :func:`required_packages` says which packages the
output needs (``packages.yml``).

| Contract rule | dbt test |
|---|---|
| ``nullable: false`` | ``not_null`` |
| ``unique: true`` | ``unique`` |
| ``allowed_values`` | ``accepted_values`` |
| ``min`` / ``max`` (numbers) | ``dbt_utils.accepted_range`` |
| ``max_null_rate`` | ``dbt_utils.not_null_proportion`` (``at_least`` is one minus the rate) |
| ``row_count`` ``min`` / ``max`` | ``dbt_expectations.expect_table_row_count_to_be_between`` |
| ``required_columns`` | ``dbt_expectations.expect_column_to_exist`` |
| ``allow_extra_columns: false`` | ``dbt_expectations.expect_table_columns_to_match_set`` |
| a profile's mean, standard deviation, quartiles | ``expect_column_mean_to_be_between``, ``..._stdev_...``, ``expect_column_quantile_values_to_be_between`` |

What dbt tests cannot express (:data:`NOT_EXPRESSIBLE`) is carried on the column as ``meta.shape``
so the contract can be rebuilt, and is **not tested** by dbt: ``dtype`` (a data type is
adapter-specific; use a model contract's ``data_type``), ``pattern``, ``distribution``,
``min_true_rate`` / ``max_true_rate`` and non-numeric ``min`` / ``max``.
:func:`contract_from_schema_yaml` reads the tests back; :func:`expressible` is the part of a
contract that survives the round trip with the tests alone.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from shape.drift.engine import DEFAULT_THRESHOLDS

from .project import DbtProjectError, DbtRelation, read_schema_yaml

# Package requirements. dbt_expectations depends on dbt_date, which dbt deps adds itself.
DBT_UTILS = {"package": "dbt-labs/dbt_utils", "version": [">=1.1.0", "<2.0.0"]}
DBT_EXPECTATIONS = {"package": "metaplane/dbt_expectations", "version": [">=0.10.0", "<0.11.0"]}

# Column rules a dbt test cannot state, kept as meta only.
NOT_EXPRESSIBLE = ("dtype", "pattern", "distribution", "min_true_rate", "max_true_rate")

NULL_RATE_PLACES = 9
_QUANTILES = {"p25": 0.25, "p50": 0.5, "p75": 0.75}
_EXPECT = "dbt_expectations."
_RANGE = "dbt_utils.accepted_range"
_NOT_NULL_SHARE = "dbt_utils.not_null_proportion"
_ROW_COUNT = _EXPECT + "expect_table_row_count_to_be_between"
_EXISTS = _EXPECT + "expect_column_to_exist"
_MATCH_SET = _EXPECT + "expect_table_columns_to_match_set"
_MEAN = _EXPECT + "expect_column_mean_to_be_between"
_STDEV = _EXPECT + "expect_column_stdev_to_be_between"
_QUANTILE = _EXPECT + "expect_column_quantile_values_to_be_between"
_TAG = "shape"
_KEY_NAME = re.compile(r"(^|_)(id|key|uuid|guid)$", re.IGNORECASE)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ---- contract <-> normal form --------------------------------------------------------------


def normalize_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """The contract without the rules that say nothing (``nullable: true``, ``unique: false``,
    ``allow_extra_columns: true``, a column with no rules), the null rate rounded to nine places,
    and ``required_columns`` sorted: two contracts that mean the same compare equal."""
    out: dict[str, Any] = {}
    if "tables" in contract:
        out["tables"] = {k: normalize_contract(v) for k, v in contract["tables"].items()}
        return out
    rc = {k: v for k, v in (contract.get("row_count") or {}).items() if k in ("min", "max")}
    if rc:
        out["row_count"] = rc
    columns: dict[str, Any] = {}
    for name, rules in (contract.get("columns") or {}).items():
        kept: dict[str, Any] = {}
        for key, value in rules.items():
            if key == "nullable" and value is not False:
                continue
            if key == "unique" and value is not True:
                continue
            if key == "max_null_rate":
                value = round(float(value), NULL_RATE_PLACES)
            kept[key] = copy.deepcopy(value)
        if kept:
            columns[name] = kept
    if columns:
        out["columns"] = columns
    if contract.get("required_columns"):
        out["required_columns"] = sorted(contract["required_columns"])
    if contract.get("allow_extra_columns") is False:
        out["allow_extra_columns"] = False
    return out


def expressible(contract: Mapping[str, Any]) -> dict[str, Any]:
    """The part of ``contract`` that dbt tests state: what :func:`contract_from_schema_yaml`
    returns from the tests alone (``use_meta=False``). The rest is :data:`NOT_EXPRESSIBLE`."""
    norm = normalize_contract(contract)
    if "tables" in norm:
        return {"tables": {k: expressible(v) for k, v in norm["tables"].items()}}
    columns: dict[str, Any] = {}
    for name, rules in norm.get("columns", {}).items():
        kept = {
            k: v
            for k, v in rules.items()
            if k not in NOT_EXPRESSIBLE and not (k in ("min", "max") and not _is_number(v))
        }
        if kept:
            columns[name] = kept
    out = {k: v for k, v in norm.items() if k != "columns"}
    if columns:
        out["columns"] = columns
    return out


def not_expressible(contract: Mapping[str, Any]) -> list[str]:
    """Where ``contract`` has a rule dbt tests cannot state, as ``column.rule`` (or
    ``table:column.rule``); these are carried as ``meta`` and not tested."""
    norm = normalize_contract(contract)
    if "tables" in norm:
        return [
            f"{t}:{item}" for t, sub in norm["tables"].items() for item in not_expressible(sub)
        ]
    out = []
    for name, rules in norm.get("columns", {}).items():
        for key, value in rules.items():
            if key in NOT_EXPRESSIBLE or (key in ("min", "max") and not _is_number(value)):
                out.append(f"{name}.{key}")
    return out


# ---- profile -> contract and bounds ---------------------------------------------------------


def _plain(tagged: Any) -> Any:
    """The profile stores a minimum or maximum type-tagged: ``["int", 5]``, and a decimal as
    text, ``["Decimal", "1.50"]``."""
    if not (isinstance(tagged, list) and len(tagged) == 2):
        return None
    if tagged[0] == "Decimal":
        try:
            return float(tagged[1])
        except (TypeError, ValueError):
            return None
    return tagged[1]


def _round_out(value: float, *, up: bool) -> float | int:
    """``value`` rounded away from the data to six significant digits."""
    if value == 0:
        return 0
    f = 10.0 ** (5 - math.floor(math.log10(abs(value))))
    r = (math.ceil if up else math.floor)(value * f) / f
    return int(r) if r == int(r) else r


def _widen(lo: float, hi: float, margin: float) -> tuple[float | int, float | int]:
    """``lo`` and ``hi`` moved apart by ``margin`` of their span; a floor of zero stays zero."""
    span = hi - lo
    low = lo - margin * span
    if lo >= 0:
        low = max(low, 0.0)
    return _round_out(low, up=False), _round_out(hi + margin * span, up=True)


def _column_contract(col: Mapping[str, Any], margin: float, null_slack: float) -> dict[str, Any]:
    rules: dict[str, Any] = {"dtype": col["dtype"]}
    if col.get("null_count") == 0:
        rules["nullable"] = False
    elif col.get("null_rate") is not None:
        rules["max_null_rate"] = round(min(1.0, float(col["null_rate"]) + null_slack), 6)
    # A small sample makes any column of names unique: only a key is stated as unique.
    if col.get("is_unique") and (col.get("is_primary_key") or _KEY_NAME.search(str(col.get("name")))):
        rules["unique"] = True
    lo, hi = _plain(col.get("min_value")), _plain(col.get("max_value"))
    if col["dtype"] in ("integer", "float") and _is_number(lo) and _is_number(hi):
        rules["min"], rules["max"] = _widen(lo, hi, margin)
        if col["dtype"] == "integer":
            rules["min"], rules["max"] = math.floor(rules["min"]), math.ceil(rules["max"])
    enum = col.get("enum_values")
    if col["dtype"] == "string" and col.get("is_enum") and isinstance(enum, dict) and enum:
        rules["allowed_values"] = sorted(enum)
    if col.get("distribution") and col["dtype"] == "float":
        rules["distribution"] = col["distribution"]
    return rules


def contract_from_profile(
    profile: Any, *, margin: float = 0.05, null_slack: float = 0.01
) -> dict[str, Any]:
    """A v1 contract that the profiled data satisfies: per column its dtype, ``nullable: false``
    where no value was null, otherwise ``max_null_rate`` (the observed rate plus ``null_slack``),
    ``unique``, numeric ``min`` / ``max`` widened by ``margin`` of the observed range, and the
    ``allowed_values`` of a text column with a complete value set. A dataset profile gives a
    ``tables`` contract. Row counts are left out (they are not a property of the shape)."""
    tables = {name: _table_contract(t, margin, null_slack) for name, t in _tables(profile).items()}
    if getattr(profile, "is_dataset", False):
        return {"tables": tables}
    return next(iter(tables.values()))


def _tables(profile: Any) -> dict[str, Any]:
    if getattr(profile, "is_dataset", False):
        return dict(profile.tables)
    data = profile.to_dict()
    return {str(data.get("name") or "table"): data}


def _table_contract(table: Mapping[str, Any], margin: float, null_slack: float) -> dict[str, Any]:
    cols = table["columns"]
    return {
        "columns": {n: _column_contract(c, margin, null_slack) for n, c in cols.items()},
        "required_columns": list(cols),
    }


def bounds_from_profile(
    profile: Any,
    *,
    mean_shift_std: float | None = None,
    std_ratio: tuple[float, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Distribution bounds per numeric column: ``mean`` within ``mean_shift_std`` standard
    deviations of the profiled mean, ``std`` within the ratios ``std_ratio`` of the profiled one
    and the quartiles within the same mean band. The defaults are the drift defaults
    (``mean_shift_std`` 0.5; ``std_ratio`` 0.67 to 1.5), so a dbt test fails where ``shape diff``
    would report a shift. A dataset profile gives ``{table: {column: bounds}}``."""
    k = DEFAULT_THRESHOLDS["mean_shift_std"] if mean_shift_std is None else mean_shift_std
    lo_r, hi_r = std_ratio or (
        DEFAULT_THRESHOLDS["std_ratio_min"],
        DEFAULT_THRESHOLDS["std_ratio_max"],
    )
    out: dict[str, Any] = {}
    for tname, table in _tables(profile).items():
        per_col: dict[str, Any] = {}
        for cname, col in table["columns"].items():
            mean, std = col.get("mean"), col.get("std")
            if col.get("dtype") not in ("integer", "float") or not _is_number(mean):
                continue
            if not _is_number(std) or std <= 0:
                continue
            if col.get("is_primary_key") or (col["dtype"] == "integer" and col.get("is_unique")):
                continue  # the mean of a key says nothing about the data
            quantiles = col.get("quantiles") or {}
            per_col[cname] = {
                "mean": [_r(mean - k * std), _r(mean + k * std)],
                "std": [_r(std * lo_r), _r(std * hi_r)],
                "quantiles": {
                    str(q): [_r(quantiles[key] - k * std), _r(quantiles[key] + k * std)]
                    for key, q in _QUANTILES.items()
                    if _is_number(quantiles.get(key))
                },
            }
        if per_col:
            out[tname] = per_col
    if getattr(profile, "is_dataset", False):
        return out
    return next(iter(out.values()), {})


def _r(value: float) -> float:
    return float(f"{value:.9g}")


# ---- compile -------------------------------------------------------------------------------


def _test(
    name: str, args: Mapping[str, Any] | None, *, args_style: str, tag: bool = True
) -> Any:
    """``not_null`` when it has nothing to say, else ``{name: {...}}``. Arguments sit under
    ``arguments:`` (dbt 1.10 and later) or inline (every dbt version)."""
    body: dict[str, Any] = {}
    if args:
        if args_style == "arguments":
            body["arguments"] = dict(args)
        else:
            body.update(args)
    if tag:
        body["config"] = {"tags": [_TAG]}
    return {name: body}


def _column_tests(
    rules: Mapping[str, Any], *, args_style: str, notes: list[str], where: str
) -> tuple[list[Any], dict[str, Any], set[str]]:
    tests: list[Any] = []
    meta: dict[str, Any] = {}
    packages: set[str] = set()

    def add(name: str, args: Mapping[str, Any] | None = None) -> None:
        tests.append(_test(name, args, args_style=args_style))
        if name.startswith("dbt_utils."):
            packages.add("dbt_utils")
        if name.startswith(_EXPECT):
            packages.add("dbt_expectations")

    if rules.get("nullable") is False:
        add("not_null")
    if rules.get("unique") is True:
        add("unique")
    if "allowed_values" in rules:
        values = list(rules["allowed_values"])
        add("accepted_values", {"values": values, "quote": not all(_is_number(v) for v in values)})
    bounds = {k: rules[k] for k in ("min", "max") if k in rules}
    numeric = {k: v for k, v in bounds.items() if _is_number(v)}
    for key in set(bounds) - set(numeric):
        notes.append(f"{where}.{key}: not a number, not compiled (kept as meta)")
        meta[key] = bounds[key]
    if numeric:
        args: dict[str, Any] = {"inclusive": True}
        if "min" in numeric:
            args["min_value"] = numeric["min"]
        if "max" in numeric:
            args["max_value"] = numeric["max"]
        add(_RANGE, args)
    if "max_null_rate" in rules:
        at_least = round(1 - float(rules["max_null_rate"]), NULL_RATE_PLACES)
        add(_NOT_NULL_SHARE, {"at_least": at_least})
    for key in NOT_EXPRESSIBLE:
        if key in rules:
            meta[key] = rules[key]
            notes.append(f"{where}.{key}: dbt tests cannot state it; kept as meta, not tested")
    return tests, meta, packages


def _relation_entry(
    name: str,
    contract: Mapping[str, Any],
    bounds: Mapping[str, Any] | None,
    *,
    tests_key: str,
    args_style: str,
    notes: list[str],
    packages: set[str],
) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": name}
    model_tests: list[Any] = []

    def add_model(test: Any, package: str | None = None) -> None:
        model_tests.append(test)
        if package:
            packages.add(package)

    rc = contract.get("row_count") or {}
    if rc:
        args = {f"{k}_value": rc[k] for k in ("min", "max") if k in rc}
        add_model(_test(_ROW_COUNT, args, args_style=args_style), "dbt_expectations")
    required = list(contract.get("required_columns") or [])
    columns = dict(contract.get("columns") or {})
    if contract.get("allow_extra_columns") is False:
        known = list(dict.fromkeys([*required, *columns]))
        add_model(
            _test(_MATCH_SET, {"column_list": known}, args_style=args_style), "dbt_expectations"
        )
    if model_tests:
        entry[tests_key] = model_tests
    cols: list[dict[str, Any]] = []
    for cname in dict.fromkeys([*required, *columns, *(bounds or {})]):
        tests, meta, used = _column_tests(
            columns.get(cname, {}), args_style=args_style, notes=notes, where=f"{name}.{cname}"
        )
        packages |= used
        if cname in required:
            tests.append(_test(_EXISTS, None, args_style=args_style))
            packages.add("dbt_expectations")
        tests += _bound_tests((bounds or {}).get(cname), args_style, packages)
        col: dict[str, Any] = {"name": cname}
        if tests:
            col[tests_key] = tests
        if meta:
            col["meta"] = {"shape": meta}
        if len(col) > 1:
            cols.append(col)
    if cols:
        entry["columns"] = cols
    return entry


def _bound_tests(bounds: Mapping[str, Any] | None, args_style: str, packages: set[str]) -> list[Any]:
    if not bounds:
        return []
    out: list[Any] = []
    packages.add("dbt_expectations")
    if "mean" in bounds:
        lo, hi = bounds["mean"]
        out.append(_test(_MEAN, {"min_value": lo, "max_value": hi}, args_style=args_style))
    if "std" in bounds:
        lo, hi = bounds["std"]
        out.append(_test(_STDEV, {"min_value": lo, "max_value": hi}, args_style=args_style))
    for q, (lo, hi) in (bounds.get("quantiles") or {}).items():
        args = {"quantile": float(q), "min_value": lo, "max_value": hi}
        out.append(_test(_QUANTILE, args, args_style=args_style))
    return out


class Compiled:
    """The result of :func:`compile_tests`: the ``schema.yml`` document (``doc``), what dbt
    cannot test (``notes``) and the packages it needs (``packages``: ``dbt_utils``,
    ``dbt_expectations``)."""

    def __init__(self, doc: dict[str, Any], notes: list[str], packages: set[str]) -> None:
        self.doc = doc
        self.notes = notes
        self.packages = packages

    def yaml(self) -> str:
        return render_yaml(self.doc, self.packages)

    def packages_yml(self) -> str:
        return packages_yaml(self.packages)


def compile_tests(
    contract: Mapping[str, Any],
    *,
    model: str | None = None,
    kind: str = "models",
    source_name: str = "raw",
    bounds: Mapping[str, Any] | None = None,
    tests_key: str = "data_tests",
    args_style: str = "arguments",
) -> Compiled:
    """A contract (one table: give ``model``; several: its ``tables`` object) as a ``schema.yml``
    document.

    ``kind`` says where the tests go: ``models``, ``seeds`` or ``sources`` (under
    ``source_name``). ``bounds`` is :func:`bounds_from_profile`'s result. ``tests_key`` is
    ``data_tests`` (dbt 1.8 and later) or ``tests``; ``args_style`` is ``arguments`` (dbt 1.10
    and later) or ``inline`` (every version)."""
    if kind not in ("models", "seeds", "sources"):
        raise DbtProjectError(f"kind must be models, seeds or sources, not {kind!r}")
    if args_style not in ("arguments", "inline"):
        raise DbtProjectError(f"args_style must be arguments or inline, not {args_style!r}")
    notes: list[str] = []
    packages: set[str] = set()
    if "tables" in contract:
        per_table = dict(contract["tables"])
        per_bounds = dict(bounds or {})
    else:
        if not model:
            raise DbtProjectError("a contract for one table needs the name of the dbt model")
        per_table = {model: contract}
        per_bounds = {model: dict(bounds or {})}
    entries = [
        _relation_entry(
            name,
            sub,
            per_bounds.get(name),
            tests_key=tests_key,
            args_style=args_style,
            notes=notes,
            packages=packages,
        )
        for name, sub in per_table.items()
    ]
    doc: dict[str, Any] = {"version": 2}
    if kind == "sources":
        doc["sources"] = [{"name": source_name, "tables": entries}]
    else:
        doc[kind] = entries
    return Compiled(doc, notes, packages)


def _test_key(test: Any) -> tuple[str, str]:
    """A test's name and arguments, without its config: ``not_null`` and ``{not_null: {config:
    ...}}`` are one test, and so are an inline argument and the same one under ``arguments``."""
    import json

    if isinstance(test, str):
        return test, "{}"
    (name, body), = test.items()
    args: dict[str, Any] = {}
    if isinstance(body, Mapping):
        args = {k: v for k, v in body.items() if k not in ("config", "arguments", "name")}
        args.update(body.get("arguments") or {})
    return str(name), json.dumps(args, sort_keys=True, default=str)


def _same_test(a: Any, b: Any) -> bool:
    return _test_key(a) == _test_key(b)


def _merge_tests(base: dict[str, Any], extra: Mapping[str, Any], key: str) -> None:
    present = base.get("data_tests") if "data_tests" in base else base.get("tests")
    target_key = "data_tests" if "data_tests" in base else ("tests" if "tests" in base else key)
    merged = list(present or [])
    for t in extra.get(key) or []:
        if not any(_same_test(t, m) for m in merged):
            merged.append(copy.deepcopy(t))
    if merged:
        base[target_key] = merged


def _merge_entry(base: dict[str, Any], extra: Mapping[str, Any], key: str) -> None:
    _merge_tests(base, extra, key)
    cols: list[dict[str, Any]] = base.setdefault("columns", [])
    by_name = {c.get("name"): c for c in cols}
    for ec in extra.get("columns") or []:
        target = by_name.get(ec["name"])
        if target is None:
            cols.append(copy.deepcopy(ec))
            continue
        _merge_tests(target, ec, key)
        if ec.get("meta"):
            target.setdefault("meta", {}).update(copy.deepcopy(ec["meta"]))
    if not cols:
        del base["columns"]


def merge_schema_docs(
    base: Mapping[str, Any], compiled: Mapping[str, Any], *, tests_key: str = "data_tests"
) -> dict[str, Any]:
    """``compiled`` tests added to the entries of an existing ``schema.yml`` document (dbt allows
    one entry per model, seed or source table, so tests for a model that is already described
    cannot go in a second file). A test that is already there is not added twice; the key the
    file uses for its tests (``tests`` or ``data_tests``) is kept. The result is a new document;
    comments in a file written back from it are lost."""
    out = copy.deepcopy(dict(base))
    out.setdefault("version", 2)
    for section in ("models", "seeds", "snapshots"):
        for entry in compiled.get(section) or []:
            items: list[dict[str, Any]] = out.setdefault(section, [])
            target = next((i for i in items if i.get("name") == entry["name"]), None)
            if target is None:
                items.append(copy.deepcopy(entry))
            else:
                _merge_entry(target, entry, tests_key)
    for src in compiled.get("sources") or []:
        sources: list[dict[str, Any]] = out.setdefault("sources", [])
        base_src = next((s for s in sources if s.get("name") == src["name"]), None)
        if base_src is None:
            sources.append(copy.deepcopy(src))
            continue
        tables: list[dict[str, Any]] = base_src.setdefault("tables", [])
        for entry in src.get("tables") or []:
            target = next((t for t in tables if t.get("name") == entry["name"]), None)
            if target is None:
                tables.append(copy.deepcopy(entry))
            else:
                _merge_entry(target, entry, tests_key)
    return out


def render_yaml(doc: Mapping[str, Any], packages: Iterable[str] = ()) -> str:
    import yaml

    header = [
        "# Generated by `shape to-dbt-tests`. Edit the contract and compile again.",
        "# Tags: every test carries the tag `shape` (`dbt test --select tag:shape`).",
    ]
    names = sorted(packages)
    if names:
        header.append("# Needs these dbt packages (see packages.yml): " + ", ".join(names))
    body = yaml.safe_dump(dict(doc), sort_keys=False, default_flow_style=False, width=100)
    return "\n".join(header) + "\n" + body


def packages_yaml(packages: Iterable[str]) -> str:
    import yaml

    wanted = set(packages)
    entries = []
    if "dbt_utils" in wanted:
        entries.append(DBT_UTILS)
    if "dbt_expectations" in wanted:
        entries.append(DBT_EXPECTATIONS)
    return yaml.safe_dump({"packages": entries}, sort_keys=False)


# ---- read tests back -------------------------------------------------------------------------


def _test_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return dict(args)


def _read_column(
    tests: Iterable[Any], meta: Mapping[str, Any] | None, *, use_meta: bool
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
    rules: dict[str, Any] = {}
    exists = False
    bound: dict[str, Any] = {}
    for t in tests:
        kind, args = t.kind, _test_args(t.args)
        if kind == "not_null":
            rules["nullable"] = False
        elif kind == "unique":
            rules["unique"] = True
        elif kind == "accepted_values":
            rules["allowed_values"] = list(args.get("values") or [])
        elif kind == _RANGE:
            for src, dst in (("min_value", "min"), ("max_value", "max")):
                if src in args:
                    rules[dst] = args[src]
        elif kind == _NOT_NULL_SHARE:
            rules["max_null_rate"] = round(1 - float(args.get("at_least", 1.0)), NULL_RATE_PLACES)
        elif kind == _EXISTS:
            exists = True
        elif kind == _MEAN:
            bound["mean"] = [args["min_value"], args["max_value"]]
        elif kind == _STDEV:
            bound["std"] = [args["min_value"], args["max_value"]]
        elif kind == _QUANTILE:
            bound.setdefault("quantiles", {})[str(float(args["quantile"]))] = [
                args["min_value"],
                args["max_value"],
            ]
    if use_meta and meta:
        shape_meta = meta.get("shape")
        if isinstance(shape_meta, Mapping):
            for key, value in shape_meta.items():
                rules.setdefault(key, value)
    return rules, exists, bound


def _read_relation(rel: DbtRelation, *, use_meta: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    contract: dict[str, Any] = {}
    bounds: dict[str, Any] = {}
    columns: dict[str, Any] = {}
    required: list[str] = []
    for t in rel.tests:
        args = _test_args(t.args)
        if t.kind == _ROW_COUNT:
            contract["row_count"] = {
                k: args[f"{k}_value"] for k in ("min", "max") if f"{k}_value" in args
            }
        elif t.kind == _MATCH_SET:
            contract["allow_extra_columns"] = False
    for cname, col in rel.columns.items():
        rules, exists, bound = _read_column(col.tests, col.meta, use_meta=use_meta)
        if rules:
            columns[cname] = rules
        if exists:
            required.append(cname)
        if bound:
            bounds[cname] = bound
    if columns:
        contract["columns"] = columns
    if required:
        contract["required_columns"] = sorted(required)
    return contract, bounds


def contract_from_schema_yaml(
    text: str, *, use_meta: bool = True
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The inverse of :func:`compile_tests`: ``schema.yml`` text to ``(contracts, bounds)``, both
    keyed by relation name (a model, a seed or a source table). ``use_meta`` also reads the
    :data:`NOT_EXPRESSIBLE` rules from ``meta.shape``; without it the result is what the tests
    alone state, which is :func:`expressible` of the original contract. ``bounds`` are the
    distribution-bounds tests (the contract format has no place for them)."""
    contracts: dict[str, Any] = {}
    bounds: dict[str, Any] = {}
    for rel in read_schema_yaml(text):
        contracts[rel.name], bound = _read_relation(rel, use_meta=use_meta)
        if bound:
            bounds[rel.name] = bound
    return contracts, bounds


def contract_from_dbt_tests(text: str, *, model: str | None = None, use_meta: bool = True) -> Any:
    """:func:`contract_from_schema_yaml` as one contract: the ``model``'s, or, for several
    relations, a ``tables`` contract."""
    contracts, _ = contract_from_schema_yaml(text, use_meta=use_meta)
    if model is not None:
        if model not in contracts:
            raise DbtProjectError(f"no model {model!r} in the tests (has {sorted(contracts)})")
        return contracts[model]
    if len(contracts) == 1:
        return next(iter(contracts.values()))
    return {"tables": contracts}
