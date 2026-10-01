"""The cases of the P4-04b equivalence tests: ``faker``, ``native``, ``formula``, ``derived`` and
``computed``.

Unlike ``cases.py`` (one table, one column under test), a case here is a whole small schema in the
dumped-schema format, so the baseline and Shape generate exactly the same configuration, and it
says what to measure:

* ``{"kind": "values"}``: the values of ``target`` (a column ``[table, column]``);
* ``{"kind": "day_offset", "source": column}``: the whole days from a date column of the same table
  to ``target``. The date column is a helper whose generator is the baseline's own ``temporal``
  strategy; Shape generates it with a test stand-in (``shape_generators``), because the quantity
  under test is what ``derived`` adds, not where the dates come from.

``parts`` names the components of pooled text (``parts.py``). Standard library only.
"""

from __future__ import annotations

from typing import Any

from cases import _col  # type: ignore[import-not-found]

ROWS = 60_000
PARENT_ROWS = 3_000
SMALL_PARENTS = 3_000
TABLE = "t"
TARGET = "x"

_FIRST = r"(?P<first>[^ .@]+)"
_CHARS = {
    "regex": r"(?P<v>[a-z0-9]{12})",
    "groups": {"v": ["chars", "abcdefghijklmnopqrstuvwxyz0123456789"]},
}


def _pool(name: str, transform: str = "none") -> list[str]:
    return ["pool", name, transform]


def _case(
    strategy: str,
    target: dict[str, Any],
    *,
    helpers: dict[str, dict[str, Any]] | None = None,
    measure: dict[str, Any] | None = None,
    parts: dict[str, Any] | None = None,
    shape_generators: dict[str, dict[str, Any]] | None = None,
    regex: str | None = None,
    rows: int = ROWS,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "tables": {
            TABLE: {
                "rows": rows,
                "primary_key": ["id"],
                "columns": {
                    "id": _col("id", "integer", {"strategy": "sequence", "start": 1}),
                    **(helpers or {}),
                    TARGET: target,
                },
            }
        },
        "relationships": [],
        "target": [TABLE, TARGET],
        "measure": measure or {"kind": "values"},
        "parts": parts,
        "shape_generators": shape_generators or {},
        "regex": regex,
    }


def _text(strategy: str, provider: str, **extra: Any) -> dict[str, Any]:
    gen = {"strategy": strategy, "provider": provider}
    max_length = extra.pop("max_length", None)
    column = _col(TARGET, "string", gen, **extra)
    column["max_length"] = max_length
    return column


def _providers() -> dict[str, dict[str, Any]]:
    """Every built-in provider, under both strategy names."""
    specs: dict[str, dict[str, Any]] = {
        "first_name": {"parts": {"regex": r"(?P<v>\S+)", "groups": {"v": _pool("first_names")}}},
        "last_name": {"parts": {"regex": r"(?P<v>\S+)", "groups": {"v": _pool("last_names")}}},
        "name": {
            "parts": {
                "regex": r"(?P<first>\S+) (?P<last>\S+)",
                "groups": {"first": _pool("first_names"), "last": _pool("last_names")},
            }
        },
        "email": {
            "parts": {
                "regex": _FIRST + r"\.(?P<last>[^ .@\d]+)(?P<n>\d+)@(?P<domain>[^ @]+)",
                "groups": {
                    "first": _pool("first_names", "slug"),
                    "last": _pool("last_names", "slug"),
                    "n": ["int"],
                    "domain": _pool("email_domains"),
                },
            }
        },
        "phone_number": {
            "parts": {
                "regex": r"\((?P<area>\d{3})\) (?P<exchange>\d{3})-(?P<subscriber>\d{4})",
                "groups": {"area": ["int"], "exchange": ["int"], "subscriber": ["int"]},
            }
        },
        "ssn": {
            "parts": {
                "regex": r"(?P<area>\d{3})-(?P<group>\d{2})-(?P<serial>\d{4})",
                "groups": {"area": ["int"], "group": ["int"], "serial": ["int"]},
            }
        },
        "company": {"parts": {"regex": r"(?P<v>.+)", "groups": {"v": _pool("company_names")}}},
        "street_address": {
            "parts": {
                "regex": r"(?P<number>\d+) (?P<street>.+) (?P<suffix>\w+)",
                "groups": {
                    "number": ["int"],
                    "street": _pool("street_names"),
                    "suffix": _pool("street_suffixes"),
                },
            }
        },
        "sentence": {"parts": {"regex": r"(?P<v>.+)", "groups": {"v": _pool("sentences")}}},
        "city": {"parts": {"regex": r"(?P<v>.+)", "groups": {"v": _pool("us_cities")}}},
        "state_abbr": {"parts": {"regex": r"(?P<v>.+)", "groups": {"v": _pool("us_states")}}},
        "uri": {
            "parts": {
                "regex": r"https://(?P<domain>[^/]+)/(?P<path>.+)",
                "groups": {"domain": _pool("uri_domains"), "path": _pool("uri_paths")},
            }
        },
        "company_email": {
            "parts": {
                "regex": r"(?P<first>[^.@]+)\.(?P<last>[^.@]+)@(?P<stem>[^@]+)\.com",
                "groups": {
                    "first": _pool("first_names", "slug"),
                    "last": _pool("last_names", "slug"),
                    "stem": _pool("company_names", "company_stem"),
                },
            }
        },
        "pystr": {"parts": _CHARS},
        "word": {"parts": _CHARS},
    }
    out: dict[str, dict[str, Any]] = {}
    for strategy in ("native", "faker"):
        for provider, extra in specs.items():
            regex = r"[a-z0-9]{12}" if provider in ("pystr", "word") else None
            out[f"{strategy}/{provider}"] = _case(
                strategy, _text(strategy, provider), regex=regex, **extra
            )
    return out


