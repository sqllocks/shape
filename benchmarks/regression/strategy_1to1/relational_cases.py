"""The cases of the P4-04d equivalence tests: multi-table schemas, in the dumped-schema format,
for the foreign-key, hierarchy, group and lifecycle strategies, and what is observed of each.

Standard library only (plus ``cases.py`` next door), so the baseline venv and the Shape venv build
the same schemas and compute the same observations. ``observe`` takes the generated tables as
``{table: {column: list}}`` and returns ``{name: list}``: each list is fingerprinted
(``fingerprint.py``) and compared under T-21 clauses (b)-(e) (``compare.py``). Foreign keys are
observed as the *row number of the parent they point at* (a key value alone, such as a UUID,
means nothing across seeds), and fan-out (children per parent, zeros included) is one of them
(clause (f)). ``invariants`` are facts that must hold exactly, for every seed, in both tools.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import cases as base

CHILD_ROWS = 60_000
PARENT_ROWS = 3_000
Tables = Mapping[str, Mapping[str, Sequence[Any]]]


def _null(v: Any) -> bool:
    return v is None or bool(v != v)  # None, NaN and NaT


def _table(rows: int, pk: list[str], columns: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"rows": rows, "pk": pk, "columns": columns}


def _id(name: str = "id", start: int = 1) -> dict[str, Any]:
    return base._col(name, "integer", {"strategy": "sequence", "start": start})


def _fk(name: str, ref: str, **gen: Any) -> dict[str, Any]:
    extra = {k: gen.pop(k) for k in ("nullable", "null_rate") if k in gen}
    return base._col(name, "integer", {"strategy": "foreign_key", "ref": ref, **gen}, **extra)


def _parent(rows: int = PARENT_ROWS, name: str = "p", key: str = "pid") -> dict[str, Any]:
    return {name: _table(rows, [key], {key: _id(key)})}


def schema_for(case: Mapping[str, Any], seed: int = 42) -> dict[str, Any]:
    """The multi-table schema (dumped format) of a case."""
    tables = {
        name: {
            "name": name,
            "primary_key": spec["pk"],
            "columns": spec["columns"],
        }
        for name, spec in case["tables"].items()
    }
    return {
        "model": {
            "name": "relational_case",
            "description": "",
            "domain": "relational_case",
            "schema_mode": "3nf",
            "locale": "en_US",
            "seed": seed,
            "date_range": case.get("date_range", {"start": "2022-01-01", "end": "2025-12-31"}),
        },
        "tables": tables,
        "relationships": [],
        "business_rules": [],
        "generation": {
            "scale": "s",
            "scales": {"s": {n: spec["rows"] for n, spec in case["tables"].items()}},
            "derived_counts": {},
        },
        "correlated_columns": {},
    }


# ------------------------------------------------------------------------------ observers


def _index_of(keys: Sequence[Any]) -> dict[Any, int]:
    return {k: i for i, k in enumerate(keys)}


def _fan_out(parent_index: Sequence[int | None], parents: int) -> list[int]:
    counts = Counter(i for i in parent_index if i is not None)
    return [counts.get(i, 0) for i in range(parents)]


def observe_fk(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The parent row number of each child row, and the children per parent."""
    where = _index_of(tables[o["parent"]][o["pk"]])
    values = tables[o["child"]][o["column"]]
    index = [None if _null(v) else where.get(v, -1) for v in values]
    return {"x": index, "fanout": _fan_out(index, len(where))}


