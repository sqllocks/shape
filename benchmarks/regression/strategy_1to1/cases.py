"""The cases of the per-strategy equivalence tests: one single-table schema per case, in the
dumped-schema format, so the baseline and Shape generate exactly the same configuration.

Standard library only. Each case names the strategy, the column under test (``x``) and, where it
refers to other columns, those helpers. ``regex`` (when given) is a full-match pattern every
generated value must satisfy.
"""

from __future__ import annotations

from typing import Any

ROWS = 60_000
TABLE = "t"
TARGET = "x"


def _col(name: str, type_: str, generator: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "generator": generator,
        "nullable": extra.pop("nullable", False),
        "null_rate": extra.pop("null_rate", 0.0),
        "max_length": None,
        "precision": None,
        "scale": extra.pop("scale", None),
    }


def _case(
    strategy: str,
    type_: str,
    generator: dict[str, Any],
    *,
    helpers: dict[str, dict[str, Any]] | None = None,
    regex: str | None = None,
    tables: dict[str, dict[str, Any]] | None = None,
    datasets: dict[str, list[Any]] | None = None,
    rows: int | None = None,
    model_date_range: dict[str, str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "strategy": strategy,
        "column": _col(TARGET, type_, {"strategy": strategy, **generator}, **extra),
        "helpers": helpers or {},
        "regex": regex,
    }
    # Optional parts of a case: other tables of the schema (name -> {"rows", "primary_key",
    # "columns"}), reference datasets (name -> JSON list), the row count of the table under
    # test, and the model's date range.
    if tables:
        case["tables"] = tables
    if datasets:
        case["datasets"] = datasets
    if rows is not None:
        case["rows"] = rows
    if model_date_range:
        case["model_date_range"] = model_date_range
    return case


def _dist(name: str, type_: str = "decimal", **params: Any) -> dict[str, Any]:
    scale = params.pop("column_scale", None)
    null_rate = params.pop("null_rate", 0.0)
    nested = params.pop("nested", False)
    gen: dict[str, Any] = {"distribution": name}
    if nested:
        gen["params"] = params
    else:
        gen.update(params)
    return _case(
        "distribution", type_, gen, scale=scale, nullable=null_rate > 0, null_rate=null_rate
    )


_QUANTILES = {
    "p1": 1.0, "p5": 2.0, "p10": 3.5, "p25": 8.0, "p50": 20.0,
    "p75": 55.0, "p90": 130.0, "p95": 240.0, "p99": 900.0,
}  # fmt: skip

CASES: dict[str, dict[str, Any]] = {
    "sequence/default": _case("sequence", "integer", {"start": 1}),
    "sequence/offset_step": _case("sequence", "integer", {"start": 1000, "step": 7}),
    "uuid/default": _case(
        "uuid",
        "string",
        {},
        regex=r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    ),
    "weighted_enum/three": _case(
        "weighted_enum", "string", {"values": {"gold": 0.5, "silver": 0.3, "bronze": 0.2}}
    ),
    "weighted_enum/unnormalised": _case(
        "weighted_enum", "string", {"values": {"a": 10, "b": 5, "c": 1, "d": 0.5, "e": 0.01}}
    ),
    "weighted_enum/numeric_labels": _case(
        "weighted_enum", "decimal", {"values": {"0": 0.7, "1": 0.2, "2.5": 0.1}}
    ),
    "weighted_enum/many": _case(
        "weighted_enum",
        "string",
        {"values": {f"v{i:03d}": 1.0 / (i + 1) for i in range(300)}},
    ),
    "weighted_enum/nullable": _case(
        "weighted_enum", "string", {"values": {"x": 3, "y": 1}}, nullable=True, null_rate=0.15
    ),
    "distribution/uniform": _dist("uniform", min=5, max=50),
    "distribution/uniform_default": _dist("uniform"),
    "distribution/normal": _dist("normal", mean=100, std_dev=15),
    "distribution/normal_sigma_alias": _dist("normal", mean=-4, sigma=2.5),
    "distribution/normal_clipped": _dist("normal", mean=50, std_dev=30, min=20, max=90),
    "distribution/log_normal": _dist("log_normal", mean=3.5, sigma=0.8),
    "distribution/log_normal_scale": _dist(
        "log_normal", mean=4.0, sigma=0.6, min=5, max=400, column_scale=2
    ),
    "distribution/log_normal_nested": _dist("log_normal", nested=True, mean=2.0, sigma=1.1),
    "distribution/pareto": _dist("pareto", alpha=2.5, min=10),
    "distribution/zipf": _dist("zipf", alpha=1.8, max=200),
    "distribution/zipf_default": _dist("zipf"),
    "distribution/geometric": _dist("geometric", p=0.15),
    "distribution/poisson": _dist("poisson", **{"lambda": 7}),
    "distribution/poisson_large": _dist("poisson", **{"lambda": 60}),
    "distribution/bernoulli": _dist("bernoulli", probability=0.3),
    "distribution/nullable": _dist("normal", mean=0, std_dev=1, null_rate=0.1),
    "empirical/linear": _case("empirical", "decimal", {"quantiles": _QUANTILES}),
    "empirical/clipped": _case(
        "empirical", "decimal", {"quantiles": _QUANTILES, "min": 3.0, "max": 200.0}
    ),
    "empirical/tail_anchors": _case(
        "empirical",
        "decimal",
        {"quantiles": {**_QUANTILES, "p0_5": 0.2, "p99_5": 2500.0}},
    ),
    "pattern/seq_padded": _case("pattern", "string", {"format": "INV-{seq:6}"}, regex=r"INV-\d{6}"),
    "pattern/seq_plain": _case("pattern", "string", {"format": "R{seq}"}, regex=r"R\d+"),
    "pattern/random": _case(
        "pattern", "string", {"format": "{random:4}-{random:2}"}, regex=r"[A-Z0-9]{4}-[A-Z0-9]{2}"
    ),
    "pattern/mixed": _case(
        "pattern", "string", {"format": "ORD-{seq:5}-{random:3}"}, regex=r"ORD-\d{5}-[A-Z0-9]{3}"
    ),
    "pattern/default_random_width": _case(
        "pattern", "string", {"format": "{random}"}, regex=r"[A-Z0-9]{4}"
    ),
    "pattern/column_reference": _case(
        "pattern",
        "string",
        {"format": "{tier}-{id:4}-{seq:3}"},
        helpers={
            "tier": _col(
                "tier", "string", {"strategy": "weighted_enum", "values": {"A": 3, "B": 1}}
            ),
        },
        regex=r"[AB]-\d{4,}-\d{3,}",
    ),
    "pattern/unresolved_token": _case(
        "pattern", "string", {"format": "{nothing}-{seq:2}"}, regex=r"\{nothing\}-\d{2,}"
    ),
}


# ---- P4-04c: conditional, correlated, lookup, reference_data, record_*, temporal -------------

# Reference datasets (JSON lists) that the reference cases read: written to a domain directory
# for the baseline, registered in-process for Shape.
LABELS = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa",
    "lambda", "mu", "nu", "xi", "omicron", "pi", "rho", "sigma", "tau", "upsilon",
    "alpha", "alpha", "beta", "gamma", "phi", "chi", "psi", "omega",
]  # fmt: skip
WEIGHTED = [{"name": f"item{i:02d}", "weight": round(30.0 / (i + 1), 3)} for i in range(30)]
WEIGHTED_VALUE = [{"value": f"v{i}", "weight": float(1 + i % 5)} for i in range(25)]
CATALOG = [
    {
        "code": f"C{i:03d}",
        "kind": ("tool", "toy", "book", "game")[i % 4 if i < 40 else 0],
        "price": round(4.5 + (i * 37 % 91) * 1.25, 2),
        "qty": 1 + (i * 7) % 23,
    }
    for i in range(50)
]  # fmt: skip
PLACES = [
    {
        "city": f"City{i:02d}",
        "state": ("WA", "OR", "CA", "NV", "ID", "UT")[i % 6],
        "zip": f"{98000 + i * 13:05d}",
        "lat": round(40.0 + (i * 0.37) % 9, 4),
        "lng": round(-124.0 + (i * 0.53) % 11, 4),
    }
    for i in range(60)
]  # fmt: skip
DATASETS = {
    "p404c_labels": LABELS,
    "p404c_weighted": WEIGHTED,
    "p404c_weighted_value": WEIGHTED_VALUE,
    "p404c_catalog": CATALOG,
    "p404c_places": PLACES,
}