def _email_with_names() -> dict[str, dict[str, Any]]:
    """An e-mail column in a table that has first_name and last_name: it follows them."""
    helpers = {
        "first_name": _col(
            "first_name", "string", {"strategy": "native", "provider": "first_name"}
        ),
        "last_name": _col("last_name", "string", {"strategy": "native", "provider": "last_name"}),
    }
    out = {}
    for strategy in ("native", "faker"):
        out[f"{strategy}/email_follows_names"] = _case(
            strategy,
            _text(strategy, "email"),
            helpers=helpers,
            parts={
                "regex": _FIRST + r"\.(?P<last>[^ .@\d]+)(?P<n>\d+)@(?P<domain>[^ @]+)",
                "groups": {
                    "first": _pool("first_names", "slug"),
                    "last": _pool("last_names", "slug"),
                    "n": ["int"],
                    "domain": _pool("email_domains"),
                },
            },
        )
    return out


def _formula(expression: str, **helper_specs: Any) -> dict[str, Any]:
    scale = helper_specs.pop("scale", None)
    type_ = helper_specs.pop("type_", "decimal")
    helpers = {k: _col(k, "decimal", v) for k, v in helper_specs.items()}
    return _case(
        "formula",
        _col(TARGET, type_, {"strategy": "formula", "expression": expression}, scale=scale),
        helpers=helpers,
    )


def _enum(**weights: float) -> dict[str, Any]:
    return {"strategy": "weighted_enum", "values": weights}


_QTY = {"strategy": "weighted_enum", "values": {"1": 5, "2": 3, "3": 2, "5": 1, "10": 0.3}}
_PRICE = {"strategy": "distribution", "distribution": "log_normal", "mean": 3.5, "sigma": 0.8}
_PCT = {"strategy": "weighted_enum", "values": {"0": 7, "5": 1, "10": 1, "25": 0.2}}


def _formulas() -> dict[str, dict[str, Any]]:
    return {
        "formula/line_total": _formula(
            "quantity * unit_price", quantity=_QTY, unit_price=_PRICE, scale=2
        ),
        "formula/discounted": _formula(
            "quantity * unit_price * (1 - discount_percent / 100)",
            quantity=_QTY,
            unit_price=_PRICE,
            discount_percent=_PCT,
            scale=2,
        ),
        "formula/margin": _formula(
            "price - cost",
            price={"strategy": "distribution", "distribution": "normal", "mean": 80, "sigma": 10},
            cost={"strategy": "distribution", "distribution": "normal", "mean": 50, "sigma": 8},
            scale=2,
        ),
        "formula/fare": _formula(
            "(2.5 + distance_mi * 1.75 + duration_min * 0.35) * surge_mult",
            distance_mi={
                "strategy": "distribution",
                "distribution": "log_normal",
                "mean": 1.2,
                "sigma": 0.7,
            },
            duration_min={
                "strategy": "distribution",
                "distribution": "uniform",
                "min": 3,
                "max": 60,
            },
            surge_mult={"strategy": "weighted_enum", "values": {"1": 8, "1.5": 1.5, "2": 0.5}},
            scale=2,
        ),
        "formula/numpy_helpers": _formula(
            "np_round(np_sqrt(a) + np_log(b + 1), 3) + np_clip(a - 20, 0, 5)",
            a={"strategy": "distribution", "distribution": "uniform", "min": 1, "max": 100},
            b={"strategy": "distribution", "distribution": "uniform", "min": 0, "max": 50},
        ),
        "formula/np_where": _formula(
            "np_where(a > 50, a * 2, np_maximum(a, b))",
            a={"strategy": "distribution", "distribution": "uniform", "min": 0, "max": 100},
            b={"strategy": "distribution", "distribution": "uniform", "min": 0, "max": 100},
        ),
        "formula/integer": _formula("id * 3 + 7", type_="integer"),
        "formula/constant": _formula("12.5", type_="decimal"),
    }


