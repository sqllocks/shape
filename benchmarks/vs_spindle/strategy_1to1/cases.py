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
    **extra: Any,
) -> dict[str, Any]:
    return {
        "strategy": strategy,
        "column": _col(TARGET, type_, {"strategy": strategy, **generator}, **extra),
        "helpers": helpers or {},
        "regex": regex,
    }


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


def strategies() -> list[str]:
    return sorted({c["strategy"] for c in CASES.values()})


def schema_for(case: dict[str, Any], rows: int = ROWS, seed: int = 42) -> dict[str, Any]:
    """The single-table schema (dumped format) of a case."""
    columns = {
        "id": _col("id", "integer", {"strategy": "sequence", "start": 1}),
        **case["helpers"],
        TARGET: case["column"],
    }
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
        "tables": {TABLE: {"name": TABLE, "primary_key": ["id"], "columns": columns}},
        "relationships": [],
        "business_rules": [],
        "generation": {"scale": "s", "scales": {"s": {TABLE: rows}}, "derived_counts": {}},
        "correlated_columns": {},
    }
