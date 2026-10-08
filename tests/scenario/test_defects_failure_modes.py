"""W6-03 item 1: the defect kinds added for the failure mode scenarios (``chaos_temporal``,
``chaos_volume``, ``shuffle_column``, ``scale_values``, ``shift_hours``, ``truncate_strings``,
``placeholder_values``, ``corrupt_encoding``)."""

from __future__ import annotations

import pyarrow.compute as pc
import pytest

pytest.importorskip("shape_domains")

from shape.scenario.library.defects import DEFECTS, DefectError, apply_defects  # noqa: E402
from shape.scenario.library.run import _generate, _schema  # noqa: E402


@pytest.fixture(scope="module")
def retail():
    schema = _schema("retail")
    return schema, _generate(schema, "tiny", 42).tables


def plant(retail, defect, seed=42):
    schema, tables = retail
    out, changed = apply_defects(tables, [defect], schema, seed)
    return out[defect["table"]], changed[defect["kind"]]


def test_every_new_kind_is_registered():
    new = {
        "chaos_temporal",
        "chaos_volume",
        "shuffle_column",
        "scale_values",
        "shift_hours",
        "truncate_strings",
        "placeholder_values",
        "corrupt_encoding",
    }
    assert new <= set(DEFECTS)


def test_shuffle_keeps_every_value_and_changes_the_order(retail):
    _, tables = retail
    out, rows = plant(
        retail, {"kind": "shuffle_column", "table": "order", "column": "status", "fraction": 1.0}
    )
    before, after = tables["order"]["status"], out["status"]
    assert rows == 100 and sorted(before.to_pylist()) == sorted(after.to_pylist())
    assert before.to_pylist() != after.to_pylist()
    assert out.num_rows == tables["order"].num_rows


def test_scale_multiplies_the_last_share_of_the_rows_only(retail):
    _, tables = retail
    out, rows = plant(
        retail,
        {"kind": "scale_values", "table": "order", "column": "order_total", "fraction": 0.5,
         "factor": 100},
    )  # fmt: skip
    before, after = tables["order"]["order_total"].to_pylist(), out["order_total"].to_pylist()
    assert rows == 50 and after[:50] == before[:50]
    assert all(a == pytest.approx(b * 100) for a, b in zip(after[50:], before[50:], strict=True))


def test_scale_rounds_integers_and_keeps_the_type(retail):
    out, _ = plant(
        retail,
        {"kind": "scale_values", "table": "order", "column": "store_id", "fraction": 1.0,
         "factor": 2.5},
    )  # fmt: skip
    assert str(out["store_id"].type) == "int64"


@pytest.mark.parametrize(
    "defect",
    [
        {"kind": "scale_values", "table": "order", "column": "order_total", "factor": 0},
        {"kind": "scale_values", "table": "order", "column": "order_total"},
        {"kind": "scale_values", "table": "order", "column": "status", "factor": 2},
        {"kind": "scale_values", "table": "order", "column": "order_total", "factor": 2,
         "fraction": 0},
    ],
)  # fmt: skip
def test_scale_refuses_a_bad_factor_fraction_or_column(retail, defect):
    with pytest.raises(DefectError):
        plant(retail, defect)


def test_shift_hours_moves_the_asked_share_by_the_hours(retail):
    _, tables = retail
    out, rows = plant(
        retail,
        {"kind": "shift_hours", "table": "order", "column": "order_date", "fraction": 1.0,
         "hours": 8},
    )  # fmt: skip
    delta = pc.subtract(out["order_date"], tables["order"]["order_date"]).to_pylist()
    assert rows == 100 and {d.total_seconds() for d in delta} == {8 * 3600.0}


def test_shift_hours_refuses_zero_hours_and_a_column_that_is_not_a_timestamp(retail):
    base = {"kind": "shift_hours", "table": "order", "column": "order_date"}
    with pytest.raises(DefectError, match="must not be 0"):
        plant(retail, {**base, "hours": 0})
    with pytest.raises(DefectError, match="not a timestamp"):
        plant(retail, {**base, "column": "status", "hours": 1})


def _tables_email(retail):
    return retail[1]["customer"]["email"].to_pylist()


