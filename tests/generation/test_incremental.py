"""P6-05: ``ContinueEngine`` and ``TimeTravelEngine`` on small hand-made tables.

One test per behaviour of the port, and one regression test per trust fix (the names ``CONT-*`` and
``TT-*`` are the allow-list entries of ``docs/plans/lane_status/P6-05.md``).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pytest

from shape.generation.incremental import (
    DELTA_TIMESTAMP_COLUMN,
    DELTA_TYPE_COLUMN,
    ContinueConfig,
    ContinueEngine,
    IncrementalError,
    TimeTravelConfig,
    TimeTravelEngine,
    add_months,
    parse_seasonality,
)
from shape.generation.schema import Column, GenSchema, Model, Relationship, Table

N_PARENT, N_CHILD = 200, 1000


def schema() -> GenSchema:
    def table(name: str, key: str, columns: list[Column]) -> Table:
        return Table(name, {c.name: c for c in columns}, [key])

    seq = {"strategy": "sequence"}
    parent = table(
        "parent",
        "parent_id",
        [
            Column("parent_id", "integer", seq),
            Column("region_id", "integer", {"strategy": "x"}),
            Column("tier", "string", {"strategy": "x"}),
        ],
    )
    fk = {"strategy": "foreign_key", "ref": "parent.parent_id"}
    child = table(
        "child",
        "child_id",
        [
            Column("child_id", "integer", seq),
            Column("parent_id", "integer", fk),
            Column("amount", "decimal", {"strategy": "x"}),
            Column("quantity", "integer", {"strategy": "x"}),
            Column("status", "string", {"strategy": "x"}),
            Column("placed", "datetime", {"strategy": "x"}),
        ],
    )
    rel = Relationship("child_parent", "parent", "child", ["parent_id"], ["parent_id"])
    return GenSchema(Model(name="t", domain="t", seed=7), {"parent": parent, "child": child}, [rel])


def tables(seed: int = 1) -> dict[str, pa.Table]:
    rng = np.random.default_rng(seed)
    # `region_id` comes first: an integer `*_id` column that is not the key, which a guess picks.
    parent = pa.table(
        {
            "region_id": pa.array(rng.integers(1, 6, N_PARENT), pa.int64()),
            "parent_id": pa.array(np.arange(1000, 1000 + N_PARENT), pa.int64()),
            "tier": pa.array(rng.choice(["a", "b", "c"], N_PARENT).tolist()),
        }
    )
    child = pa.table(
        {
            "child_id": pa.array(np.arange(1, N_CHILD + 1), pa.int64()),
            "parent_id": pa.array(
                rng.choice(np.arange(1000, 1000 + N_PARENT), N_CHILD), pa.int64()
            ),
            "amount": pa.array(rng.uniform(10, 500, N_CHILD)),
            "quantity": pa.array(rng.integers(1, 6, N_CHILD), pa.int64()),
            "status": pa.array(rng.choice(["pending", "shipped", "done"], N_CHILD).tolist()),
            "placed": pa.array(
                [
                    dt.datetime(2024, 1, 1) + dt.timedelta(days=int(d))
                    for d in rng.integers(0, 300, N_CHILD)
                ],
                pa.timestamp("us"),
            ),
        }
    )
    return {"parent": parent, "child": child}


def keys(table: pa.Table, column: str) -> set[int]:
    return set(table.column(column).to_pylist())


# ---- continue -----------------------------------------------------------------------------


def test_counts_columns_and_tags():
    data = tables()
    cfg = ContinueConfig(insert_count=30, update_fraction=0.1, delete_fraction=0.02, seed=3)
    delta = ContinueEngine().continue_from(data, schema(), cfg)
    assert delta.stats["parent"] == {"inserts": 30, "updates": 20, "deletes": 4}
    assert delta.stats["child"] == {"inserts": 30, "updates": 100, "deletes": 20}
    for name, table in delta.combined.items():
        assert table.column_names == data[name].column_names + [
            DELTA_TYPE_COLUMN,
            DELTA_TIMESTAMP_COLUMN,
        ]
        kinds = table.column(DELTA_TYPE_COLUMN).to_pylist()
        assert kinds.count("INSERT") == delta.stats[name]["inserts"]
        assert kinds.count("UPDATE") == delta.stats[name]["updates"]
        assert kinds.count("DELETE") == delta.stats[name]["deletes"]
    assert delta.summary().startswith("Incremental Generation Result")


def test_new_keys_continue_above_existing_and_are_not_reissued():
    data = tables()
    engine = ContinueEngine()
    cfg = ContinueConfig(insert_count=10, seed=1)
    first = (
        engine.continue_from(data, schema(), cfg).inserts["child"].column("child_id").to_pylist()
    )
    second = (
        engine.continue_from(data, schema(), cfg).inserts["child"].column("child_id").to_pylist()
    )
    assert first == list(range(N_CHILD + 1, N_CHILD + 11))
    assert second == list(range(N_CHILD + 11, N_CHILD + 21))


def test_seed_makes_the_delta_reproducible_and_as_of_stamps_it():
    data = tables()
    when = dt.datetime(2026, 1, 2, 3, 4, 5)
    cfg = ContinueConfig(insert_count=20, seed=9, as_of=when)
    a = ContinueEngine().continue_from(data, schema(), cfg)
    b = ContinueEngine().continue_from(data, schema(), cfg)
    assert all(a.combined[n].equals(b.combined[n]) for n in a.combined)
    stamps = set(a.combined["child"].column(DELTA_TIMESTAMP_COLUMN).to_pylist())
    assert stamps == {when}
    other = ContinueEngine().continue_from(
        data, schema(), ContinueConfig(insert_count=20, seed=10, as_of=when)
    )
    assert not a.combined["child"].equals(other.combined["child"])


def test_updates_keep_keys_and_foreign_keys_and_change_values():
    data = tables()
    delta = ContinueEngine().continue_from(data, schema(), ContinueConfig(insert_count=0, seed=4))
    upd, child = delta.updates["child"], data["child"]
    index = {k: i for i, k in enumerate(child.column("child_id").to_pylist())}
    rows = [index[k] for k in upd.column("child_id").to_pylist()]
    original = child.take(pa.array(rows))
    assert upd.column("parent_id").equals(original.column("parent_id"))
    changed = np.asarray(upd.column("amount").to_pylist()) != np.asarray(
        original.column("amount").to_pylist()
    )
    assert 0.1 < changed.mean() < 0.6  # about 30% of the values of a column change
    ratio = (
        np.asarray(upd.column("amount").to_pylist())[changed]
        / np.asarray(original.column("amount").to_pylist())[changed]
    )
    assert ratio.min() >= 0.9 and ratio.max() <= 1.1


def test_state_transitions():
    data = tables()
    transitions = {"child.status": {"pending": {"shipped": 1.0}, "shipped": {"done": 1.0}}}
    cfg = ContinueConfig(insert_count=0, update_fraction=0.5, seed=5, state_transitions=transitions)
    delta = ContinueEngine().continue_from(data, schema(), cfg)
    child = data["child"]
    index = {k: i for i, k in enumerate(child.column("child_id").to_pylist())}
    for k, new in zip(
        delta.updates["child"].column("child_id").to_pylist(),
        delta.updates["child"].column("status").to_pylist(),
        strict=True,
    ):
        old = child.column("status")[index[k]].as_py()
        assert new == {"pending": "shipped", "shipped": "done", "done": "done"}[old]


# ---- the trust fixes ----------------------------------------------------------------------


def test_cont_chain_a_row_moves_one_state_per_update():
    data = tables()
    # pending -> shipped and shipped -> done: a pending row must end `shipped`, not `done`.
    transitions = {"child.status": {"pending": {"shipped": 1.0}, "shipped": {"done": 1.0}}}
    cfg = ContinueConfig(
        insert_count=0,
        update_fraction=1.0,
        delete_fraction=0.0,
        seed=2,
        state_transitions=transitions,
    )
    delta = ContinueEngine().continue_from(data, schema(), cfg)
    before = dict(
        zip(
            data["child"].column("child_id").to_pylist(),
            data["child"].column("status").to_pylist(),
            strict=True,
        )
    )
    after = dict(
        zip(
            delta.updates["child"].column("child_id").to_pylist(),
            delta.updates["child"].column("status").to_pylist(),
            strict=True,
        )
    )
    pending = [k for k, v in before.items() if v == "pending"]
    assert pending and {after[k] for k in pending} == {"shipped"}


def test_booleans_are_never_flipped():
    data = tables()
    flags = pa.array(np.random.default_rng(0).random(N_CHILD) < 0.5)
    data["child"] = data["child"].append_column("active", flags)
    cfg = ContinueConfig(insert_count=100, update_fraction=1.0, delete_fraction=0.0, seed=1)
    delta = ContinueEngine().continue_from(data, schema(), cfg)
    index = {k: i for i, k in enumerate(data["child"].column("child_id").to_pylist())}
    for k, flag in zip(
        delta.updates["child"].column("child_id").to_pylist(),
        delta.updates["child"].column("active").to_pylist(),
        strict=True,
    ):
        assert flag == flags[index[k]].as_py()


def test_cont_zero_fraction_changes_no_rows():
    cfg = ContinueConfig(insert_count=0, update_fraction=0.0, delete_fraction=0.0, seed=1)
    delta = ContinueEngine().continue_from(tables(), schema(), cfg)
    assert all(s == {"inserts": 0, "updates": 0, "deletes": 0} for s in delta.stats.values())
    assert all(t.num_rows == 0 for t in delta.combined.values())
    # A tiny non-zero fraction still changes one row.
    tiny = ContinueEngine().continue_from(
        tables(),
        schema(),
        ContinueConfig(insert_count=0, update_fraction=0.0001, delete_fraction=0.0001, seed=1),
    )
    assert tiny.stats["child"] == {"inserts": 0, "updates": 1, "deletes": 1}


def test_cont_overlap_a_row_is_never_updated_and_deleted():
    for seed in range(10):
        cfg = ContinueConfig(insert_count=0, update_fraction=0.4, delete_fraction=0.4, seed=seed)
        delta = ContinueEngine().continue_from(tables(), schema(), cfg)
        for name, column in (("parent", "parent_id"), ("child", "child_id")):
            upd = keys(delta.updates[name], column)
            dele = keys(delta.deletes[name], column)
            assert not upd & dele
            assert len(upd) == delta.updates[name].num_rows  # nothing updated twice


def test_cont_overlap_everything_updated_leaves_nothing_to_delete():
    cfg = ContinueConfig(insert_count=0, update_fraction=1.0, delete_fraction=0.5, seed=1)
    delta = ContinueEngine().continue_from(tables(), schema(), cfg)
    assert delta.stats["child"] == {"inserts": 0, "updates": N_CHILD, "deletes": 0}


def test_cont_deleted_parent_inserted_children_reference_surviving_parents():
    data = tables()
    for seed in range(20):
        cfg = ContinueConfig(insert_count=400, update_fraction=0.0, delete_fraction=0.5, seed=seed)
        delta = ContinueEngine().continue_from(data, schema(), cfg)
        gone = keys(delta.deletes["parent"], "parent_id")
        alive = (keys(data["parent"], "parent_id") - gone) | keys(
            delta.inserts["parent"], "parent_id"
        )
        assert gone
        assert keys(delta.inserts["child"], "parent_id") <= alive


def test_cont_fk_column_draws_from_the_referenced_column_not_the_first_id_column():
    data = tables()
    delta = ContinueEngine().continue_from(data, schema(), ContinueConfig(insert_count=200, seed=2))
    fk_values = keys(delta.inserts["child"], "parent_id")
    valid = keys(data["parent"], "parent_id") | keys(delta.inserts["parent"], "parent_id")
    assert fk_values <= valid  # region_id values (1-5) would be orphans


def test_cont_fk_found_without_a_schema_by_unambiguous_key_name():
    data = tables()
    delta = ContinueEngine().continue_from(data, None, ContinueConfig(insert_count=100, seed=2))
    # Without a schema the key is guessed: the first integer *_id column, region_id, for parent.
    assert delta.inserts["child"].num_rows == 100


def test_cont_keys_non_integer_primary_key_is_an_error():
    data = tables()
    data["parent"] = data["parent"].set_column(
        1, "parent_id", pa.array([f"P{i}" for i in range(N_PARENT)])
    )
    with pytest.raises(IncrementalError, match="no integer column"):
        ContinueEngine().continue_from(data, schema(), ContinueConfig(insert_count=5, seed=1))


@pytest.mark.parametrize(
    "transitions, message",
    [
        ({"nope.status": {"a": {"b": 1.0}}}, "must be 'table.column'"),
        ({"child.nope": {"a": {"b": 1.0}}}, "no column"),
        ({"child.status": {"pending": {"shipped": 0.0}}}, "not all zero"),
        ({"child.status": {"pending": {"shipped": -1.0, "done": 2.0}}}, "non-negative"),
        ({"status": {"pending": {"shipped": 1.0}}}, "must be 'table.column'"),
    ],
)
def test_cont_transitions_invalid_specs_are_errors(transitions, message):
    cfg = ContinueConfig(insert_count=1, seed=1, state_transitions=transitions)
    with pytest.raises(IncrementalError, match=message):
        ContinueEngine().continue_from(tables(), schema(), cfg)


@pytest.mark.parametrize(
    "kwargs",
    [{"insert_count": -1}, {"update_fraction": 1.5}, {"delete_fraction": -0.1}],
)
def test_continue_config_validation(kwargs):
    with pytest.raises(ValueError):
        ContinueConfig(**kwargs)


def test_nulls_survive_perturbation():
    data = tables()
    child = data["child"]
    amount = child.column("amount").to_pylist()
    amount[::7] = [None] * len(amount[::7])
    data["child"] = child.set_column(2, "amount", pa.array(amount))
    delta = ContinueEngine().continue_from(data, schema(), ContinueConfig(insert_count=200, seed=3))
    assert delta.inserts["child"].column("amount").null_count > 0


# ---- time travel --------------------------------------------------------------------------


def run_tt(**kwargs):
    cfg = TimeTravelConfig(**{"months": 6, "seed": 11, **kwargs})
    return TimeTravelEngine().generate_from(tables(), cfg, schema=schema(), domain_name="t")


def test_time_travel_counts_follow_growth_churn_and_seasonality():
    result = run_tt(seasonality={3: 2.0}, growth_rate=0.05, churn_rate=0.02)
    assert [s.month_index for s in result.snapshots] == list(range(7))
    assert [s.snapshot_date for s in result.snapshots][:3] == [
        "2023-01-01",
        "2023-02-01",
        "2023-03-01",
    ]
    n = N_CHILD
    expected = [n]
    for month in range(1, 7):
        mult = 2.0 if (1 + month) == 3 else 1.0  # the month of the new snapshot date
        grown = max(1, int(n * 0.05 * mult))
        n = n - int(n * 0.02) + grown
        expected.append(n)
    assert [s.row_counts["child"] for s in result.snapshots] == expected
    assert result.get_snapshot(2).row_counts["child"] == expected[2]
    assert "Months" not in result.summary() and "Snapshots: 7" in result.summary()


def test_time_travel_keys_stay_unique_and_increase():
    result = run_tt()
    for snap in result.snapshots:
        ids = snap.tables["child"].column("child_id").to_pylist()
        assert len(set(ids)) == len(ids)
    assert max(result.snapshots[-1].tables["child"].column("child_id").to_pylist()) > N_CHILD


def test_time_travel_is_reproducible():
    a, b = run_tt(), run_tt()
    assert all(
        x.tables[n].equals(y.tables[n])
        for x, y in zip(a.snapshots, b.snapshots, strict=True)
        for n in x.tables
    )


def test_tt_zero_growth_adds_no_rows():
    result = run_tt(growth_rate=0.0, churn_rate=0.0, update_fraction=0.0, months=3)
    assert all(s.row_counts == {"parent": N_PARENT, "child": N_CHILD} for s in result.snapshots)


def test_tt_zero_season_multiplier_adds_no_rows_that_month():
    result = run_tt(seasonality={2: 0.0}, churn_rate=0.0, months=2)
    assert result.snapshots[1].row_counts["child"] == N_CHILD  # snapshot date 2023-02-01


def test_tt_orphans_every_snapshot_has_full_foreign_key_integrity():
    result = run_tt(months=8, churn_rate=0.2)
    for snap in result.snapshots:
        parents = keys(snap.tables["parent"], "parent_id")
        assert keys(snap.tables["child"], "parent_id") <= parents
    assert result.snapshots[-1].row_counts["parent"] < N_PARENT * 2


def test_tt_orphans_repair_keeps_the_skew_of_the_relationship():
    parent = pa.table({"parent_id": pa.array(range(1, 11), pa.int64())})
    # Parent 1 has 90 children, parents 2-5 have 2 each, and 40 children point at a removed parent.
    fk = [1] * 90 + [2, 2, 3, 3, 4, 4, 5, 5] + [99] * 40
    child = pa.table(
        {"child_id": pa.array(range(len(fk)), pa.int64()), "parent_id": pa.array(fk, pa.int64())}
    )
    current = {"parent": parent, "child": child}
    keys_ = {"parent": ["parent_id"], "child": ["child_id"]}
    fks = {"parent": {}, "child": {"parent_id": ("parent", "parent_id")}}
    TimeTravelEngine._repair_orphans(
        current, ["parent", "child"], keys_, fks, np.random.default_rng(1)
    )
    repaired = np.asarray(current["child"].column("parent_id").to_pylist())
    assert set(repaired.tolist()) <= set(range(1, 11))
    assert (repaired[:98] == np.asarray(fk[:98])).all()  # rows that were fine do not move
    # Proportional to the children each parent has (90 of 98): about 37 of the 40; uniform over the
    # 10 parents would give about 4.
    assert (repaired[98:] == 1).sum() >= 30


def test_tt_orphans_repair_when_every_child_lost_its_parent():
    parent = pa.table({"parent_id": pa.array([1, 2, 3], pa.int64())})
    child = pa.table(
        {
            "child_id": pa.array(range(6), pa.int64()),
            "parent_id": pa.array([99, 98, None, 99, 97, 96], pa.int64()),
        }
    )
    current = {"parent": parent, "child": child}
    keys_ = {"parent": ["parent_id"], "child": ["child_id"]}
    fks = {"parent": {}, "child": {"parent_id": ("parent", "parent_id")}}
    TimeTravelEngine._repair_orphans(
        current, ["parent", "child"], keys_, fks, np.random.default_rng(1)
    )
    repaired = current["child"].column("parent_id").to_pylist()
    assert repaired[2] is None  # a null foreign key is not an orphan
    assert all(v in (1, 2, 3) for i, v in enumerate(repaired) if i != 2)


def test_tt_rounding_integer_updates_do_not_drift_down():
    data = tables()
    data["child"] = data["child"].set_column(
        2,
        "amount",
        pa.array(["x"] * N_CHILD),  # no numeric column before quantity
    )
    cfg = TimeTravelConfig(months=6, growth_rate=0.0, churn_rate=0.0, update_fraction=1.0, seed=3)
    result = TimeTravelEngine().generate_from(data, cfg, schema=schema())
    before = np.asarray(data["child"].column("quantity").to_pylist())
    after = np.asarray(result.snapshots[-1].tables["child"].column("quantity").to_pylist())
    assert abs(after.mean() - before.mean()) < 0.3  # truncation lost about 0.5 per update
    assert after.min() >= 1 or before.min() < 1


def test_tt_keys_declared_primary_key_is_used_and_non_integer_is_an_error():
    data = tables()
    # The first unique integer *_id column is region_id-free here: the declared key wins.
    result = TimeTravelEngine().generate_from(
        data, TimeTravelConfig(months=1, seed=1), schema=schema()
    )
    ids = result.snapshots[1].tables["parent"].column("parent_id").to_pylist()
    assert len(set(ids)) == len(ids)
    data["parent"] = data["parent"].set_column(
        1, "parent_id", pa.array([f"P{i}" for i in range(N_PARENT)])
    )
    with pytest.raises(IncrementalError, match="no integer column"):
        TimeTravelEngine().generate_from(data, TimeTravelConfig(months=1, seed=1), schema=schema())


def test_to_partitioned_tables_adds_the_snapshot_date():
    result = run_tt(months=2)
    stacked = result.to_partitioned_tables()
    assert stacked["child"].num_rows == sum(s.row_counts["child"] for s in result.snapshots)
    assert set(stacked["child"].column("_shape_snapshot_date").to_pylist()) == {
        "2023-01-01",
        "2023-02-01",
        "2023-03-01",
    }


def test_time_travel_generates_month_zero_from_a_schema():
    from shape.generation.schema import GenSchema  # noqa: F401  (engine import path)

    pytest.importorskip("shape_domains")
    from shape.generation.domains import load_domain

    result = TimeTravelEngine().generate(
        load_domain("retail").schema, TimeTravelConfig(months=2, seed=5), scale="small"
    )
    assert result.domain_name == "retail"
    assert len(result.snapshots) == 3 and result.snapshots[0].row_counts["customer"] == 1000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"months": -1},
        {"growth_rate": -0.1},
        {"churn_rate": 1.1},
        {"update_fraction": -1},
        {"seasonality": {13: 1.0}},
        {"seasonality": {1: -1.0}},
        {"start_date": "yesterday"},
    ],
)
def test_time_travel_config_validation(kwargs):
    with pytest.raises(ValueError):
        TimeTravelConfig(**kwargs)


def test_add_months_clamps_to_the_end_of_the_month():
    assert add_months(dt.date(2023, 1, 31), 1) == dt.date(2023, 2, 28)
    assert add_months(dt.date(2024, 1, 31), 1) == dt.date(2024, 2, 29)
    assert add_months(dt.date(2023, 11, 15), 3) == dt.date(2024, 2, 15)
    assert add_months(dt.date(2023, 12, 1), 12) == dt.date(2024, 12, 1)


def test_parse_seasonality():
    assert parse_seasonality("11=1.5, 12:2") == {11: 1.5, 12: 2.0}
    assert parse_seasonality("") == {}
    with pytest.raises(ValueError):
        parse_seasonality("november=2")
