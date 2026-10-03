"""Financial patterns: reversals, fraud bursts, settlement batches."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest
from shape_simulation.financial_patterns import FinancialStreamConfig, FinancialStreamSimulator

HOUR = 3_600_000_000


def sim(tables, **cfg):
    return FinancialStreamSimulator(tables=tables, config=FinancialStreamConfig(**cfg))


def times(table, col="transaction_time"):
    return np.asarray(table.column(col).cast(pa.int64()).to_numpy(), dtype=np.int64)


def test_deterministic_and_seed_sensitive(financial_tables):
    a = sim(financial_tables, seed=5, reversal_probability=0.1).run()
    b = sim(financial_tables, seed=5, reversal_probability=0.1).run()
    c = sim(financial_tables, seed=6, reversal_probability=0.1).run()
    assert all(a.table_map()[k].equals(b.table_map()[k]) for k in a.TABLES)
    assert not a.reversals.equals(c.reversals)


def test_reversals_mirror_their_originals(financial_tables):
    r = sim(financial_tables, reversal_probability=0.2, reversal_delay_hours_max=6.0).run()
    txn = financial_tables["transaction"].to_pydict()
    amount = dict(zip(txn["transaction_id"], txn["amount"], strict=True))
    when = dict(
        zip(txn["transaction_id"], times(financial_tables["transaction"]).tolist(), strict=True)
    )
    rev = r.reversals.to_pydict()
    assert 0.15 < len(rev["reversal_id"]) / 1500 < 0.25
    ids = rev["original_transaction_id"]
    assert len(set(ids)) == len(ids) and set(ids) <= set(amount)
    for a, o, reason in zip(rev["amount"], ids, rev["reversal_reason"], strict=True):
        assert a == pytest.approx(-abs(amount[o])) and reason in {
            "customer_dispute",
            "duplicate",
            "fraud_confirmed",
            "error",
        }
    delay = (times(r.reversals, "reversed_at") - np.array([when[o] for o in ids])) / HOUR
    assert delay.min() >= 0.1 - 1e-6 and delay.max() <= 6.0 + 1e-6
    assert (
        r.reversals.column("account_id").type
        == financial_tables["transaction"].schema.field("account_id").type
    )


def test_combined_table_is_the_original_then_reversals_then_fraud(financial_tables):
    r = sim(financial_tables, reversal_probability=0.1, fraud_burst_probability=0.5).run()
    n0, nr, nf = 1500, r.reversals.num_rows, r.fraud_events.num_rows
    assert nr > 0 and nf > 0 and r.transactions.num_rows == n0 + nr + nf
    assert r.transactions.column_names == [
        "transaction_id",
        "account_id",
        "amount",
        "transaction_time",
        "reversal_id",
        "original_transaction_id",
        "reversal_reason",
        "reversed_at",
        "fraud_tx_id",
        "merchant_category",
        "is_fraud",
    ]
    assert r.transactions.column("reversal_id").null_count == n0 + nf
    assert (
        r.transactions.slice(0, n0)
        .column("amount")
        .equals(financial_tables["transaction"].column("amount"))
    )
    assert r.stats["combined_transaction_count"] == n0 + nr + nf


def test_fraud_bursts(financial_tables):
    r = sim(
        financial_tables,
        fraud_burst_probability=0.5,
        fraud_burst_count=8,
        fraud_burst_amount_range=(100.0, 200.0),
        duration_hours=20.0,
    ).run()
    f = r.fraud_events.to_pydict()
    assert len(f["fraud_tx_id"]) > 0 and len(f["fraud_tx_id"]) % 8 == 0
    assert all(100.0 <= a <= 200.0 for a in f["amount"]) and all(f["is_fraud"])
    assert set(f["account_id"]) <= set(financial_tables["account"].column("account_id").to_pylist())
    start = times(financial_tables["transaction"]).min()
    t = times(r.fraud_events)
    assert t.min() >= start and ((t - start) % HOUR).max() <= 120_000_000
    assert set(f["merchant_category"]) <= {
        "electronics",
        "jewelry",
        "gift_cards",
        "cryptocurrency",
        "wire_transfer",
        "online_gambling",
        "luxury_goods",
    }


def test_settlement_windows_match_the_transactions(financial_tables):
    r = sim(
        financial_tables,
        duration_hours=48.0,
        settlement_batch_hours=6.0,
        settlement_success_rate=1.0,
    ).run()
    t = times(financial_tables["transaction"])
    amount = np.asarray(financial_tables["transaction"].column("amount").to_numpy())
    s = r.settlements.to_pydict()
    assert (
        len(s["batch_id"]) == 8
        and set(s["status"]) == {"settled"}
        and set(s["failure_reason"]) == {None}
    )
    for i in range(8):
        lo = t.min() + i * 6 * HOUR
        inside = (t >= lo) & (t < lo + 6 * HOUR)
        assert s["transaction_count"][i] == int(inside.sum())
        assert s["total_amount"][i] == pytest.approx(round(float(amount[inside].sum()), 2))
    assert sum(s["transaction_count"]) == 1500


def test_settlement_outcomes(financial_tables):
    r = sim(
        financial_tables,
        duration_hours=400.0,
        settlement_batch_hours=1.0,
        settlement_success_rate=0.5,
    ).run()
    s = r.settlements.to_pydict()
    n = len(s["batch_id"])
    assert n == 400
    share = {k: s["status"].count(k) / n for k in ("settled", "partial", "failed")}
    assert 0.4 < share["settled"] < 0.6 and share["partial"] > 0.1 and share["failed"] > 0.1
    for status, reason, amount in zip(
        s["status"], s["failure_reason"], s["total_amount"], strict=True
    ):
        assert (reason is None) == (status == "settled")
        if status == "failed":
            assert amount == 0.0


def test_switches_return_typed_empty_tables(financial_tables):
    r = sim(
        financial_tables,
        reversal_enabled=False,
        fraud_burst_enabled=False,
        settlement_enabled=False,
    ).run()
    assert r.reversals.num_rows == r.fraud_events.num_rows == r.settlements.num_rows == 0
    assert (
        r.reversals.column_names[0] == "reversal_id"
        and r.settlements.schema.field("transaction_count").type == pa.int64()
    )
    assert r.transactions.equals(financial_tables["transaction"])  # nothing enabled adds no columns


def test_columns_follow_the_configuration_not_the_run(financial_tables):
    """A run with no fraud burst (or reversal) has the same columns as one with them."""
    full = (
        sim(financial_tables, reversal_probability=0.5, fraud_burst_probability=1.0)
        .run()
        .transactions.column_names
    )
    for seed in range(8):
        r = sim(
            financial_tables, seed=seed, reversal_probability=0.0, fraud_burst_probability=0.0
        ).run()
        assert r.reversals.num_rows == r.fraud_events.num_rows == 0
        assert r.transactions.column_names == full and r.transactions.num_rows == 1500
    only = (
        sim(financial_tables, fraud_burst_enabled=False, reversal_probability=0.5)
        .run()
        .transactions.column_names
    )
    assert "reversal_id" in only and "fraud_tx_id" not in only


def test_domain_style_time_column_and_decimal_amounts(financial_tables):
    t = financial_tables["transaction"]
    renamed = t.rename_columns(["transaction_id", "account_id", "amount", "transaction_date"])
    dec = renamed.set_column(2, "amount", renamed.column("amount").cast(pa.decimal128(12, 2)))
    r = sim(
        {"transaction": dec, "account": financial_tables["account"]},
        reversal_probability=0.2,
        duration_hours=48.0,
        settlement_batch_hours=12.0,
    ).run()
    assert sum(r.settlements.column("transaction_count").to_pylist()) == 1500
    assert r.transactions.schema.field("amount").type == pa.float64()
    assert times(r.reversals, "reversed_at").min() > times(dec, "transaction_date").min()


def test_without_a_time_column_batches_are_even_slices(financial_tables):
    t = financial_tables["transaction"].select(["transaction_id", "account_id", "amount"])
    r = sim(
        {"transaction": t, "account": financial_tables["account"]},
        duration_hours=24.0,
        settlement_batch_hours=4.0,
        reversal_probability=0.2,
    ).run()
    assert r.settlements.column("transaction_count").to_pylist() == [250] * 6
    assert r.reversals.num_rows > 0 and r.reversals.column("reversed_at").null_count == 0


def test_inputs_are_not_modified_and_arguments_are_checked(financial_tables):
    before = financial_tables["transaction"]
    sim(financial_tables, reversal_probability=0.5).run()
    assert financial_tables["transaction"].equals(before)
    with pytest.raises(ValueError, match="tables= or both"):
        FinancialStreamSimulator()
    with pytest.raises(ValueError, match="amount"):
        FinancialStreamSimulator(before.drop(["amount"]), financial_tables["account"])
    r = FinancialStreamSimulator(before, financial_tables["account"].to_pandas()).run()
    assert r.stats["original_transaction_count"] == 1500


def test_empty_transactions(financial_tables):
    empty = financial_tables["transaction"].slice(0, 0)
    r = sim({"transaction": empty, "account": financial_tables["account"]}).run()
    assert r.reversals.num_rows == 0 and r.settlements.num_rows == 6
    assert "FinancialStreamResult(transactions=0" in repr(r)


# ---- the default window: the whole span of the transactions -------------------------------


def months_of(table, col):
    us = times(table, col)
    return {(np.datetime64(int(v), "us").astype("datetime64[M]")) for v in us}


@pytest.fixture
def multi_month_tables():
    """Transactions across 120 days (five months of 30 days) and their accounts."""
    rng = np.random.default_rng(3)
    n = 4000
    start = np.datetime64("2024-01-01T00:00:00", "us").astype(np.int64)
    when = np.sort(start + (rng.random(n) * 120 * 24 * HOUR).astype(np.int64))
    accounts = pa.table({"account_id": pa.array([f"acc_{i}" for i in range(50)])})
    txn = pa.table(
        {
            "transaction_id": pa.array([f"t{i}" for i in range(n)]),
            "account_id": pa.array([f"acc_{i % 50}" for i in range(n)]),
            "amount": pa.array(np.round(rng.uniform(1, 200, n), 2)),
            "transaction_time": pa.array(when, pa.timestamp("us")),
        }
    )
    return {"transaction": txn, "account": accounts}


def test_default_window_settles_every_month(multi_month_tables):
    r = sim(multi_month_tables, settlement_success_rate=1.0).run()
    txn = multi_month_tables["transaction"]
    seen = months_of(r.settlements, "settled_at")
    assert months_of(txn, "transaction_time") <= seen
    s = r.settlements.to_pydict()
    assert set(s["status"]) == {"settled"}
    # every transaction falls in a batch: nothing is left unsettled at the end of the span
    assert sum(s["transaction_count"]) == txn.num_rows
    assert times(r.settlements, "settled_at").max() >= times(txn).max()
    span_hours = float(times(txn).max() - times(txn).min()) / HOUR
    assert r.stats["duration_hours"] == pytest.approx(span_hours + 4.0)


def test_default_window_covers_fraud_and_clearing_for_the_whole_period(multi_month_tables):
    r = sim(multi_month_tables, fraud_burst_probability=0.01).run()
    assert len(months_of(r.fraud_events, "transaction_time")) >= 4
    assert sum(r.settlements.column("transaction_count").to_pylist()) == 4000


def test_settlement_lag_distribution_is_unchanged(multi_month_tables):
    """A transaction settles at the end of its batch: the lag is in (0, batch hours], spread
    evenly, whatever the window, and the status mix follows the success rate."""
    txn = multi_month_tables["transaction"]
    t = times(txn)
    batch = 4.0
    for cfg in ({}, {"duration_hours": 120 * 24.0 + batch}):
        r = sim(multi_month_tables, settlement_success_rate=0.9, seed=11, **cfg).run()
        ends = np.sort(times(r.settlements, "settled_at"))
        idx = np.searchsorted(ends, t, side="right")  # the first batch end after each transaction
        lag = (ends[idx] - t) / HOUR
        assert lag.min() > 0 and lag.max() <= batch + 1e-9
        assert abs(lag.mean() - batch / 2) < 0.1
        share = r.settlements.column("status").to_pylist().count("settled") / r.settlements.num_rows
        assert 0.85 < share < 0.95


def test_explicit_window_equals_the_default_when_it_is_the_same_length(multi_month_tables):
    default = sim(multi_month_tables, seed=9).run()
    same = sim(multi_month_tables, seed=9, duration_hours=default.stats["duration_hours"]).run()
    assert all(default.table_map()[k].equals(same.table_map()[k]) for k in default.TABLES)


def test_duration_hours_still_overrides_the_default_window(multi_month_tables):
    r = sim(multi_month_tables, duration_hours=48.0, settlement_batch_hours=6.0).run()
    assert r.settlements.num_rows == 8 and r.stats["duration_hours"] == 48.0
    only_first_two_days = months_of(r.settlements, "settled_at")
    assert len(only_first_two_days) == 1
    t = times(multi_month_tables["transaction"])
    assert times(r.settlements, "settled_at").max() <= t.min() + 48 * HOUR


def test_default_window_without_a_time_column_is_a_day(financial_tables):
    bare = {
        "transaction": financial_tables["transaction"].drop_columns(["transaction_time"]),
        "account": financial_tables["account"],
    }
    r = sim(bare).run()
    assert r.stats["duration_hours"] == 24.0 and r.settlements.num_rows == 6


def test_a_far_future_time_or_huge_window_is_refused_not_simulated():
    # Issue #413: one sentinel date (9999-12-31) made the default window ~70M hours, and the
    # run made a settlement batch and a fraud draw for each of them (6.7 GB, 47 s).
    import datetime as dt

    tx = pa.table(
        {
            "transaction_id": [1, 2, 3],
            "account_id": [1, 2, 3],
            "amount": [5.0, 6.0, 7.0],
            "transaction_time": [
                dt.datetime(2024, 1, 1),
                dt.datetime(2024, 1, 2),
                dt.datetime(9999, 12, 31),
            ],
        }
    )
    accounts = pa.table({"account_id": [1, 2, 3]})
    with pytest.raises(ValueError, match="duration_hours"):
        FinancialStreamSimulator(tx, accounts, FinancialStreamConfig()).run()
    with pytest.raises(ValueError, match="duration_hours"):
        FinancialStreamSimulator(tx, accounts, FinancialStreamConfig(duration_hours=1e10)).run()
    with pytest.raises(ValueError, match="settlement_batch_hours"):
        FinancialStreamSimulator(
            tx, accounts, FinancialStreamConfig(duration_hours=24 * 365, settlement_batch_hours=1e-4)
        ).run()
    # a year of data is fine
    ok = FinancialStreamSimulator(
        tx.slice(0, 2), accounts, FinancialStreamConfig(duration_hours=24 * 365)
    ).run()
    assert ok.settlements.num_rows == 24 * 365 // 4  # one batch per settlement_batch_hours