def _date_source(name: str = "d") -> dict[str, Any]:
    return _col(
        name,
        "timestamp",
        {"strategy": "temporal", "pattern": "uniform", "start": "2022-01-01", "end": "2025-12-31"},
    )


_STAND_IN_DATES = {"strategy": "test_dates", "start": "2022-01-01", "end": "2025-12-31"}


def _derived(params: dict[str, Any], **extra: Any) -> dict[str, Any]:
    generator = {"strategy": "derived", "source": "d", **extra}
    if params:
        generator["params"] = params
    return _case(
        "derived",
        _col(TARGET, "timestamp", generator),
        helpers={"d": _date_source()},
        measure={"kind": "day_offset", "source": "d"},
        shape_generators={"d": _STAND_IN_DATES},
    )


def _derived_cases() -> dict[str, dict[str, Any]]:
    out = {
        "derived/add_days_uniform": _derived(
            {"distribution": "uniform", "min": 3, "max": 30}, rule="add_days"
        ),
        "derived/add_days_log_normal": _derived(
            {"distribution": "log_normal", "mean": 1.5, "sigma": 0.7, "min": 1, "max": 60},
            rule="add_days",
        ),
        "derived/add_days_normal": _derived(
            {"distribution": "normal", "mean": 10, "std_dev": 3, "min": 1, "max": 30},
            rule="add_days",
        ),
        "derived/add_days_defaults": _derived({}, rule="add_days"),
        "derived/operation_alias": _derived(
            {"distribution": "uniform", "min": 0, "max": 5}, operation="add_days"
        ),
        "derived/unknown_distribution_is_uniform": _derived(
            {"distribution": "weibull", "min": 2, "max": 9}, rule="add_days"
        ),
        "derived/days_shorthand": _derived({}, days=7),
    }
    # copy: the value is the source's, so the offset is always zero.
    out["derived/copy"] = _derived({}, rule="copy")
    # Cross-table: the child reads the parent's date through a key column.
    parent = {
        "rows": ROWS - 20_000,
        "primary_key": ["order_id"],
        "columns": {
            "order_id": _col("order_id", "integer", {"strategy": "sequence", "start": 1}),
            "order_date": _date_source("order_date"),
        },
    }
    child = {
        "rows": ROWS - 10_000,  # 10,000 rows have no parent: their value is null
        "primary_key": ["return_id"],
        "columns": {
            "return_id": _col("return_id", "integer", {"strategy": "sequence", "start": 1}),
            "order_id": _col(
                "order_id", "integer", {"strategy": "sequence", "start": 1, "step": 1}
            ),
            TARGET: _col(
                TARGET,
                "timestamp",
                {
                    "strategy": "derived",
                    "source": "order.order_date",
                    "via": "order_id",
                    "rule": "add_days",
                    "params": {
                        "distribution": "log_normal",
                        "mean": 2.0,
                        "sigma": 0.8,
                        "min": 1,
                        "max": 90,
                    },
                },
            ),
        },
    }
    out["derived/cross_table"] = {
        "strategy": "derived",
        "tables": {"order": parent, "return": child},
        "relationships": [
            {
                "name": "order_return",
                "parent": "order",
                "child": "return",
                "parent_columns": ["order_id"],
                "child_columns": ["order_id"],
                "type": "one_to_many",
                "cardinality": {},
                "optional": False,
            }
        ],
        "target": ["return", TARGET],
        "measure": {
            "kind": "cross_day_offset",
            "parent": ["order", "order_date"],
            "via": "order_id",
        },
        "parts": None,
        "shape_generators": {"order.order_date": _STAND_IN_DATES},
        "regex": None,
    }
    return out