def invariants_fk(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    where = _index_of(tables[o["parent"]][o["pk"]])
    values = [v for v in tables[o["child"]][o["column"]] if not _null(v)]
    out: dict[str, Any] = {"integrity": all(v in where for v in values)}
    if "max_per_parent" in o:
        out["max_per_parent"] = max(Counter(values).values()) <= o["max_per_parent"]
    if o.get("unique"):
        out["unique"] = len(set(values)) == len(values)
    return out


def invariants_constrained(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    owner = dict(zip(tables["addr"]["aid"], tables["addr"]["cid"], strict=True))
    child = tables["ord"]
    pairs = [(a, c) for a, c in zip(child["aid"], child["cid"], strict=True) if not _null(a)]
    has_address = set(tables["addr"]["cid"])
    unmatched = [c for a, c in zip(child["aid"], child["cid"], strict=True) if _null(a)]
    return {
        "integrity": all(a in owner for a, _ in pairs),
        "belongs_to_customer": all(owner[a] == c for a, c in pairs),
        "null_only_without_address": all(c not in has_address for c in unmatched),
    }


def observe_constrained(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    child = tables["ord"]
    where = _index_of(tables["addr"]["aid"])
    index = [None if _null(v) else where[v] for v in child["aid"]]
    return {"x": index, "fanout": _fan_out(index, len(where))}


def observe_cfk(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    parent = tables["line"]
    where = _index_of(list(zip(parent["a"], parent["b"], strict=True)))
    child = tables["ret"]
    index = [where.get(k, -1) for k in zip(child["a"], child["b"], strict=True)]
    return {"x": index, "fanout": _fan_out(index, len(where))}


def invariants_cfk(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    parent, child = tables["line"], tables["ret"]
    pairs = set(zip(parent["a"], parent["b"], strict=True))
    return {"integrity": all(k in pairs for k in zip(child["a"], child["b"], strict=True))}


def observe_first(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    child = tables["c"]
    return {"flag": [bool(v) for v in child["flag"]], **observe_fk(tables, o)}


def invariants_first(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    child = tables["c"]
    default = o["default"]
    seen: set[Any] = set()
    ok = True
    for parent, flag in zip(child["cid"], child["flag"], strict=True):
        ok = ok and (bool(flag) == default) == (parent not in seen)
        seen.add(parent)
    return {"first_row_marked": ok}


def observe_hierarchy(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    cat = tables["cat"]
    where = _index_of(cat["category_id"])
    parent = [None if _null(v) else where[v] for v in cat["parent_category_id"]]
    return {
        "is_root": [p is None for p in parent],
        "level": list(cat["level"]),
        "parent_position": [p / len(where) for p in parent if p is not None],
        "fanout": _fan_out(parent, len(where)),
    }


def invariants_hierarchy(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    cat = tables["cat"]
    level_of = dict(zip(cat["category_id"], cat["level"], strict=True))
    pairs = list(zip(cat["parent_category_id"], cat["level"], strict=True))
    return {
        "parent_is_one_level_up": all(
            _null(p) == (lv == 1) and (_null(p) or level_of[p] == lv - 1) for p, lv in pairs
        ),
        "levels_in_range": set(cat["level"]) <= set(range(1, o["levels"] + 1)),
        "root_rows": sum(1 for v in cat["level"] if v == 1) == o["roots"],
    }


def observe_lifecycle(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    return {"x": list(tables["t"]["x"])}


def invariants_lifecycle(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    values = tables["t"]["x"]
    return {
        "labels_are_strings": all(isinstance(v, str) for v in values),
        "only_known_labels": set(values) <= set(o["labels"]),
    }


def observe_scd2(tables: Tables, o: Mapping[str, Any]) -> dict[str, list[Any]]:
    dim = tables["dim"]
    sizes = Counter(dim["cust"])
    return {
        "effective": [None if _null(v) else v for v in dim["effective_date"]],
        "ended": [None if _null(v) else v for v in dim["end_date"]],
        "is_current": [bool(v) for v in dim["is_current"]],
        "version": list(dim["version"]),
        "versions_per_key": list(sizes.values()),
    }


def invariants_scd2(tables: Tables, o: Mapping[str, Any]) -> dict[str, Any]:
    dim = tables["dim"]
    groups: dict[Any, list[int]] = {}
    for i, key in enumerate(dim["cust"]):
        groups.setdefault(key, []).append(i)
    gap = o["min_gap_days"]
    ordered = latest = chained = numbered = True
    for rows in groups.values():
        rows.sort(key=lambda i: dim["effective_date"][i])
        for v, i in enumerate(rows):
            numbered = numbered and dim["version"][i] == v + 1
            latest = latest and bool(dim["is_current"][i]) == (v == len(rows) - 1)
            if v + 1 < len(rows):
                nxt = dim["effective_date"][rows[v + 1]]
                chained = (
                    chained
                    and not _null(dim["end_date"][i])
                    and nxt - dim["end_date"][i] == _gap(gap)
                )
                ordered = ordered and dim["effective_date"][i] <= nxt
            else:
                chained = chained and _null(dim["end_date"][i])
    return {
        "versions_numbered": numbered,
        "one_current_per_key": latest,
        "end_is_next_start_minus_gap": chained,
        "dates_do_not_decrease": ordered,
    }


def _gap(days: int) -> Any:
    import datetime as dt

    return dt.timedelta(days=days)


Observer = Callable[[Tables, Mapping[str, Any]], dict[str, list[Any]]]
Invariants = Callable[[Tables, Mapping[str, Any]], dict[str, Any]]

OBSERVERS: dict[str, tuple[Observer, Invariants]] = {
    "fk": (observe_fk, invariants_fk),
    "constrained": (observe_constrained, invariants_constrained),
    "cfk": (observe_cfk, invariants_cfk),
    "first": (observe_first, invariants_first),
    "hierarchy": (observe_hierarchy, invariants_hierarchy),
    "lifecycle": (observe_lifecycle, invariants_lifecycle),
    "scd2": (observe_scd2, invariants_scd2),
}


def observe(case: Mapping[str, Any], tables: Tables) -> dict[str, list[Any]]:
    return OBSERVERS[case["observer"]][0](tables, case["observe"])


def invariants(case: Mapping[str, Any], tables: Tables) -> dict[str, Any]:
    return OBSERVERS[case["observer"]][1](tables, case["observe"])


# ------------------------------------------------------------------------------ cases


def _case(
    strategy: str,
    observer: str,
    tables: dict[str, dict[str, Any]],
    observe_: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "observer": observer,
        "tables": tables,
        "observe": observe_,
        **extra,
    }


def _fk_child(**gen: Any) -> dict[str, dict[str, Any]]:
    cols = {"id": _id(), "x": _fk("x", "p.pid", **gen)}
    return {**_parent(), "c": _table(CHILD_ROWS, ["id"], cols)}


_OBS_FK = {"child": "c", "column": "x", "parent": "p", "pk": "pid"}


def _fk_cases() -> dict[str, dict[str, Any]]:
    def case(name: str, observe_: dict[str, Any] | None = None, **gen: Any) -> None:
        out[f"foreign_key/{name}"] = _case(
            "foreign_key", "fk", _fk_child(**gen), {**_OBS_FK, **(observe_ or {})}
        )

    out: dict[str, dict[str, Any]] = {}
    case("uniform")
    case("zipf_top_level_alpha", distribution="zipf", alpha=1.2)
    case("zipf_params_alpha", distribution="zipf", params={"alpha": 1.5})
    case("pareto", distribution="pareto", alpha=1.2)
    case("pareto_default_alpha", distribution="pareto")
    case(
        "pareto_capped", {"max_per_parent": 40}, distribution="pareto", alpha=1.2, max_per_parent=40
    )
    case("nullable", nullable=True, null_rate=0.1)
    case("unknown_distribution_is_uniform", distribution="gaussian")

    uuid_parent = {
        "p": _table(PARENT_ROWS, ["pid"], {"pid": base._col("pid", "string", {"strategy": "uuid"})})
    }
    string_child = {
        "id": _id(),
        "x": base._col("x", "string", {"strategy": "foreign_key", "ref": "p.pid"}),
    }
    out["foreign_key/uuid_keys"] = _case(
        "foreign_key",
        "fk",
        {**uuid_parent, "c": _table(CHILD_ROWS, ["id"], string_child)},
        dict(_OBS_FK),
    )

    employees = {
        "e": _table(
            PARENT_ROWS,
            ["eid"],
            {
                "eid": _id("eid"),
                "mgr": _fk("mgr", "e.eid", nullable=True, null_rate=0.05),
            },
        )
    }
    out["foreign_key/self_reference"] = _case(
        "foreign_key", "fk", employees, {"child": "e", "column": "mgr", "parent": "e", "pk": "eid"}
    )

    customers = {"cust": _table(1_000, ["cid"], {"cid": _id("cid")})}
    addresses = {"addr": _table(2_500, ["aid"], {"aid": _id("aid"), "cid": _fk("cid", "cust.cid")})}
    orders = {
        "ord": _table(
            CHILD_ROWS,
            ["oid"],
            {
                "oid": _id("oid"),
                "cid": _fk("cid", "cust.cid", distribution="zipf", alpha=1.2),
                "aid": _fk("aid", "addr.aid", constrained_by="cid", nullable=True),
            },
        )
    }
    out["foreign_key/constrained_by"] = _case(
        "foreign_key", "constrained", {**customers, **addresses, **orders}, {}
    )

    sampled = {
        **_parent(),
        "c": _table(
            int(PARENT_ROWS * 0.1),
            ["id"],
            {"id": _id(), "x": _fk("x", "p.pid", sample_rate=0.1)},
        ),
    }
    out["foreign_key/sample_rate"] = _case(
        "foreign_key", "fk", sampled, {**_OBS_FK, "unique": True}
    )
    return out


def _composite_cases() -> dict[str, dict[str, Any]]:
    line = _table(
        PARENT_ROWS,
        ["a", "b"],
        {
            "a": _id("a", 1000),
            "b": base._col("b", "integer", {"strategy": "sequence", "start": 7, "step": 2}),
        },
    )

    def case(name: str, **gen: Any) -> dict[str, Any]:
        ret = _table(
            CHILD_ROWS,
            ["id"],
            {
                "id": _id(),
                "a": base._col(
                    "a",
                    "integer",
                    {
                        "strategy": "composite_foreign_key",
                        "ref_table": "line",
                        "ref_columns": ["a", "b"],
                        **gen,
                    },
                ),
                "b": base._col(
                    "b",
                    "integer",
                    {"strategy": "composite_fk_field", "source_column": "a", "ref_column": "b"},
                ),
            },
        )
        return _case("composite_foreign_key", "cfk", {"line": line, "ret": ret}, {})

    return {
        "composite_foreign_key/uniform": case("uniform"),
        "composite_foreign_key/zipf": case("zipf", distribution="zipf", params={"alpha": 1.3}),
        "composite_foreign_key/zipf_default_alpha": case("zipf", distribution="zipf"),
        "composite_foreign_key/unknown_distribution_is_uniform": case("x", distribution="pareto"),
    }


def _first_cases() -> dict[str, dict[str, Any]]:
    def case(name: str, default: bool | None, **gen: Any) -> None:
        flag: dict[str, Any] = {"strategy": "first_per_parent", "parent_column": "cid"}
        if default is not None:
            flag["default"] = default
        cols = {
            "id": _id(),
            "cid": _fk("cid", "p.pid", **gen),
            "flag": base._col("flag", "boolean", flag),
        }
        out[f"first_per_parent/{name}"] = _case(
            "first_per_parent",
            "first",
            {**_parent(), "c": _table(CHILD_ROWS, ["id"], cols)},
            {
                "child": "c",
                "column": "cid",
                "parent": "p",
                "pk": "pid",
                "default": default is not False,
            },
        )

    out: dict[str, dict[str, Any]] = {}
    case("default_true", None)
    case("explicit_true", True)
    case("default_false", False)
    case("zipf_parents", True, distribution="zipf", alpha=1.5)
    return out


def _hierarchy_cases() -> dict[str, dict[str, Any]]:
    def case(name: str, rows: int, levels: int | None, roots: int | None, **gen: Any) -> None:
        spec: dict[str, Any] = {"strategy": "self_referencing", "pk_column": "category_id"}
        if levels is not None:
            spec["levels" if "alias" not in gen else "max_depth"] = levels
        if roots is not None:
            spec["root_count"] = roots
        cols = {
            "category_id": _id("category_id"),
            "parent_category_id": base._col("parent_category_id", "integer", spec, nullable=True),
            "level": base._col(
                "level", "integer", {"strategy": "self_ref_field", "field": "level"}
            ),
        }
        depth = levels if levels is not None else 3
        n_roots = max(1, min(roots if roots is not None else max(1, rows // 10), rows // depth))
        if depth == 1:
            n_roots = rows  # a single level holds every row
        out[f"self_referencing/{name}"] = _case(
            "self_referencing",
            "hierarchy",
            {"cat": _table(rows, ["category_id"], cols)},
            {"levels": depth, "roots": n_roots},
        )

    out: dict[str, dict[str, Any]] = {}
    case("levels3_roots8", 2_000, 3, 8)
    case("max_depth_alias", 2_000, 2, 8, alias=True)
    case("defaults", 3_000, None, None)
    case("deep_levels5_roots20", 5_000, 5, 20)
    case("one_level", 1_000, 1, 5)
    case("tiny", 25, 3, 8)
    return out


def _lifecycle_cases() -> dict[str, dict[str, Any]]:
    def case(name: str, spec: dict[str, Any], labels: list[str]) -> None:
        cols = {"id": _id(), "x": base._col("x", "string", {"strategy": "lifecycle", **spec})}
        out[f"lifecycle/{name}"] = _case(
            "lifecycle", "lifecycle", {"t": _table(CHILD_ROWS, ["id"], cols)}, {"labels": labels}
        )

    out: dict[str, dict[str, Any]] = {}
    phases = {"introduced": 0.10, "active": 0.75, "discontinued": 0.15}
    case("three_phases", {"phases": phases}, list(phases))
    case("values_alias", {"values": {"a": 3, "b": 1}}, ["a", "b"])
    case("zero_weight", {"phases": {"on": 2, "off": 0, "idle": 1}}, ["on", "off", "idle"])
    case(
        "numeric_labels_stay_strings", {"phases": {"1": 0.5, "2": 0.3, "10": 0.2}}, ["1", "2", "10"]
    )
    case(
        "many",
        {"phases": {f"p{i:02d}": 1.0 / (i + 1) for i in range(40)}},
        [f"p{i:02d}" for i in range(40)],
    )
    return out


def _scd2_cases() -> dict[str, dict[str, Any]]:
    def case(name: str, gap: int | None, window: dict[str, str] | None = None, **fk: Any) -> None:
        extra = {} if gap is None else {"min_gap_days": gap}
        key = {"strategy": "scd2", "business_key": "cust", **extra}
        cols = {
            "id": _id(),
            "cust": _fk("cust", "customer.cid", **fk),
            "effective_date": base._col(
                "effective_date", "date", {**key, "role": "effective_date"}
            ),
            "end_date": base._col("end_date", "date", {**key, "role": "end_date"}, nullable=True),
            "is_current": base._col("is_current", "boolean", {**key, "role": "is_current"}),
            "version": base._col("version", "integer", {**key, "role": "version"}),
        }
        tables = {
            "customer": _table(1_500, ["cid"], {"cid": _id("cid")}),
            "dim": _table(6_000, ["id"], cols),
        }
        out[f"scd2/{name}"] = _case(
            "scd2",
            "scd2",
            tables,
            {"min_gap_days": 1 if gap is None else gap},
            **({"date_range": window} if window else {}),
        )

    out: dict[str, dict[str, Any]] = {}
    case("default_gap", None)
    case("gap_7_days", 7)
    case("zero_gap", 0)
    case("skewed_business_keys", 1, distribution="zipf", alpha=1.3)
    case("short_window", 2, {"start": "2024-01-01", "end": "2024-03-01"})
    return out


CASES: dict[str, dict[str, Any]] = {
    **_fk_cases(),
    **_composite_cases(),
    **_first_cases(),
    **_hierarchy_cases(),
    **_lifecycle_cases(),
    **_scd2_cases(),
}


def strategies() -> list[str]:
    return sorted({c["strategy"] for c in CASES.values()})
