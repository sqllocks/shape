"""Parity case: ``financial_patterns`` (reversals, fraud bursts, settlement batches)."""

from __future__ import annotations

from typing import Any

import harness as h
import inputs as fixtures
import numpy as np
import pyarrow as pa
from harness import Col, Report, Run, TableSpec

NAME = "financial"
SIM = "financial"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
HOUR_US = 3_600_000_000
REASONS = frozenset({"customer_dispute", "duplicate", "fraud_confirmed", "error"})
MERCHANTS = frozenset(
    {
        "electronics",
        "jewelry",
        "gift_cards",
        "cryptocurrency",
        "wire_transfer",
        "online_gambling",
        "luxury_goods",
    }
)
FAILURES = frozenset(
    {
        "insufficient_funds",
        "account_closed",
        "compliance_hold",
        "network_timeout",
        "duplicate_batch",
    }
)


def inputs(quick: bool) -> dict[str, pa.Table]:
    return fixtures.financial(quick)


def configs(quick: bool) -> dict[str, dict[str, Any]]:
    return {
        # The window is pinned to the baseline's own default of 24 h: Shape's default is the whole
        # span of the transactions (SIM-9, probed below), and every other check stays comparable.
        "default": {"duration_hours": 24.0},
        "variant": {
            "duration_hours": 48.0,
            "reversal_probability": 0.10,
            "reversal_delay_hours_max": 12.0,
            "fraud_burst_probability": 0.30,
            "fraud_burst_count": 10,
            "fraud_burst_amount_range": [100.0, 2000.0],
            "settlement_batch_hours": 0.25,
            "settlement_success_rate": 0.70,
        },
    }


def controls(quick: bool) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        "reversal_probability 0.03 -> 0.08": ("default", {"reversal_probability": 0.08}),
        "settlement_success_rate 0.70 -> 0.92": ("variant", {"settlement_success_rate": 0.92}),
        "fraud_burst_probability 0.30 -> 0.90": ("variant", {"fraud_burst_probability": 0.90}),
    }


def _shape_config(cfg: dict[str, Any], seed: int) -> Any:
    from shape_simulation.financial_patterns import FinancialStreamConfig

    fixed = dict(cfg)
    if "fraud_burst_amount_range" in fixed:
        fixed["fraud_burst_amount_range"] = tuple(fixed["fraud_burst_amount_range"])
    return FinancialStreamConfig(**{**fixed, "seed": seed})


def run_shape(cfg: dict[str, Any], seed: int, inputs: Any) -> Run:
    from shape_simulation.financial_patterns import FinancialStreamSimulator

    r = FinancialStreamSimulator(tables=inputs, config=_shape_config(cfg, seed)).run()
    return Run(r.table_map(), r.stats)