def _computed(
    rule: str,
    column: str = "amount",
    type_: str = "decimal",
    parents: int = PARENT_ROWS,
    children: int = 18_000,
) -> dict[str, Any]:
    """``parent.x`` aggregates ``line.<column>``. The baseline's result is a float whenever some
    parent has no children (a pandas NaN, then filled with 0) and an integer otherwise; the cases
    that aggregate integers use enough children that every parent has some."""
    parent = {
        "rows": parents,
        "primary_key": ["id"],
        "columns": {
            "id": _col("id", "integer", {"strategy": "sequence", "start": 1}),
            TARGET: _col(
                TARGET,
                type_,
                {
                    "strategy": "computed",
                    "rule": rule,
                    "child_table": "line",
                    "child_column": column,
                },
            ),
        },
    }
    child = {
        "rows": children,
        "primary_key": ["line_id"],
        "columns": {
            "line_id": _col("line_id", "integer", {"strategy": "sequence", "start": 1}),
            "parent_id": _col(
                "parent_id",
                "integer",
                {"strategy": "foreign_key", "ref": "parent.id", "distribution": "uniform"},
            ),
            "amount": _col(
                "amount",
                "decimal",
                {
                    "strategy": "distribution",
                    "distribution": "log_normal",
                    "mean": 3.0,
                    "sigma": 0.6,
                },
                scale=2,
            ),
            "units": _col(
                "units",
                "integer",
                {"strategy": "weighted_enum", "values": {"1": 4, "2": 3, "3": 2, "4": 1}},
            ),
        },
    }
    return {
        "strategy": "computed",
        "tables": {"parent": parent, "line": child},
        "relationships": [
            {
                "name": "parent_line",
                "parent": "parent",
                "child": "line",
                "parent_columns": ["id"],
                "child_columns": ["parent_id"],
                "type": "one_to_many",
                "cardinality": {},
                "optional": False,
            }
        ],
        "target": ["parent", TARGET],
        "measure": {"kind": "values"},
        "parts": None,
        "shape_generators": {},
        "regex": None,
    }


CASES: dict[str, dict[str, Any]] = {
    **_providers(),
    **_email_with_names(),
    **_formulas(),
    **_derived_cases(),
    "computed/sum_children": _computed("sum_children"),
    "computed/count_children": _computed(
        "count_children", parents=SMALL_PARENTS, children=SMALL_PARENTS * 25
    ),
    "computed/avg_children": _computed("avg_children"),
    "computed/min_children": _computed("min_children"),
    "computed/max_children": _computed("max_children"),
    "computed/sum_of_integers": _computed(
        "sum_children", "line_id", "integer", parents=SMALL_PARENTS, children=SMALL_PARENTS * 25
    ),
}

# Native text cases with a database-style cap on the length, and a nullable column.
CASES["native/first_name_nullable"] = _case(
    "native", _text("native", "first_name", nullable=True, null_rate=0.2)
)
CASES["native/sentence_max_length"] = _case("native", _text("native", "sentence", max_length=24))
CASES["faker/city_max_length"] = _case("faker", _text("faker", "city", max_length=6))
CASES["native/default_provider"] = _case(
    "native", _col(TARGET, "string", {"strategy": "native"}), regex=r"[a-z0-9]{12}", parts=_CHARS
)


def strategies() -> list[str]:
    return sorted({c["strategy"] for c in CASES.values()})


def schema_for(case: dict[str, Any], impl: str = "shape", seed: int = 42) -> dict[str, Any]:
    """The schema (dumped format) of a case. ``impl`` is ``"shape"`` or ``"baseline"``: Shape
    gets the stand-ins named in ``shape_generators`` in place of the baseline's helpers."""
    tables: dict[str, Any] = {}
    for tname, t in case["tables"].items():
        columns = {}
        for cname, col in t["columns"].items():
            col = dict(col)
            override = case["shape_generators"].get(cname) or case["shape_generators"].get(
                f"{tname}.{cname}"
            )
            if impl == "shape" and override:
                col["generator"] = dict(override)
            columns[cname] = col
        tables[tname] = {"name": tname, "primary_key": t["primary_key"], "columns": columns}
    return {
        "model": {
            "name": "strategy_case",
            "description": "",
            "domain": "strategy_case",
            "schema_mode": "3nf",
            "locale": "en_US",
            "seed": seed,
            "date_range": {"start": "2022-01-01", "end": "2025-12-31"},
        },
        "tables": tables,
        "relationships": case["relationships"],
        "business_rules": [],
        "generation": {
            "scale": "s",
            "scales": {"s": {n: t["rows"] for n, t in case["tables"].items()}},
            "derived_counts": {},
        },
        "correlated_columns": {},
    }