def _helper(name: str, values: dict[str, float], type_: str = "string", **extra: Any) -> Any:
    return _col(name, type_, {"strategy": "weighted_enum", "values": values}, **extra)


def _zipf_keys(n: int, power: float = 1.0) -> dict[str, float]:
    return {str(i + 1): 1.0 / (i + 1) ** power for i in range(n)}


def _parent(rows: int, columns: dict[str, dict[str, Any]], key: str = "pid") -> dict[str, Any]:
    return {
        "rows": rows,
        "primary_key": [key],
        "columns": {key: _col(key, "integer", {"strategy": "sequence", "start": 1}), **columns},
    }


def _lookup(table: str, column: str, via: str = "pid", **extra: Any) -> dict[str, Any]:
    return {"source_table": table, "source_column": column, "via": via, **extra}


_PARENT_LABELS = {
    "p": _parent(
        40,
        {
            "code": _col("code", "string", {"strategy": "pattern", "format": "P{seq:3}"}),
            "label": _col(
                "label",
                "string",
                {"strategy": "weighted_enum", "values": {"red": 5, "green": 3, "blue": 1}},
            ),
        },
    )
}
_PARENT_BIG = {
    "p": _parent(
        2000,
        {
            "val": _col(
                "val", "decimal", {"strategy": "distribution", "distribution": "log_normal",
                                   "mean": 3.0, "sigma": 0.9}, scale=2,
            )
        },
    )
}  # fmt: skip
_PARENT_ID = {
    "p": _parent(
        ROWS,
        {
            "val": _col(
                "val", "decimal", {"strategy": "distribution", "distribution": "normal",
                                   "mean": 50, "std_dev": 12}, scale=1,
            )
        },
        key="id",
    )
}  # fmt: skip
_PARENT_DISCOUNT = {
    "p": _parent(
        30,
        {
            "discount_pct": _col(
                "discount_pct",
                "decimal",
                {
                    "strategy": "weighted_enum",
                    "values": {"0.05": 4, "0.10": 3, "0.15": 2, "0.25": 1},
                },
            )
        },
    )
}  # fmt: skip