def test_truncate_cuts_the_asked_share_to_the_length(retail):
    _, tables = retail
    out, rows = plant(
        retail,
        {"kind": "truncate_strings", "table": "customer", "column": "email", "fraction": 0.4,
         "length": 5},
    )  # fmt: skip
    emails = out["email"].to_pylist()
    before = _tables_email(retail)
    cut = [a for a, b in zip(emails, before, strict=True) if a != b]
    assert rows == len(cut) and rows > 0 and all(len(a) == 5 for a in cut)


def test_truncate_refuses_a_zero_length_and_a_number_column(retail):
    with pytest.raises(DefectError, match="at least 1"):
        plant(
            retail,
            {"kind": "truncate_strings", "table": "customer", "column": "email", "length": 0},
        )
    with pytest.raises(DefectError, match="not text"):
        plant(
            retail,
            {"kind": "truncate_strings", "table": "customer", "column": "customer_id"},
        )


def test_placeholders_replace_the_asked_share_with_the_value(retail):
    out, rows = plant(
        retail,
        {"kind": "placeholder_values", "table": "customer", "column": "first_name",
         "fraction": 0.2, "value": "TBD"},
    )  # fmt: skip
    assert rows == 20 and out["first_name"].to_pylist().count("TBD") == 20


def test_placeholders_default_to_na(retail):
    out, _ = plant(
        retail,
        {"kind": "placeholder_values", "table": "customer", "column": "first_name",
         "fraction": 0.1},
    )  # fmt: skip
    assert "N/A" in out["first_name"].to_pylist()


def test_encoding_corruption_appends_the_mojibake_to_the_asked_share(retail):
    out, rows = plant(
        retail,
        {"kind": "corrupt_encoding", "table": "customer", "column": "email", "fraction": 0.3},
    )
    broken = [v for v in out["email"].to_pylist() if v and v.endswith("\u00c3\u00a9")]
    assert rows == len(broken) and rows > 0


def test_chaos_temporal_runs_the_chaos_mutator_on_the_column(retail):
    _, tables = retail
    out, rows = plant(
        retail,
        {"kind": "chaos_temporal", "table": "order", "column": "order_date",
         "action": "dst_boundary", "intensity": 15},
    )  # fmt: skip
    assert rows > 0
    stamps = {v.isoformat() for v in out["order_date"].to_pylist()}
    assert "2024-03-10T02:30:00" in stamps and out.num_rows == tables["order"].num_rows


def test_chaos_temporal_refuses_an_unknown_action_and_a_text_column(retail):
    base = {"kind": "chaos_temporal", "table": "order", "column": "order_date"}
    with pytest.raises(DefectError, match="'action'"):
        plant(retail, {**base, "action": "late_arrivals"})
    with pytest.raises(DefectError, match="not a timestamp"):
        plant(retail, {**base, "column": "status", "action": "out_of_order"})


@pytest.mark.parametrize(
    ("action", "rows"),
    [("empty", 0), ("single_row", 1)],
)
def test_chaos_volume_empties_or_cuts_the_table(retail, action, rows):
    out, changed = plant(retail, {"kind": "chaos_volume", "table": "order", "action": action})
    assert out.num_rows == rows and changed == 100 - rows
    assert out.schema == retail[1]["order"].schema


def test_chaos_volume_spike_adds_rows_and_needs_no_column(retail):
    out, changed = plant(
        retail, {"kind": "chaos_volume", "table": "order", "action": "spike", "intensity": 1}
    )
    assert out.num_rows == 100 + changed and changed == 1000


def test_chaos_volume_refuses_an_unknown_action(retail):
    with pytest.raises(DefectError, match="'action'"):
        plant(retail, {"kind": "chaos_volume", "table": "order", "action": "halve"})


@pytest.mark.parametrize(
    "defect",
    [
        {"kind": "shuffle_column", "table": "order", "column": "status", "fraction": 0.5},
        {"kind": "corrupt_encoding", "table": "customer", "column": "email", "fraction": 0.3},
        {"kind": "chaos_volume", "table": "order", "action": "spike"},
    ],
)
def test_the_new_defects_are_deterministic_for_a_seed(retail, defect):
    first, _ = plant(retail, defect, seed=5)
    second, _ = plant(retail, defect, seed=5)
    assert first.equals(second)


def test_a_missing_column_or_table_is_refused_by_name(retail):
    with pytest.raises(DefectError, match="no column order.nope"):
        plant(retail, {"kind": "shuffle_column", "table": "order", "column": "nope"})
    with pytest.raises(DefectError, match="no table"):
        plant(retail, {"kind": "chaos_volume", "table": "nope", "action": "empty"})
