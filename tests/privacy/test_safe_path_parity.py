"""Safe output invariants across memory, full captures and safe captures."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import shape
from shape.cli.main import main
from shape.privacy.safe_profile import SafeConfig, to_safe_profile


@settings(max_examples=6, deadline=None)
@given(rows=st.integers(min_value=100, max_value=500), offset=st.integers(6, 1000))
def test_sensitive_fit_is_withheld_on_every_path(rows, offset):
    profile = shape.profile(pa.table({"order_id": list(range(offset, offset + rows))}))
    original = profile.to_dict()["columns"]["order_id"]
    assert original["distribution"] == "uniform"
    assert original["distribution_params"] is not None
    with TemporaryDirectory() as directory:
        paths = [profile]
        for mode in ("full", "safe"):
            path = Path(directory) / f"{mode}.shape"
            shape.save(profile, path, capture=mode)
            paths.append(path)
        columns = [
            next(iter(to_safe_profile(source, SafeConfig(k=5)).tables.values()))
            .columns["order_id"]
            .to_dict()
            for source in paths
        ]
        for column in columns:
            assert column["distribution_params"] is None
            assert column["distribution"] is None
        assert columns[0] == columns[1] == columns[2]
    assert profile.to_dict()["columns"]["order_id"] == original


@pytest.mark.parametrize("selection", ["exclude", "include"])
@settings(max_examples=5, deadline=None)
@given(suffix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=12))
def test_excluded_name_is_absent_from_every_safe_json(selection, suffix):
    excluded = f"shipping_address_{suffix}"
    profile = shape.profile(pa.table({excluded: list(range(200)), "status": ["paid", "new"] * 100}))
    with TemporaryDirectory() as directory:
        sources = [profile]
        for mode in ("full", "safe"):
            path = Path(directory) / f"{mode}.shape"
            shape.save(profile, path, capture=mode)
            sources.append(path)
        for source in sources:
            safe = to_safe_profile(source)
            before = safe.to_json()
            selected = safe.select_columns(
                include=["status"] if selection == "include" else None,
                exclude=[excluded] if selection == "exclude" else None,
            )
            for compact in (False, True):
                assert excluded.encode() not in selected.to_json(compact=compact).encode()
            assert excluded not in json.dumps(selected.redaction_manifest)
            assert "status" in next(iter(selected.tables.values())).columns
            assert safe.to_json() == before


def test_cli_exclusion_removes_the_manifest_entry(tmp_path):
    path = tmp_path / "input.shape"
    output = tmp_path / "safe.json"
    excluded = "shipping_address_id"
    shape.save(
        shape.profile(pa.table({excluded: list(range(200)), "status": ["paid"] * 200})), path
    )
    assert main(["profile", "safe", str(path), "--exclude", excluded, "-o", str(output)]) == 0
    assert excluded.encode() not in output.read_bytes()


@pytest.mark.parametrize("distribution", ["uniform", "exponential", "lognormal", "normal"])
@pytest.mark.parametrize("quantiles", [None, {"p1": 8.0, "p99": 95.0}])
def test_sensitive_fit_policy_does_not_depend_on_quantiles(distribution, quantiles):
    from shape.privacy.redact import redact_profile
    from shape.profile.reference.profile import Profile

    params = {"loc": 6.0, "scale": 99.0}
    profile = Profile(
        {
            "name": "t",
            "row_count": 100,
            "columns": {
                "order_id": {
                    "name": "order_id",
                    "dtype": "integer",
                    "cardinality": 100,
                    "null_count": 0,
                    "distribution": distribution,
                    "distribution_params": params,
                    "quantiles": quantiles,
                }
            },
        },
        name="t",
    )
    expected = params if distribution == "normal" else None
    for source in (profile, redact_profile(profile)):
        column = to_safe_profile(source).tables["t"].columns["order_id"]
        assert column.distribution_params == expected
    unsafe = to_safe_profile(profile, unsafe_full_fidelity=True)
    assert unsafe.tables["t"].columns["order_id"].distribution_params == params


def test_selection_removes_relationships_naming_excluded_columns():
    excluded = "shipping_address_id"
    profile = shape.profile(
        {
            "addresses": pa.table({excluded: list(range(200)), "status": ["new"] * 200}),
            "orders": pa.table({"order_id": list(range(400)), excluded: list(range(200)) * 2}),
        }
    )
    data = profile.to_dict()
    data["relationships"] = [
        {
            "name": f"fk_orders_{excluded}",
            "parent": "addresses",
            "child": "orders",
            "parent_columns": [excluded],
            "child_columns": [excluded],
            "type": "one_to_many",
        }
    ]
    safe = to_safe_profile(data)
    assert safe.relationships
    selected = safe.select_columns(exclude=[excluded])
    assert excluded.encode() not in selected.to_json().encode()
    assert selected.relationships == []