_PRICE = _col(
    "price", "decimal", {"strategy": "distribution", "distribution": "log_normal",
                         "mean": 3.5, "sigma": 0.6}, scale=2,
)  # fmt: skip
_TIER = _helper("tier", {"A": 3, "B": 2, "C": 1})
_LEVEL = _helper("level", {"1": 5, "2": 3, "3": 2}, "decimal")
_ANCHOR = _col("anchor", "string", {"strategy": "record_sample", "dataset": "p404c_places",
                                    "field": "city"})  # fmt: skip


def _temporal(pattern: str | None = None, **gen: Any) -> dict[str, Any]:
    extra = {"model_date_range": gen.pop("model_date_range")} if "model_date_range" in gen else {}
    return _case(
        "temporal",
        "timestamp",
        {"pattern": pattern, **gen} if pattern else gen,
        **extra,
    )


_MONTHS_W = {"Jan": 0.5, "Feb": 0.7, "Mar": 1.0, "Jun": 1.4, "Nov": 3.0, "Dec": 4.0}
_DOW_W = {"Mon": 1.0, "Tue": 1.0, "Wed": 1.2, "Thu": 1.2, "Fri": 2.0, "Sat": 3.0, "Sun": 2.5}

CASES.update(
    {
        # ---- lookup -----------------------------------------------------------------------
        "lookup/small_parent": _case(
            "lookup",
            "string",
            _lookup("p", "code"),
            tables=_PARENT_LABELS,
            helpers={"pid": _helper("pid", _zipf_keys(40), "decimal")},
        ),
        "lookup/random_parent_values": _case(
            "lookup",
            "string",
            _lookup("p", "label"),
            tables=_PARENT_LABELS,
            helpers={"pid": _helper("pid", _zipf_keys(40), "decimal")},
        ),
        "lookup/large_parent": _case(
            "lookup",
            "decimal",
            _lookup("p", "val"),
            tables=_PARENT_BIG,
            scale=2,
            helpers={"pid": _helper("pid", _zipf_keys(2000, 0.8), "decimal")},
        ),
        "lookup/same_key_name": _case(
            "lookup",
            "decimal",
            _lookup("p", "val", "id"),
            tables=_PARENT_ID,
            scale=1,
        ),
        "lookup/unmatched_keys": _case(
            "lookup",
            "string",
            _lookup("p", "label"),
            tables=_PARENT_LABELS,
            helpers={"pid": _helper("pid", _zipf_keys(55, 0.5), "decimal")},
        ),
        # ---- conditional --------------------------------------------------------------------
        "conditional/not_null_lookup": _case(
            "conditional",
            "decimal",
            {
                "condition": "pid IS NOT NULL",
                "true_generator": {"strategy": "lookup", **_lookup("p", "discount_pct")},
                "false_generator": {"fixed": 0.0},
            },
            tables=_PARENT_DISCOUNT,
            helpers={
                "pid": _helper("pid", _zipf_keys(30), "decimal", nullable=True, null_rate=0.35)
            },
        ),
        "conditional/is_null_fixed": _case(
            "conditional",
            "decimal",
            {
                "condition": "flag IS NULL",
                "true_generator": {"fixed": 1.0},
                "false_generator": {"fixed": 2.5},
            },
            helpers={"flag": _helper("flag", {"x": 3, "y": 1}, nullable=True, null_rate=0.5)},
        ),
        "conditional/not_null_case_insensitive": _case(
            "conditional",
            "decimal",
            {
                "condition": "TIER is not null",
                "true_generator": {"fixed": 7},
                "false_generator": {"fixed": -1},
            },
            helpers={"tier": _helper("tier", {"A": 1}, nullable=True, null_rate=0.3)},
        ),
        "conditional/equals_text": _case(
            "conditional",
            "decimal",
            {
                "condition": "tier == 'A'",
                "true_generator": {"fixed": 10},
                "false_generator": {"fixed": 0},
            },
            helpers={"tier": _TIER},
        ),
        "conditional/not_equals_number": _case(
            "conditional",
            "decimal",
            {
                "condition": "level != 2",
                "true_generator": {"fixed": 5.5},
                "false_generator": {"fixed": 0.5},
            },
            helpers={"level": _LEVEL},
        ),
        "conditional/equals_number_text_column": _case(
            "conditional",
            "decimal",
            {
                "condition": "level == 3",
                "true_generator": {"fixed": 1},
                "false_generator": {"fixed": 0},
            },
            helpers={"level": _helper("level", {"1": 5, "2": 3, "3": 2}, "string")},
        ),
        "conditional/text_result": _case(
            "conditional",
            "string",
            {
                "condition": "tier == 'B'",
                "true_generator": {"fixed": "premium"},
                "false_generator": {"fixed": "standard"},
            },
            helpers={"tier": _TIER},
        ),
        "conditional/empty_branch": _case(
            "conditional",
            "decimal",
            {"condition": "tier == 'C'", "true_generator": {"fixed": 3}, "false_generator": {}},
            helpers={"tier": _TIER},
        ),
        # ---- correlated ---------------------------------------------------------------------
        "correlated/multiply_default": _case(
            "correlated",
            "decimal",
            {"source_column": "price"},
            helpers={"price": _PRICE},
        ),
        "correlated/multiply_params": _case(
            "correlated",
            "decimal",
            {
                "source_column": "price",
                "rule": "multiply",
                "params": {"factor_min": 1.3, "factor_max": 1.8},
            },
            helpers={"price": _PRICE},
        ),
        "correlated/operation_alias_scale": _case(
            "correlated",
            "decimal",
            {
                "source_column": "price",
                "operation": "multiply",
                "params": {"min": 0.1, "max": 0.95},
            },
            helpers={"price": _PRICE},
            scale=3,
        ),
        "correlated/add": _case(
            "correlated",
            "decimal",
            {
                "source_column": "price",
                "rule": "add",
                "params": {"offset_min": 2, "offset_max": 20},
            },
            helpers={"price": _PRICE},
        ),
        "correlated/add_defaults": _case(
            "correlated",
            "decimal",
            {"source_column": "price", "rule": "add"},
            helpers={"price": _PRICE},
        ),
        "correlated/subtract_clipped": _case(
            "correlated",
            "decimal",
            {
                "source_column": "bal",
                "rule": "subtract",
                "params": {"offset_min": 1, "offset_max": 9},
            },
            helpers={
                "bal": _col(
                    "bal",
                    "decimal",
                    {"strategy": "distribution", "distribution": "normal", "mean": 4, "std_dev": 3},
                    scale=2,
                )
            },
        ),
        "correlated/null_source": _case(
            "correlated",
            "decimal",
            {"source_column": "price"},
            helpers={
                "price": _col(
                    "price",
                    "decimal",
                    {
                        "strategy": "distribution",
                        "distribution": "log_normal",
                        "mean": 3.5,
                        "sigma": 0.6,
                    },
                    scale=2,
                    nullable=True,
                    null_rate=0.12,
                )
            },
        ),
        # ---- reference_data -----------------------------------------------------------------
        "reference_data/strings": _case(
            "reference_data",
            "string",
            {"dataset": "p404c_labels"},
            datasets=DATASETS,
        ),
        "reference_data/field_text": _case(
            "reference_data",
            "string",
            {"dataset": "p404c_catalog", "field": "kind"},
            datasets=DATASETS,
        ),
        "reference_data/field_float": _case(
            "reference_data",
            "decimal",
            {"dataset": "p404c_catalog", "field": "price"},
            datasets=DATASETS,
        ),
        "reference_data/field_int": _case(
            "reference_data",
            "integer",
            {"dataset": "p404c_catalog", "field": "qty"},
            datasets=DATASETS,
        ),
        "reference_data/weighted_name": _case(
            "reference_data",
            "string",
            {"dataset": "p404c_weighted"},
            datasets=DATASETS,
        ),
        "reference_data/weighted_value_key": _case(
            "reference_data",
            "string",
            {"dataset": "p404c_weighted_value", "field": "nope"},
            datasets=DATASETS,
        ),
        "reference_data/nullable": _case(
            "reference_data",
            "string",
            {"dataset": "p404c_labels"},
            datasets=DATASETS,
            nullable=True,
            null_rate=0.2,
        ),
        # ---- record_sample / record_field ---------------------------------------------------
        "record_sample/anchor": _case(
            "record_sample",
            "string",
            {"dataset": "p404c_places", "field": "state"},
            datasets=DATASETS,
        ),
        "record_sample/numeric_field": _case(
            "record_sample",
            "decimal",
            {"dataset": "p404c_places", "field": "lat"},
            datasets=DATASETS,
        ),
        "record_sample/unique_all": _case(
            "record_sample",
            "string",
            {"dataset": "p404c_places", "field": "city", "unique": True},
            datasets=DATASETS,
            rows=60,
        ),
        "record_sample/unique_subset": _case(
            "record_sample",
            "string",
            {"dataset": "p404c_places", "field": "zip", "unique": True},
            datasets=DATASETS,
            rows=45,
        ),
        "record_sample/unique_too_many": _case(
            "record_sample",
            "string",
            {"dataset": "p404c_places", "field": "state", "unique": True},
            datasets=DATASETS,
        ),
        "record_field/state": _case(
            "record_field",
            "string",
            {"dataset": "p404c_places", "field": "state"},
            helpers={"anchor": _ANCHOR},
            datasets=DATASETS,
        ),
        "record_field/zip": _case(
            "record_field",
            "string",
            {"dataset": "p404c_places", "field": "zip"},
            helpers={"anchor": _ANCHOR},
            datasets=DATASETS,
        ),
        "record_field/lng": _case(
            "record_field",
            "decimal",
            {"dataset": "p404c_places", "field": "lng"},
            helpers={"anchor": _ANCHOR},
            datasets=DATASETS,
        ),
        "record_field/unique_anchor": _case(
            "record_field",
            "string",
            {"dataset": "p404c_places", "field": "state"},
            helpers={
                "anchor": _col(
                    "anchor",
                    "string",
                    {
                        "strategy": "record_sample",
                        "dataset": "p404c_places",
                        "field": "city",
                        "unique": True,
                    },
                )
            },
            datasets=DATASETS,
            rows=60,
        ),
        # ---- temporal -----------------------------------------------------------------------
        "temporal/uniform_default": _temporal(),
        "temporal/uniform_range": _temporal("uniform", start="2023-03-01", end="2024-02-29"),
        "temporal/nested_date_range": _temporal(
            "uniform", date_range={"start": "2019-06-15", "end": "2021-06-14"}
        ),
        "temporal/range_alias": _temporal(
            "uniform", range={"start": "2020-01-01", "end": "2020-12-31"}
        ),
        "temporal/range_ref": _temporal(
            "uniform",
            range_ref="model.date_range",
            model_date_range={"start": "2021-05-01", "end": "2022-04-30"},
        ),
        "temporal/unknown_pattern": _temporal("weekly", start="2022-01-01", end="2022-12-31"),
        "temporal/seasonal_month": _temporal(
            "seasonal", start="2022-01-01", end="2025-12-31", profiles={"month": _MONTHS_W}
        ),
        "temporal/seasonal_dow": _temporal(
            "seasonal", start="2022-01-01", end="2025-12-31", profiles={"day_of_week": _DOW_W}
        ),
        "temporal/seasonal_both_bimodal": _temporal(
            "seasonal",
            start="2023-01-01",
            end="2024-12-31",
            profiles={
                "month": _MONTHS_W,
                "day_of_week": _DOW_W,
                "hour_of_day": {"distribution": "bimodal", "peaks": [10, 20], "std_dev": 2.5},
            },
        ),
        "temporal/seasonal_bimodal_wrap": _temporal(
            "seasonal",
            start="2023-01-01",
            end="2023-12-31",
            profiles={
                "day_of_week": _DOW_W,
                "hour_of_day": {"distribution": "bimodal", "peaks": [22, 3], "std_dev": 3},
            },
        ),
        "temporal/seasonal_hour_default_peaks": _temporal(
            "seasonal",
            start="2023-01-01",
            end="2023-12-31",
            profiles={"month": {"Jan": 2.0}, "hour_of_day": {"distribution": "bimodal"}},
        ),
        "temporal/seasonal_hour_uniform": _temporal(
            "seasonal",
            start="2023-01-01",
            end="2023-12-31",
            profiles={"month": _MONTHS_W, "hour_of_day": {"distribution": "uniform"}},
        ),
        "temporal/seasonal_hour_only": _temporal(
            "seasonal",
            start="2023-01-01",
            end="2023-12-31",
            profiles={"hour_of_day": {"distribution": "bimodal", "peaks": [8, 17], "std_dev": 1.5}},
        ),
        "temporal/seasonal_short_range": _temporal(
            "seasonal",
            start="2024-03-01",
            end="2024-05-15",
            profiles={
                "month": {"Jan": 5.0, "Mar": 1.0, "Apr": 1.0, "May": 2.0},
                "day_of_week": _DOW_W,
            },
        ),
        "temporal/seasonal_top_level_weights": _temporal(
            "seasonal",
            start="2022-01-01",
            end="2023-12-31",
            month_weights=_MONTHS_W,
            day_of_week_weights=_DOW_W,
        ),
        "temporal/seasonal_empty_profiles": _temporal(
            "seasonal", start="2022-01-01", end="2022-12-31", profiles={}
        ),
        "temporal/seasonal_range_ref": _temporal(
            "seasonal",
            range_ref="model.date_range",
            profiles={"month": _MONTHS_W},
            model_date_range={"start": "2020-01-01", "end": "2022-12-31"},
        ),
    }
)


