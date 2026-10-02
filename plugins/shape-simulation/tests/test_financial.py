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
