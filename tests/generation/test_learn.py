"""P4-08: ``shape learn``, the schema builder.

The acceptance case: Shape's schema for D2 equals the one the pinned baseline's ``learn`` writes
(``benchmarks/vs_spindle/fixtures/learn/d2_20000.json``, regenerated and checked by
``learn_1to1/baseline.py --check``) except for the columns in ``ALLOWED`` below, each with the
reason it differs. ``learn_1to1/verify.py`` runs the same comparison on the full 1M-row D2 and on
the multi-table set against the live baseline. Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import shape
from shape.generation.engine import Engine
from shape.generation.learn import DIFFERENCES, SchemaBuilder, learn, profile_from_dict
from shape.generation.schema import GenSchema

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH / "profile_1to1"))


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


learn_compare = _load("vs_spindle_learn_compare", BENCH / "learn_1to1" / "compare.py")
learn_baseline = _load("vs_spindle_learn_baseline", BENCH / "learn_1to1" / "baseline.py")

# column -> (rule, why this column differs from the baseline's schema)
ALLOWED = {
    "d2.wait_time": (
        "exponential",
        "wait_time is fitted as an exponential distribution: Shape generates an exponential, the "
        "baseline a normal with the same mean and standard deviation clipped to the observed "
        "range",
    ),
    "d2.currency": (
        "enum_pattern",
        "currency holds ten currency codes, all listed in the profile: Shape generates those ten "
        "with their weights, the baseline any code of a faker provider",
    ),
}


@pytest.fixture(scope="module")
def d2_schema(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    csv = learn_baseline.d2_csv(tmp_path_factory.mktemp("d2"))
    return learn(shape.profile(str(csv))).to_dict()


def test_d2_schema_equals_the_baselines_except_the_listed_columns(
    d2_schema: dict[str, Any],
) -> None:
    baseline = json.loads(learn_baseline.FIXTURE.read_text())
    unexplained, explained = learn_compare.compare(d2_schema, baseline)
    assert unexplained == []
    assert {e["column"]: e["rule"] for e in explained} == {c: r for c, (r, _) in ALLOWED.items()}
    for e in explained:
        assert e["reason"] == DIFFERENCES[e["rule"]]
        assert ALLOWED[e["column"]][1]  # every difference is explained here too


def test_every_column_that_is_not_listed_is_equal(d2_schema: dict[str, Any]) -> None:
    baseline = json.loads(learn_baseline.FIXTURE.read_text())["tables"]["d2"]["columns"]
    ours = d2_schema["tables"]["d2"]["columns"]
    assert list(ours) == list(baseline)
    for name, col in baseline.items():
        if f"d2.{name}" in ALLOWED:
            continue
        assert ours[name]["generator"] == col["generator"], name
        assert ours[name]["type"] == col["type"], name
        assert ours[name]["null_rate"] == col.get("null_rate", 0.0), name


def test_the_comparison_catches_a_real_difference(d2_schema: dict[str, Any]) -> None:
    """The harness is not vacuous: a changed generator is reported as unexplained."""
    baseline = json.loads(learn_baseline.FIXTURE.read_text())
    baseline["tables"]["d2"]["columns"]["age"]["generator"] = {"strategy": "uuid"}
    unexplained, _ = learn_compare.compare(d2_schema, baseline)
    assert any(line.startswith("d2.age.generator") for line in unexplained)


def test_learned_schema_validates_and_generates(d2_schema: dict[str, Any]) -> None:
    schema = GenSchema.from_dict(d2_schema)
    assert [i for i in schema.validate() if i.level == "error"] == []
    result = Engine(schema, scale="small", seed=1).generate()
    assert result.tables["d2"].num_rows == schema.generation.scales["small"]["d2"]


# ---- the deliberate differences, one test each ----------------------------------------------


def _column(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "x",
        "dtype": "float",
        "null_count": 0,
        "null_rate": 0.0,
        "cardinality": 1000,
        "cardinality_ratio": 0.001,
        "is_unique": False,
        "is_enum": False,
        "enum_values": None,
        "min_value": ["float", 1.0],
        "max_value": ["float", 100.0],
        "mean": 50.0,
        "std": 10.0,
        "distribution": None,
        "distribution_params": None,
        "pattern": None,
        "is_primary_key": False,
        "is_foreign_key": False,
        "fk_ref_table": None,
        "quantiles": {"p1": 2.0, "p50": 50.0, "p99": 99.0},
        "value_counts_ext": None,
        "fit_score": 0.95,
    }
    base.update(fields)
    return base


def _generator(**fields: Any) -> dict[str, Any]:
    col = _column(**fields)
    profile = profile_from_dict(
        {
            "name": "t",
            "row_count": 100000,
            "primary_key": [],
            "detected_fks": {},
            "correlation_matrix": None,
            "columns": {"x": col},
        }
    )
    return SchemaBuilder().column_generator(profile.tables["t"].columns["x"], {})


def test_truncated_enum_is_generated_from_the_distribution() -> None:
    top = {str(i): 0.0005 for i in range(500)}  # 500 listed values of 1000 distinct
    gen = _generator(is_enum=True, value_counts_ext=top, enum_values=top)
    assert gen["strategy"] in ("distribution", "empirical")
    whole = {str(i): 0.01 for i in range(100)}  # every distinct value is listed
    assert _generator(is_enum=True, cardinality=100, value_counts_ext=whole)["strategy"] == (
        "weighted_enum"
    )


def test_exponential_fit_is_generated_as_an_exponential() -> None:
    gen = _generator(
        distribution="exponential", distribution_params={"loc": 0.001, "scale": 12.0}, mean=11.9
    )
    assert gen["distribution"] == "exponential"
    assert gen["params"]["lambda"] == pytest.approx(1 / (11.9 - 0.001))


def test_shifted_lognormal_uses_the_quantiles() -> None:
    shifted = {"s": 0.03, "loc": -300.0, "scale": 350.0}
    gen = _generator(distribution="lognormal", distribution_params=shifted)
    assert gen["strategy"] == "empirical"
    plain = {"s": 0.9, "loc": 0.4, "scale": 30.0}
    assert _generator(distribution="lognormal", distribution_params=plain)["distribution"] == (
        "log_normal"
    )


def test_code_patterns_keep_their_values_but_personal_ones_never_do() -> None:
    values = {"USD": 0.7, "EUR": 0.3}
    code = _generator(
        dtype="string",
        is_enum=True,
        pattern="currency_code",
        cardinality=2,
        value_counts_ext=values,
    )
    assert code == {"strategy": "weighted_enum", "values": values}
    for pattern, provider in (("email", "email"), ("phone", "phone_number"), ("ssn", "ssn")):
        gen = _generator(
            dtype="string", is_enum=True, pattern=pattern, cardinality=2, value_counts_ext=values
        )
        assert gen == {"strategy": "faker", "provider": provider}


# ---- the baseline's rules that are kept -----------------------------------------------------


def test_keys_relationships_and_scales_of_two_tables(tmp_path: Path) -> None:
    import pyarrow as pa
    from pyarrow import csv

    customer = pa.table({"customer_id": list(range(1, 201)), "name": ["n"] * 200})
    orders = pa.table(
        {"order_id": list(range(1, 1001)), "customer_id": [1 + i % 200 for i in range(1000)]}
    )
    csv.write_csv(customer, str(tmp_path / "customer.csv"))
    csv.write_csv(orders, str(tmp_path / "orders.csv"))
    profile = shape.profile(
        {"customer": tmp_path / "customer.csv", "orders": tmp_path / "orders.csv"}
    )
    schema = learn(profile, "shop")
    assert schema.model.name == "shop_inferred" and schema.model.domain == "shop"
    assert schema.tables["customer"].primary_key == ["customer_id"]
    assert schema.tables["orders"].columns["customer_id"].generator == {
        "strategy": "foreign_key",
        "ref": "customer.customer_id",
    }
    assert [r.parent for r in schema.relationships] == ["customer"]
    assert schema.generation.scales["medium"] == {"customer": 2000, "orders": 10000}
    assert schema.generation.scales["small"] == {"customer": 200, "orders": 1000}


def test_a_table_without_a_key_gets_a_surrogate(tmp_path: Path) -> None:
    import pyarrow as pa

    schema = learn(
        shape.profile(pa.table({"a": [1, 1, 2, 2], "b": ["x", "y", "x", "y"]}), name="t")
    )
    assert schema.tables["t"].primary_key == ["_row_id"]
    assert schema.tables["t"].columns["_row_id"].generator == {"strategy": "sequence", "start": 1}