def strategies() -> list[str]:
    return sorted({c["strategy"] for c in CASES.values()})


def schema_for(case: dict[str, Any], rows: int | None = None, seed: int = 42) -> dict[str, Any]:
    """The schema (dumped format) of a case: table ``t`` holds the column under test, after an
    ``id`` key and the case's helper columns; ``case["tables"]`` adds other tables."""
    n = rows if rows is not None else case.get("rows", ROWS)
    columns = {
        "id": _col("id", "integer", {"strategy": "sequence", "start": 1}),
        **case["helpers"],
        TARGET: case["column"],
    }
    tables: dict[str, Any] = {TABLE: {"name": TABLE, "primary_key": ["id"], "columns": columns}}
    sizes = {TABLE: n}
    for name, spec in case.get("tables", {}).items():
        tables[name] = {
            "name": name,
            "primary_key": spec["primary_key"],
            "columns": spec["columns"],
        }
        sizes[name] = spec["rows"]
    return {
        "model": {
            "name": "strategy_case",
            "description": "",
            "domain": "strategy_case",
            "schema_mode": "3nf",
            "locale": "en_US",
            "seed": seed,
            "date_range": case.get(
                "model_date_range", {"start": "2022-01-01", "end": "2025-12-31"}
            ),
        },
        "tables": tables,
        "relationships": [],
        "business_rules": [],
        "generation": {"scale": "s", "scales": {"s": sizes}, "derived_counts": {}},
        "correlated_columns": {},
    }
