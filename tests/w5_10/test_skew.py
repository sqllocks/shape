"""W5-10 item 5: the skew rehearsal (`shape skew-rehearsal`)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape import skew
from shape.cli.main import main
from shape.generation.fanout import FanOut

from .conftest import schema_doc


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def planted_ids(top_customers: int = 40, per_top: int = 40, tail: int = 160) -> list[int]:
    """80/20: the top 20% of 200 customers hold 80% of 2000 orders."""
    ids: list[int] = []
    for c in range(top_customers):
        ids += [c + 1] * per_top
    for j in range(tail):
        ids += [top_customers + 1 + j] * (2 if j % 2 == 0 else 3)
    return ids


def make_profile(
    tmp_path: Path, ids: list[int], name: str = "order", column: str = "customer_id"
) -> Path:
    data = tmp_path / "profiled"
    data.mkdir(exist_ok=True)
    pq.write_table(
        pa.table({"order_id": list(range(len(ids))), column: ids}), data / f"{name}.parquet"
    )
    out = tmp_path / f"{name}.shape"
    assert main(["profile", str(data / f"{name}.parquet"), "-o", str(out)]) == 0
    return out


@pytest.fixture
def planted(tmp_path) -> Path:
    return make_profile(tmp_path, planted_ids())


def rehearse(capsys, profile, schema, out, *extra, scale="large"):
    return run(
        capsys, "skew-rehearsal", profile, "--schema", schema, "--scale", scale, "-o", out, *extra
    )


def report(out: Path) -> dict:
    return json.loads((out / "skew_report.json").read_text())


# ---- measuring the profile ------------------------------------------------------------------


def col(counts: dict[str, float], card: int) -> dict:
    return {"value_counts_ext": counts, "cardinality": card}


def test_measure_reads_the_planted_concentration(planted):
    column = shape.load(str(planted)).tables["order"]["columns"]["customer_id"]
    found = skew.measure(column)
    assert found is not None
    assert found.cardinality == 200 and found.top_fraction == 0.2
    assert found.top_share == pytest.approx(0.8, abs=1e-6)


def test_measure_with_only_the_top_keys_listed():
    # 1000 keys, only 10 listed: the top is 10 keys (1%), not 200 (20%)
    counts = {str(i): 0.05 for i in range(10)}
    found = skew.measure(col(counts, 1000))
    assert found is not None
    assert found.top_fraction == pytest.approx(0.01) and found.top_share == pytest.approx(0.5)


@pytest.mark.parametrize(
    "column",
    [
        {},
        col({}, 100),
        col({"a": 1.0}, 1),
        col({"a": 1.0}, 0),
        {"value_counts_ext": {"a": 1.0}},
        col({"a": 1.0}, None),
    ],
)
def test_measure_returns_none_without_usable_frequency_data(column):
    assert skew.measure(column) is None


def test_measure_clamps_a_flat_profile_to_the_fraction():
    counts = {str(i): 1 / 100 for i in range(100)}
    found = skew.measure(col(counts, 100))
    assert found is not None and found.top_share == pytest.approx(found.top_fraction)
    assert found.top_fraction == 0.2 and found.top_share == pytest.approx(0.2)


def test_measure_two_keys():
    found = skew.measure(col({"a": 0.9, "b": 0.1}, 2))
    assert found is not None and found.top_fraction == 0.5 and found.top_share == 0.9


# ---- the rehearsal --------------------------------------------------------------------------


def test_the_rehearsal_matches_a_planted_concentration(capsys, tmp_path, planted, schema_file):
    out = tmp_path / "out"
    code, text, err = rehearse(capsys, planted, schema_file, out)
    assert code == 0, err
    doc = report(out)
    assert doc["format"] == "shape-skew-report" and doc["version"] == 1
    assert doc["tolerance"] == 0.02 and doc["within_tolerance"] is True
    assert doc["scale"] == "large" and doc["seed"] == 7 and doc["skipped"] == []
    (entry,) = doc["columns"]
    assert entry["column"] == "order.customer_id" and entry["references"] == "customer.customer_id"
    assert entry["parents"] == 2000 and entry["rows"] == 40_000
    assert entry["profile_top_share"] == pytest.approx(0.8)
    assert abs(entry["generated_top_share"] - 0.8) <= 0.02
    assert entry["within_tolerance"] is True
    assert pq.read_table(out / "customer.parquet").num_rows == 2000
    assert pq.read_table(out / "order.parquet").num_rows == 40_000
    assert sorted(doc["files"]) == ["customer.parquet", "order.parquet"]
    assert "ok" in text


def test_a_uniform_run_fails(capsys, tmp_path, planted, schema_file, monkeypatch):
    """A generator that ignores the concentration (uniform keys) is caught."""
    monkeypatch.setattr(
        FanOut, "from_spec", classmethod(lambda cls, n, spec: cls(n, 0.2, 0.2, "power"))
    )
    out = tmp_path / "out"
    code, text, _ = rehearse(capsys, planted, schema_file, out)
    assert code == 1 and "OUTSIDE tolerance" in text
    doc = report(out)
    assert doc["within_tolerance"] is False
    (entry,) = doc["columns"]
    assert entry["within_tolerance"] is False
    assert entry["generated_top_share"] < 0.3 and entry["difference"] > 0.4


def test_the_default_generation_without_the_skew_is_uniform(tmp_path, schema_file):
    """The control for the test above: without ``fan_out`` the same schema is not skewed."""
    from shape.cli.generation import load_target
    from shape.generation.engine import Engine

    result = Engine(load_target(str(schema_file)), scale="large").generate()
    share, parents = skew.generated_share(
        result.tables["order"].column("customer_id"),
        result.tables["customer"].column("customer_id"),
        0.2,
    )
    assert parents == 2000 and share < 0.3


def test_tolerance_is_stated_in_the_report_and_changes_the_verdict(
    capsys, tmp_path, planted, schema_file, monkeypatch
):
    monkeypatch.setattr(
        FanOut, "from_spec", classmethod(lambda cls, n, spec: cls(n, 0.2, 0.2, "power"))
    )
    out = tmp_path / "out"
    assert rehearse(capsys, planted, schema_file, out)[0] == 1
    code, _, _ = rehearse(capsys, planted, schema_file, out, "--tolerance", "0.7")
    assert code == 0
    assert report(out)["tolerance"] == 0.7


@pytest.mark.parametrize("tolerance", ["0", "-0.1", "1", "2"])
def test_tolerance_outside_zero_one_exits_2(capsys, tmp_path, planted, schema_file, tolerance):
    code, _, err = rehearse(
        capsys, planted, schema_file, tmp_path / "out", f"--tolerance={tolerance}"
    )
    assert code == 2 and "--tolerance" in err


def test_tight_tolerance_is_a_failure_not_an_error(capsys, tmp_path, planted, schema_file):
    code, _, _ = rehearse(capsys, planted, schema_file, tmp_path / "o", "--tolerance", "0.000001")
    assert code == 1
    assert report(tmp_path / "o")["columns"][0]["within_tolerance"] is False


def test_the_seed_is_recorded_and_the_report_is_deterministic(
    capsys, tmp_path, planted, schema_file
):
    for name in ("a", "b"):
        assert rehearse(capsys, planted, schema_file, tmp_path / name, "--seed", "11")[0] == 0
    assert (tmp_path / "a" / "skew_report.json").read_bytes() == (
        tmp_path / "b" / "skew_report.json"
    ).read_bytes()
    assert report(tmp_path / "a")["seed"] == 11


def test_a_flat_profile_gives_a_flat_rehearsal(capsys, tmp_path, schema_file):
    """Few rows per key make the realized top share of a flat column exceed the fraction (the
    heaviest 20% of random counts hold more than 20%), so the tolerance has to allow for it; the
    rehearsal is still nowhere near an 80/20 column (docs/SCALE.md)."""
    flat = make_profile(tmp_path, [c + 1 for c in range(200)] * 10)
    code, _, _ = rehearse(capsys, flat, schema_file, tmp_path / "o", "--tolerance", "0.1")
    assert code == 0
    (entry,) = report(tmp_path / "o")["columns"]
    assert entry["profile_top_share"] == pytest.approx(0.2)
    assert 0.2 <= entry["generated_top_share"] < 0.3


def test_scales_other_than_the_profiles_keep_the_skew(capsys, tmp_path, planted, schema_file):
    code, _, _ = rehearse(capsys, planted, schema_file, tmp_path / "o", scale="small")
    (entry,) = report(tmp_path / "o")["columns"]
    assert entry["parents"] == 50 and entry["rows"] == 200
    assert entry["top_fraction"] == 0.2


# ---- which columns --------------------------------------------------------------------------


def three_table_schema(tmp_path: Path) -> Path:
    doc = schema_doc()
    doc["tables"]["order_line"] = {
        "name": "order_line",
        "primary_key": ["line_id"],
        "columns": {
            "line_id": {
                "name": "line_id",
                "type": "integer",
                "generator": {"strategy": "sequence"},
            },
            "order_id": {
                "name": "order_id",
                "type": "integer",
                "generator": {"strategy": "foreign_key", "ref": "order.order_id"},
            },
        },
    }
    doc["relationships"].append(
        {
            "name": "l_o",
            "parent": "order",
            "child": "order_line",
            "parent_columns": ["order_id"],
            "child_columns": ["order_id"],
        }
    )
    doc["generation"]["scales"]["small"]["order_line"] = 500
    doc["generation"]["scales"]["large"]["order_line"] = 80_000
    path = tmp_path / "three.json"
    path.write_text(json.dumps(doc))
    return path


def test_default_columns_skip_foreign_keys_without_frequency_data(capsys, tmp_path, planted):
    schema = three_table_schema(tmp_path)
    code, _, err = rehearse(capsys, planted, schema, tmp_path / "o")
    assert code == 0
    doc = report(tmp_path / "o")
    assert [c["column"] for c in doc["columns"]] == ["order.customer_id"]
    assert doc["skipped"] == [
        {"column": "order_line.order_id", "reason": "the profile has no frequency data"}
    ]
    assert "skipped order_line.order_id" in err


def test_named_columns_are_the_only_ones_skewed(capsys, tmp_path, planted):
    schema = three_table_schema(tmp_path)
    code, _, _ = rehearse(capsys, planted, schema, tmp_path / "o", "--columns", "order.customer_id")
    assert code == 0 and [c["column"] for c in report(tmp_path / "o")["columns"]] == [
        "order.customer_id"
    ]


def test_a_requested_column_without_frequency_data_exits_2(capsys, tmp_path, planted):
    schema = three_table_schema(tmp_path)
    out = tmp_path / "o"
    code, _, err = rehearse(capsys, planted, schema, out, "--columns", "order_line.order_id")
    assert code == 2 and "no frequency data for order_line.order_id" in err
    assert not out.exists()  # nothing was generated


def test_a_profile_without_the_column_at_all_exits_2(capsys, tmp_path, schema_file):
    other = make_profile(tmp_path, planted_ids(), name="invoice", column="buyer")
    code, _, err = rehearse(
        capsys, other, schema_file, tmp_path / "o", "--columns", "order.customer_id"
    )
    assert code == 2 and "no frequency data" in err


def test_no_candidate_at_all_exits_2(capsys, tmp_path, schema_file):
    other = make_profile(tmp_path, planted_ids(), name="invoice", column="buyer")
    code, _, err = rehearse(capsys, other, schema_file, tmp_path / "o")
    assert code == 2 and "nothing to rehearse" in err


def test_a_key_with_one_distinct_value_has_no_frequency_data(capsys, tmp_path, schema_file):
    constant = make_profile(tmp_path, [1] * 100)
    code, _, err = rehearse(
        capsys, constant, schema_file, tmp_path / "o", "--columns", "order.customer_id"
    )
    assert code == 2 and "no frequency data" in err


@pytest.mark.parametrize(
    ("column", "message"),
    [
        ("order.amount", "not a foreign-key column"),
        ("order.nope", "not a foreign-key column"),
        ("nope.x", "not a column of the schema"),
        ("customer_id", "not a column of the schema"),
    ],
)
def test_requested_columns_that_cannot_be_skewed_exit_2(
    capsys, tmp_path, planted, schema_file, column, message
):
    code, _, err = rehearse(capsys, planted, schema_file, tmp_path / "o", "--columns", column)
    assert code == 2 and message in err


@pytest.mark.parametrize("key", ["sample_rate", "constrained_by"])
def test_foreign_keys_that_bypass_the_fan_out_are_not_skewed(capsys, tmp_path, planted, key):
    doc = schema_doc()
    doc["tables"]["order"]["columns"]["customer_id"]["generator"][key] = (
        0.5 if key == "sample_rate" else "amount"
    )
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc))
    code, _, err = rehearse(capsys, planted, path, tmp_path / "o", "--columns", "order.customer_id")
    assert code == 2 and key in err
    code, _, err = rehearse(capsys, planted, path, tmp_path / "o2")
    assert code == 2 and "nothing to rehearse" in err


def test_empty_columns_option_exits_2(capsys, tmp_path, planted, schema_file):
    assert rehearse(capsys, planted, schema_file, tmp_path / "o", "--columns", " , ")[0] == 2


def test_bad_inputs_exit_2(capsys, tmp_path, planted, schema_file):
    assert rehearse(capsys, tmp_path / "missing.shape", schema_file, tmp_path / "o")[0] == 2
    assert rehearse(capsys, planted, tmp_path / "missing.json", tmp_path / "o")[0] == 2
    code, _, err = rehearse(capsys, planted, schema_file, tmp_path / "o", scale="nope")
    assert code == 2 and "unknown scale" in err


def test_the_fan_out_is_set_on_a_copy_of_the_schema(planted, schema_file):
    from shape.cli.generation import load_target

    schema = load_target(str(schema_file))
    skew.rehearse(shape.load(str(planted)), schema, "small")
    assert "fan_out" not in schema.tables["order"].columns["customer_id"].generator


def test_a_dataset_profile_is_read_by_table_name(capsys, tmp_path, schema_file):
    data = tmp_path / "ds"
    data.mkdir()
    pq.write_table(pa.table({"customer_id": list(range(1, 201))}), data / "customer.parquet")
    pq.write_table(
        pa.table({"order_id": list(range(2000)), "customer_id": planted_ids()}),
        data / "order.parquet",
    )
    profile = tmp_path / "ds.shape"
    assert main(["profile", str(data), "--dataset", "-o", str(profile)]) == 0
    code, _, err = rehearse(capsys, profile, schema_file, tmp_path / "o")
    assert code == 0, err
    assert report(tmp_path / "o")["columns"][0]["profile_top_share"] == pytest.approx(0.8)


def test_the_fan_out_spec_is_the_measured_concentration():
    found = skew.Concentration(200, 200, 0.2, 0.8)
    assert skew._fan_out(found) == {
        "top_fraction": 0.2,
        "top_share": 0.8,
        "shape": "power",
        "shuffle": True,
    }