def _windows(inputs: dict[str, pa.Table], cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """The settlement windows computed straight from the input: (count, total) per batch."""
    t = inputs["transaction"]
    times = h.numbers(t.column("transaction_time"), 0) * 1e6
    amount = np.asarray(t.column("amount").to_numpy(zero_copy_only=False), dtype=float)
    start = times.min()
    hours = cfg.get("duration_hours", 24.0)
    step = cfg.get("settlement_batch_hours", 4.0)
    n = max(1, int(np.ceil(hours / step)))
    lo = start + np.arange(n) * step * HOUR_US
    count = np.array([int(((times >= a) & (times < a + step * HOUR_US)).sum()) for a in lo])
    total = np.array([float(amount[(times >= a) & (times < a + step * HOUR_US)].sum()) for a in lo])
    return count, total


def _facts(run: Run, inputs: dict[str, pa.Table], cfg: dict[str, Any]) -> dict[str, Any]:
    txn = inputs["transaction"].to_pydict()
    amount_of = dict(zip(txn["transaction_id"], txn["amount"], strict=True))
    time_of = dict(
        zip(
            txn["transaction_id"],
            h.numbers(inputs["transaction"].column("transaction_time"), 0) * 1e6,
            strict=True,
        )
    )
    rev = run.tables["reversals"].to_pydict()
    fraud = run.tables["fraud_events"].to_pydict()
    sett = run.tables["settlements"].to_pydict()
    delays = (
        [
            (r - time_of[o]) / HOUR_US
            for o, r in zip(
                rev["original_transaction_id"],
                h.numbers(run.tables["reversals"].column("reversed_at"), 0) * 1e6,
                strict=True,
            )
        ]
        if rev["reversal_id"]
        else []
    )
    count, total = _windows(inputs, cfg)
    n_batches = len(sett["batch_id"])
    settled = [i for i, s in enumerate(sett["status"]) if s == "settled"]
    lo, hi = cfg.get("fraud_burst_amount_range", (500.0, 10000.0))
    by_account: dict[Any, int] = {}
    for a in fraud["account_id"]:
        by_account[a] = by_account.get(a, 0) + 1
    burst = cfg.get("fraud_burst_count", 15)
    return {
        "reversal_amounts": all(
            abs(a + abs(amount_of[o])) < 1e-9
            for a, o in zip(rev["amount"], rev["original_transaction_id"], strict=True)
        ),
        "reversal_ids_distinct": len(set(rev["original_transaction_id"]))
        == len(rev["original_transaction_id"]),
        "reversal_delay_bounds": all(
            0.1 - 1e-6 <= d <= cfg.get("reversal_delay_hours_max", 48.0) + 1e-6 for d in delays
        ),
        "fraud_amount_range": all(lo - 1e-9 <= a <= hi + 1e-9 for a in fraud["amount"]),
        "fraud_burst_multiples": all(c % burst == 0 for c in by_account.values()),
        "fraud_flag": all(fraud["is_fraud"]),
        "settlement_counts": sett["transaction_count"] == count.tolist()[:n_batches],
        "settled_totals": all(
            abs(sett["total_amount"][i] - round(float(total[i]), 2)) < 0.011 for i in settled
        ),
        "failure_reason_iff_not_settled": all(
            (r is None) == (s == "settled")
            for r, s in zip(sett["failure_reason"], sett["status"], strict=True)
        ),
        "combined_is_concat": run.tables["transactions"].num_rows
        == inputs["transaction"].num_rows + len(rev["reversal_id"]) + len(fraud["fraud_tx_id"]),
        "exercised_reversals": len(rev["reversal_id"]) > 0,
        "exercised_fraud": len(fraud["fraud_tx_id"]) > 0,
    }


def compare(
    rep: Report, shape: Run, base: dict[int, Run], cfg: dict[str, Any], inputs: Any, quick: bool
) -> None:
    accounts = frozenset(inputs["account"].column("account_id").to_pylist())
    txn_ids = frozenset(inputs["transaction"].column("transaction_id").to_pylist())
    reversal_cols = {
        "reversal_id": Col("id", regex=UUID),
        "original_transaction_id": Col("vocab", vocab=txn_ids),
        "account_id": Col("vocab", vocab=accounts),
        "reversal_reason": Col("enum", vocab=REASONS),
    }
    fraud_cols = {
        "fraud_tx_id": Col("id", regex=UUID),
        "account_id": Col("vocab", vocab=accounts),
        "merchant_category": Col("enum", vocab=MERCHANTS),
        "is_fraud": Col("const", value=True),
    }
    specs = {
        "transactions": TableSpec(
            columns={
                "transaction_id": Col("pattern", regexes=(r"txn_\d{7}",)),
                "account_id": Col("vocab", vocab=accounts),
                **{k: v for k, v in reversal_cols.items() if k not in ("account_id",)},
                "fraud_tx_id": Col("id", regex=UUID),
                "merchant_category": Col("enum", vocab=MERCHANTS),
                "is_fraud": Col("enum", vocab=frozenset({"True", "False"})),
            }
        ),
        "reversals": TableSpec(columns=reversal_cols),
        "fraud_events": TableSpec(columns=fraud_cols),
        "settlements": TableSpec(
            rows="exact",
            key=("settled_at",),
            columns={
                "batch_id": Col("id", regex=UUID),
                "transaction_count": Col("exact"),
                "status": Col("enum", vocab=frozenset({"settled", "partial", "failed"})),
                "failure_reason": Col("enum", vocab=FAILURES),
            },
        ),
    }
    for name, spec in specs.items():
        tables = {s: r.tables[name] for s, r in base.items()}
        if name == "transactions":  # SIM-7: the baseline's column set depends on chance
            tables = {s: h.conform(t, shape.tables[name]) for s, t in tables.items()}
        h.compare_table(rep, name, shape.tables[name], tables, spec)
    h.compare_stats(
        rep,
        shape.stats,
        {s: r.stats for s, r in base.items()},
        exact=("original_transaction_count", "duration_hours", "settlement_batch_count"),
        counts=("reversal_count", "fraud_event_count", "combined_transaction_count"),
        skip=("seed",),
    )
    for status in ("settled", "partial", "failed"):
        share = {
            s: r.tables["settlements"].column("status").to_pylist().count(status)
            / max(r.tables["settlements"].num_rows, 1)
            for s, r in base.items()
        }
        mine = shape.tables["settlements"].column("status").to_pylist().count(status) / max(
            shape.tables["settlements"].num_rows, 1
        )
        h.compare_scalar(rep, f"settlements:share_{status}", mine, list(share.values()), floor=0.02)
    fs = _facts(shape, inputs, cfg)
    fb = {s: _facts(r, inputs, cfg) for s, r in base.items()}
    for key, value in fs.items():
        if key.startswith("exercised"):
            continue
        h.invariant(rep, f"invariant:{key}", value, {s: f[key] for s, f in fb.items()})
        rep.add(f"holds:{key}", bool(value))
    if cfg.get("reversal_probability", 0.03) >= 0.05:
        rep.add(
            "exercised:reversals",
            fs["exercised_reversals"] and all(f["exercised_reversals"] for f in fb.values()),
        )
    if cfg.get("fraud_burst_probability", 0.01) >= 0.1:
        rep.add(
            "exercised:fraud_bursts",
            fs["exercised_fraud"] and all(f["exercised_fraud"] for f in fb.values()),
        )


def probes(ctx: h.Context) -> list[Report]:
    """SIM-6 (financial): the domain's time column is ``transaction_date``; the baseline does not
    look at it, so reversals get the wall-clock time and settlement windows are even slices."""
    data = fixtures.financial(True)
    renamed = data["transaction"].rename_columns(
        ["transaction_id", "account_id", "amount", "transaction_date"]
    )
    tables = {"transaction": renamed, "account": data["account"]}
    cfg = {"reversal_probability": 0.2, "settlement_batch_hours": 4.0, "duration_hours": 24.0}
    truth, _ = _windows(data, {"duration_hours": 24.0, "settlement_batch_hours": 4.0})
    rep = Report("SIM-6 financial domain column names")
    theirs = h.baseline_once(SIM, cfg, tables, 5, "domain-names")
    ours = run_shape(cfg, 5, tables)
    rep.add(
        "baseline: settlement counts ignore the transaction times",
        theirs.tables["settlements"].column("transaction_count").to_pylist() != truth.tolist(),
    )
    rep.add(
        "shape: settlement counts follow the transaction times",
        ours.tables["settlements"].column("transaction_count").to_pylist() == truth.tolist(),
    )
    years = {v.year for v in theirs.tables["reversals"].column("reversed_at").to_pylist()}
    rep.add(
        "baseline: reversals are stamped with the wall clock", years != {2024}, years=sorted(years)
    )
    delays = h.numbers(ours.tables["reversals"].column("reversed_at"), 0)
    rep.add(
        "shape: reversals follow their transactions",
        len(delays) > 0
        and float(delays.min()) >= float(h.numbers(renamed.column("transaction_date"), 0).min()),
    )
    return [rep, _columns_probe(), _window_probe()]


def _columns_probe() -> Report:
    """SIM-7: the baseline's combined table has the fraud columns only when a burst happened."""
    data = fixtures.financial(True)
    cfg = {"fraud_burst_probability": 0.05, "reversal_probability": 0.0}
    rep = Report("SIM-7 financial combined columns")
    theirs = h.baseline_many(SIM, cfg, data, range(1, 13), "columns")
    variants = {tuple(r.tables["transactions"].column_names) for r in theirs.values()}
    rep.add(
        "baseline: the column set varies from seed to seed",
        len(variants) > 1,
        variants=len(variants),
    )
    ours = {
        tuple(run_shape(cfg, s, data).tables["transactions"].column_names) for s in range(1, 13)
    }
    rep.add("shape: one column set for every seed", len(ours) == 1)
    return rep


def _window_probe() -> Report:
    """SIM-9: a table of months. The baseline's default window settles only the first 24 hours;
    Shape's covers the whole span of the transactions and every month has settlements."""
    n = 3_000
    rng = np.random.default_rng(21)
    start = np.datetime64("2024-01-01T00:00:00", "us").astype(np.int64)
    when = np.sort(start + (rng.random(n) * 90 * 24 * HOUR_US).astype(np.int64))
    data = fixtures.financial(True)
    transaction = pa.table(
        {
            "transaction_id": pa.array([f"txn_{i:07d}" for i in range(n)]),
            "account_id": pa.array(
                [f"acc_{i % 120:05d}" for i in range(n)],
            ),
            "amount": pa.array(np.round(rng.lognormal(3.5, 1.1, n), 2)),
            "transaction_time": pa.array(when, pa.timestamp("us")),
        }
    )
    tables = {"transaction": transaction, "account": data["account"]}
    cfg = {"settlement_success_rate": 1.0}
    rep = Report("SIM-9 financial default window")
    first, last = int(when.min()), int(when.max())

    def settled(run: Run) -> list[int]:
        return [int(v) for v in h.numbers(run.tables["settlements"].column("settled_at"), 0) * 1e6]

    def months(stamps: list[int]) -> set[Any]:
        return {np.datetime64(v, "us").astype("datetime64[M]") for v in stamps}

    theirs = h.baseline_once(SIM, cfg, tables, 5, "default-window")
    ours = run_shape(cfg, 5, tables)
    t_stamps, s_stamps = settled(theirs), settled(ours)
    rep.add(
        "baseline: settles only the first day of a three-month table",
        max(t_stamps) <= first + 24 * HOUR_US
        and sum(theirs.tables["settlements"].column("transaction_count").to_pylist()) < n // 10,
        settlements=len(t_stamps),
    )
    rep.add(
        "shape: settles the whole span",
        max(s_stamps) >= last
        and sum(ours.tables["settlements"].column("transaction_count").to_pylist()) == n,
        settlements=len(s_stamps),
    )
    rep.add(
        "shape: every month of the table has settlements",
        months(list(when.tolist())) <= months(s_stamps),
    )
    return rep
