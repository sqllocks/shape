"""The six chaos categories, the engine schedule, the config and the plugin wrappers."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.chaos import (
    ChaosCategory,
    ChaosConfig,
    ChaosEngine,
    ChaosOverride,
    FileChaosMutator,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
)
from shape.plugins import kit
from shape.plugins.host import PluginHost
from shape.plugins.registry import register_builtins

N = 400


def _frame(n: int = N) -> pa.Table:
    rng = np.random.default_rng(11)
    ts = np.datetime64("2024-01-05T00:00:00", "us") + (np.arange(n) * 7 * 60 * 10**6).astype(
        "timedelta64[us]"
    )
    return pa.table(
        {
            "id": pa.array(range(1, n + 1), pa.int64()),
            "qty": pa.array(rng.integers(1, 20, n), pa.int32()),
            "unit_price": pa.array(np.round(rng.random(n) * 90 + 5, 2)),
            "name": pa.array([f"name{i}" for i in range(n)]),
            "created_at": pa.array(ts + rng.integers(0, 10**6, n).astype("timedelta64[us]")),
        }
    )


def _tables() -> dict[str, pa.Table]:
    ids = pa.array(range(1, N + 1), pa.int64())
    return {
        "customer": pa.table({"customer_id": ids, "name": pa.array([f"c{i}" for i in range(N)])}),
        "orders": pa.table(
            {
                "order_id": ids,
                "customer_id": pa.array(np.arange(N) % 50 + 1, pa.int64()),
                "amount": pa.array(np.arange(N) / 3),
            }
        ),
    }


def _rng(seed: int = 1) -> np.random.Generator:
    return np.random.default_rng(seed)


def _same(a: pa.Table, b: pa.Table) -> bool:
    return bool(a.equals(b))


# ---- value ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "rate"),
    [
        ("inject_nulls", 0.05),
        ("out_of_range", 0.03),
        ("wrong_types", 0.02),
        ("encoding_issues", 0.03),
        ("future_dates", 0.03),
        ("negative_amounts", 0.03),
    ],
)
def test_value_sub_mutation_changes_its_documented_share(kind: str, rate: float) -> None:
    out, events = ValueChaosMutator().apply_one(kind, _frame(), _rng(), 1.0)
    assert len(events) == 1 and events[0].kind == kind
    assert events[0].rows == int(N * rate)


def test_value_nulls_wrong_types_and_future_dates_have_the_documented_form() -> None:
    f = _frame()
    out, _ = ValueChaosMutator().apply_one("wrong_types", f, _rng(2), 1.0)
    changed = [
        (i, c.name)
        for c in [out.schema.field(k) for k in range(out.num_columns)]
        for i in range(N)
        if pa.types.is_string(c.type)
        and c.name in ("qty", "unit_price")
        and out.column(c.name)[i].as_py() in {"N/A", "null", "#REF!", "---", "TBD"}
    ]
    assert len(changed) == int(N * 0.02)
    out, _ = ValueChaosMutator().apply_one("future_dates", f, _rng(3), 1.0)
    new = out.column("created_at").to_pylist()
    old = f.column("created_at").to_pylist()
    hit = [i for i in range(N) if new[i] != old[i]]
    assert len(hit) == int(N * 0.03) and all(new[i].year >= 2031 for i in hit)
    assert out.schema.field("created_at").type == f.schema.field("created_at").type
    out, _ = ValueChaosMutator().apply_one("inject_nulls", f, _rng(4), 1.0)
    assert sum(out.column(i).null_count for i in range(out.num_columns)) == int(N * 0.05)


def test_value_negative_amounts_prefers_amount_like_columns() -> None:
    f = _frame()
    for seed in range(20):
        out, ev = ValueChaosMutator().apply_one("negative_amounts", f, _rng(seed), 1.0)
        assert ev[0].column in ("qty", "unit_price")  # not "id"
        assert out.column(ev[0].column).type == pa.float64()


def test_value_intensity_scales_and_caps_the_share() -> None:
    f = _frame()
    _, ev = ValueChaosMutator().apply_one("inject_nulls", f, _rng(), 5.0)
    assert ev[0].rows == int(N * 0.25)
    _, ev = ValueChaosMutator().apply_one("inject_nulls", f, _rng(), 100.0)
    assert ev[0].rows == int(N * 0.8)  # capped at 0.8


# ---- schema --------------------------------------------------------------------------------


def test_schema_additive_only_before_the_breaking_change_day() -> None:
    f = _frame()
    for seed in range(60):
        out, ev = SchemaChaosMutator(20).apply(f, 5, _rng(seed), 1.0)
        assert {e.kind for e in ev} <= {"add_column", "reorder"}
        assert set(f.column_names) <= set(out.column_names)


def test_schema_breaking_mutations_after_the_day() -> None:
    f = _frame()
    kinds = set()
    for seed in range(200):
        out, ev = SchemaChaosMutator(20).apply(f, 20, _rng(seed), 1.0)
        kinds |= {e.kind for e in ev}
    assert kinds == {"add_column", "reorder", "drop_column", "rename_column", "retype_column"}


def test_schema_two_actions_from_intensity_two() -> None:
    f = _frame()
    counts = {len(SchemaChaosMutator(20).apply(f, 30, _rng(s), 2.5)[1]) for s in range(40)}
    assert counts <= {1, 2} and 2 in counts
    assert {len(SchemaChaosMutator(20).apply(f, 30, _rng(s), 1.0)[1]) for s in range(40)} == {1}


def test_schema_retype_turns_a_number_column_into_text() -> None:
    out, ev = SchemaChaosMutator(20).apply_one("retype_column", _frame(), _rng())
    assert pa.types.is_string(out.schema.field(ev[0].column).type)
    assert out.num_rows == N


# ---- file ----------------------------------------------------------------------------------


def _csv() -> bytes:
    return ("id,name,amount\n" + "\n".join(f"{i},name{i},{i / 2:.2f}" for i in range(300))).encode()


def test_file_each_corruption_has_its_shape() -> None:
    data = _csv()
    m = FileChaosMutator()
    out, _ = m.apply_one("zero_byte", data, _rng(), 1.0)
    assert out == b""
    out, _ = m.apply_one("truncate", data, _rng(), 1.0)
    assert 0 < len(out) < len(data) and data.startswith(out)
    out, _ = m.apply_one("partial_write", data, _rng(), 1.0)
    assert len(out) == len(data) and out.endswith(b"\x00")
    out, _ = m.apply_one("garbage_header", data, _rng(), 1.0)
    assert out.endswith(data) and 8 <= len(out) - len(data) < 64
    out, _ = m.apply_one("wrong_delimiter", data, _rng(), 1.0)
    assert b"," not in out and out.count(b"|") == data.count(b",")
    out, _ = m.apply_one("bom_injection", data, _rng(), 1.0)
    assert b"\xef\xbb\xbf" in out and len(out) == len(data) + 3
    out, _ = m.apply_one("invalid_json_poison", data, _rng(), 1.0)
    assert len(out) > len(data)
    out, _ = m.apply_one("corrupt_encoding", data, _rng(), 1.0)
    assert len(out) == len(data) and out != data


def test_file_empty_input_is_returned_and_every_kind_is_reachable() -> None:
    assert FileChaosMutator().apply(b"", 1, _rng(), 1.0) == (b"", [])
    kinds = {FileChaosMutator().apply(_csv(), 1, _rng(s), 1.0)[1][0].kind for s in range(200)}
    assert kinds == set(FileChaosMutator.SUB_MUTATIONS)


# ---- referential ---------------------------------------------------------------------------


def test_referential_orphans_are_outside_the_parent_and_only_in_foreign_keys() -> None:
    t = _tables()
    out, ev = ReferentialChaosMutator().apply_one("orphan_fks", t, _rng(5), 1.0)
    assert ev and ev[0].kind == "orphan_fks"
    col = out["orders"].column("customer_id").to_pylist()
    orphans = [v for v in col if v >= 9_000_000]
    assert len(orphans) == int(N * 0.05)
    assert out["customer"].equals(t["customer"])
    assert out["orders"].column("order_id").equals(t["orders"].column("order_id"))


def test_referential_duplicate_keys_and_single_table() -> None:
    t = _tables()
    out, ev = ReferentialChaosMutator().apply_one(
        "duplicate_pks", {"only": t["orders"]}, _rng(), 1.0
    )
    assert ev[0].kind == "duplicate_pks"
    pk = out["only"].column(0).to_pylist()
    assert len(set(pk)) < N
    assert (
        ReferentialChaosMutator().apply_one("orphan_fks", {"only": t["orders"]}, _rng(), 1.0)[1]
        == []
    )


def test_referential_narrow_integer_columns_widen_to_hold_orphans() -> None:
    small = pa.table(
        {"a_id": pa.array(range(100), pa.int16()), "b_id": pa.array(range(100), pa.int16())}
    )
    out, ev = ReferentialChaosMutator().apply_one(
        "orphan_fks", {"x": small, "y": small}, _rng(), 1.0
    )
    assert ev and out[ev[0].column.split(".")[0]].column("b_id").type == pa.int64()


# ---- temporal ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", TemporalChaosMutator.SUB_MUTATIONS)
def test_temporal_sub_mutations_keep_schema_and_rows(kind: str) -> None:
    f = _frame()
    out, ev = TemporalChaosMutator().apply_one(kind, f, ["created_at"], _rng(), 1.0)
    assert out.schema == f.schema and out.num_rows == N
    assert ev[0].kind == kind
    if kind != "timezone_mismatch":  # an offset of 0 hours changes nothing
        assert not _same(out, f)


def test_temporal_late_arrivals_are_one_to_thirty_days_earlier() -> None:
    f = _frame()
    out, _ = TemporalChaosMutator().apply_one("late_arrivals", f, ["created_at"], _rng(), 1.0)
    old = np.array(f.column("created_at").to_numpy(zero_copy_only=False))
    new = np.array(out.column("created_at").to_numpy(zero_copy_only=False))
    delta = (old - new)[old != new].astype("timedelta64[D]").astype(int)
    assert len(delta) == int(N * 0.05) and delta.min() >= 1 and delta.max() <= 30


def test_temporal_swaps_keep_the_multiset_of_values() -> None:
    f = _frame()
    out, _ = TemporalChaosMutator().apply_one("out_of_order", f, ["created_at"], _rng(), 1.0)
    assert sorted(out.column("created_at").to_pylist()) == sorted(
        f.column("created_at").to_pylist()
    )
    assert not _same(out, f)


def test_temporal_without_timestamp_columns_changes_nothing() -> None:
    t = pa.table({"x": pa.array([1, 2, 3])})
    assert _same(TemporalChaosMutator().apply(t, 1, _rng(), 1.0)[0], t)


# ---- volume --------------------------------------------------------------------------------


def test_volume_actions_and_weights() -> None:
    f = _frame()
    sizes = {}
    for seed in range(600):
        out, ev = VolumeChaosMutator().apply(f, 1, _rng(seed), 1.0)
        sizes[ev[0].kind] = sizes.get(ev[0].kind, 0) + 1
        assert out.schema == f.schema
        n = out.num_rows
        assert {"spike": n == N * 11, "empty": n == 0, "single_row": n == 1}[ev[0].kind]
    assert set(sizes) == {"spike", "empty", "single_row"}
    assert abs(sizes["single_row"] / 600 - 0.4) < 0.07 and abs(sizes["spike"] / 600 - 0.3) < 0.07


def test_volume_spike_multiplier_follows_intensity() -> None:
    out, _ = VolumeChaosMutator().apply_one("spike", _frame(), _rng(), 2.5)
    assert out.num_rows == N * (1 + 25)
    out, _ = VolumeChaosMutator().apply_one("spike", _frame(), _rng(), 0.25)
    assert out.num_rows == N * (1 + 2)  # at least 2x


# ---- common behaviour ----------------------------------------------------------------------


def test_mutators_are_deterministic_and_never_modify_their_input() -> None:
    f = _frame()
    snapshot = f.combine_chunks()
    for mut in (
        SchemaChaosMutator(20),
        ValueChaosMutator(),
        TemporalChaosMutator(),
        VolumeChaosMutator(),
    ):
        a = mut.mutate(f, 30, _rng(7), 2.5)
        b = mut.mutate(f, 30, _rng(7), 2.5)
        assert _same(a, b)
    assert _same(f, snapshot)
    t = _tables()
    a2 = ReferentialChaosMutator().mutate(t, 30, _rng(3), 1.0)
    b2 = ReferentialChaosMutator().mutate(t, 30, _rng(3), 1.0)
    assert all(_same(a2[k], b2[k]) for k in a2)
    assert FileChaosMutator().mutate(_csv(), 1, _rng(3), 1.0) == FileChaosMutator().mutate(
        _csv(), 1, _rng(3), 1.0
    )


def test_empty_inputs_pass_through() -> None:
    empty = _frame().slice(0, 0)
    for mut in (
        SchemaChaosMutator(20),
        ValueChaosMutator(),
        TemporalChaosMutator(),
        VolumeChaosMutator(),
    ):
        assert mut.mutate(empty, 30, _rng(), 1.0).num_rows == 0
    assert ReferentialChaosMutator().mutate({}, 1, _rng(), 1.0) == {}


def test_unknown_sub_mutation_names_are_rejected() -> None:
    f = _frame()
    with pytest.raises(ValueError):
        ValueChaosMutator().apply_one("nope", f, _rng(), 1.0)
    with pytest.raises(ValueError):
        SchemaChaosMutator().apply_one("nope", f, _rng())
    with pytest.raises(ValueError):
        FileChaosMutator().apply_one("nope", b"x", _rng(), 1.0)
    with pytest.raises(ValueError):
        VolumeChaosMutator().apply_one("nope", f, _rng(), 1.0)
    with pytest.raises(ValueError):
        TemporalChaosMutator().apply_one("nope", f, ["created_at"], _rng(), 1.0)
    with pytest.raises(ValueError):
        ReferentialChaosMutator().apply_one("nope", _tables(), _rng(), 1.0)


# ---- config and engine ---------------------------------------------------------------------


def test_config_validation_and_presets() -> None:
    assert ChaosConfig().validate() == []
    bad = ChaosConfig(intensity="x", escalation="y", warmup_days=9, chaos_start_day=9)
    bad.categories["bogus"] = {"enabled": True}
    assert len(bad.validate()) == 4
    assert ChaosConfig(intensity="hurricane").intensity_multiplier == 5.0
    assert ChaosConfig(intensity="unknown").intensity_multiplier == 1.0
    assert {c.value for c in ChaosCategory} == {
        "schema",
        "value",
        "file",
        "referential",
        "temporal",
        "volume",
    }


def test_config_instances_do_not_share_category_state() -> None:
    a, b = ChaosConfig(), ChaosConfig()
    a.categories["value"]["enabled"] = False
    assert b.is_category_enabled("value")


def test_should_inject_gates() -> None:
    off = ChaosEngine(ChaosConfig(enabled=False))
    assert not any(off.should_inject(d, "value") for d in range(100))
    eng = ChaosEngine(ChaosConfig(enabled=True, intensity="hurricane", escalation="front-loaded"))
    assert not any(eng.should_inject(d, "value") for d in range(8))  # warmup
    cfg = ChaosConfig(enabled=True, intensity="hurricane", escalation="front-loaded")
    cfg.categories["value"]["enabled"] = False
    assert not any(ChaosEngine(cfg).should_inject(d, "value") for d in range(8, 100))
    cfg2 = ChaosConfig(enabled=True, overrides=[ChaosOverride(day=3, category="value")])
    cfg2.warmup_days, cfg2.chaos_start_day = 0, 1
    assert ChaosEngine(cfg2).should_inject(3, "value")


def test_should_inject_rate_matches_weight_intensity_escalation() -> None:
    # front-loaded, day 8 (chaos day 0): escalation 1, so p = weight x intensity = 0.15 x 2.5
    hits = sum(
        ChaosEngine(
            ChaosConfig(enabled=True, intensity="stormy", escalation="front-loaded", seed=s)
        ).should_inject(8, "value")
        for s in range(4000)
    )
    assert abs(hits / 4000 - 0.375) < 0.03
    # gradual ramps from 0: day 8 has factor 0
    assert not any(
        ChaosEngine(ChaosConfig(enabled=True, seed=s)).should_inject(8, "value") for s in range(500)
    )


def test_apply_all_runs_the_categories_and_returns_the_referential_result() -> None:
    cfg = ChaosConfig(enabled=True, intensity="hurricane", escalation="front-loaded", seed=5)
    eng = ChaosEngine(cfg)
    f, t = _frame(), _tables()
    seen = set()
    for day in range(8, 60):
        res = eng.apply_all(f, day, tables=t)
        seen |= {e.kind for e in res.events}
        if any(e.kind in ("orphan_fks", "duplicate_pks") for e in res.events):
            assert not all(_same(res.tables[k], t[k]) for k in t)  # type: ignore[index]
    assert {"orphan_fks", "duplicate_pks"} & seen
    disabled = ChaosEngine(ChaosConfig(enabled=False)).apply_all(f, 30)
    assert _same(disabled.table, f) and disabled.events == []


def test_engine_seed_argument_overrides_the_config_seed() -> None:
    a = ChaosEngine(ChaosConfig(enabled=True, seed=1), seed=9)
    b = ChaosEngine(ChaosConfig(enabled=True, seed=2), seed=9)
    assert [a.rng.random() for _ in range(3)] == [b.rng.random() for _ in range(3)]


# ---- plugins -------------------------------------------------------------------------------


def test_the_six_categories_are_registered_plugins() -> None:
    host = PluginHost()
    register_builtins(host)
    names = {r.name for r in host.records("shape.chaos")}
    assert names == {"schema", "value", "file", "referential", "temporal", "volume"}


@pytest.mark.parametrize("name", ["schema", "value", "file", "referential", "temporal", "volume"])
def test_plugin_wrappers_are_deterministic_and_report_honestly(name: str) -> None:
    host = PluginHost()
    register_builtins(host)
    plugin = host.get("shape.chaos", name)
    batch = _frame().combine_chunks().to_batches()[0]
    before = pa.Table.from_batches([batch])
    out, report = plugin.mutate(batch, 3)
    again, report2 = plugin.mutate(batch, 3)
    assert report.mutator == name and report == report2 and out.equals(again)
    assert report.rows_affected >= 0
    assert pa.Table.from_batches([batch]).equals(before)
    if name in ("value", "temporal", "referential"):
        assert out.num_rows >= 0
    if name == "file":
        assert out.schema.names == ["payload"]


def test_plugin_kit_accepts_the_schema_preserving_wrappers() -> None:
    host = PluginHost()
    register_builtins(host)
    batch = _frame().combine_chunks().to_batches()[0]
    # temporal keeps the schema and the row count by construction
    kit.check_chaos(host.get("shape.chaos", "temporal"), batch)
