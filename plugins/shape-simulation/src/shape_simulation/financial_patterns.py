"""Financial transaction stream patterns: reversals, fraud bursts and settlement batches.

Layers financial streaming anomalies on top of base transactions and accounts (from the
``financial`` domain, or any tables with the same columns).

Usage::

    from shape_simulation.financial_patterns import FinancialStreamConfig, FinancialStreamSimulator

    cfg = FinancialStreamConfig(seed=7)  # the window is the span of the transactions
    result = FinancialStreamSimulator(transactions, accounts, cfg).run()
    # or: FinancialStreamSimulator(tables=generated.tables, config=cfg)

``transactions`` needs ``account_id`` and ``amount`` and should have ``transaction_id`` and a
time column (``transaction_time``, or the domain's ``transaction_date``); ``accounts`` needs
``account_id``. The same configuration (seed included) gives the same tables; where the
transactions carry no time the window starts at ``start_time``.

The window defaults to the whole span of the transactions: from the first to the last
transaction time, plus one settlement batch so that the last transactions settle too. A table
that covers months therefore has settlements, fraud-burst chances and clearing for every month,
not just its first day. ``duration_hours`` overrides the window; without a time column the
default is 24 hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape_simulation._patterns import (
    TablesResult,
    as_table,
    check_settings,
    combine,
    float_array,
    float_values,
    parse_start,
    pick,
    table_mapping,
    timestamp_us,
    timestamps,
    uuid_strings,
)

_REVERSAL_REASONS: list[tuple[str, float]] = [
    ("customer_dispute", 0.40),
    ("duplicate", 0.25),
    ("fraud_confirmed", 0.20),
    ("error", 0.15),
]
_FRAUD_MERCHANT_CATEGORIES: list[str] = [
    "electronics",
    "jewelry",
    "gift_cards",
    "cryptocurrency",
    "wire_transfer",
    "online_gambling",
    "luxury_goods",
]
_SETTLEMENT_FAILURE_REASONS: list[str] = [
    "insufficient_funds",
    "account_closed",
    "compliance_hold",
    "network_timeout",
    "duplicate_batch",
]
_TIME_COLUMNS = ("transaction_time", "transaction_date")
_HOUR_US = 3_600_000_000
# The window is simulated hour by hour (a fraud-burst draw per hour, a settlement batch per
# ``settlement_batch_hours``), so it is bounded: 100 years of hours, a million batches. A
# sentinel time such as 9999-12-31 would otherwise make a run of millions of batches.
MAX_WINDOW_HOURS = 24 * 366 * 100
MAX_SETTLEMENT_BATCHES = 1_000_000


@dataclass
class FinancialStreamConfig:
    """Configuration for :class:`FinancialStreamSimulator`.

    Args:
        duration_hours: Total simulation window in hours. ``None`` (the default) is the full
            span of the transactions plus one settlement batch (24 hours when they carry no
            time).
        start_time: Where the window starts when the transactions have no time column
            (ISO-8601; a missing zone means UTC).
        reversal_enabled: Whether to generate transaction reversals.
        reversal_probability: Fraction of transactions that get reversed.
        reversal_delay_hours_max: Maximum delay between a transaction and its reversal.
        fraud_burst_enabled: Whether to generate fraud burst events.
        fraud_burst_probability: Per-hour probability of a fraud burst occurring.
        fraud_burst_count: Number of transactions per fraud burst.
        fraud_burst_amount_range: ``(min, max)`` amount of a fraud transaction.
        settlement_enabled: Whether to generate settlement batch records.
        settlement_batch_hours: Settlement runs every N hours.
        settlement_success_rate: Fraction of settlements that succeed.
        seed: Random seed for reproducibility.
    """

    duration_hours: float | None = None
    start_time: str = "2024-01-01T00:00:00"
    reversal_enabled: bool = True
    reversal_probability: float = 0.03
    reversal_delay_hours_max: float = 48.0
    fraud_burst_enabled: bool = True
    fraud_burst_probability: float = 0.01
    fraud_burst_count: int = 15
    fraud_burst_amount_range: tuple[float, float] = (500.0, 10000.0)
    settlement_enabled: bool = True
    settlement_batch_hours: float = 4.0
    settlement_success_rate: float = 0.98
    seed: int = 42

    def __post_init__(self) -> None:
        check_settings(
            self,
            positive=("settlement_batch_hours",),
            non_negative=("duration_hours", "reversal_delay_hours_max", "fraud_burst_count"),
            probabilities=(
                "reversal_probability",
                "fraud_burst_probability",
                "settlement_success_rate",
            ),
        )


@dataclass
class FinancialStreamResult(TablesResult):
    """Result of a :meth:`FinancialStreamSimulator.run` execution.

    Attributes:
        transactions: The original transactions followed by the reversals and fraud events
            (the columns of all three, whether or not a run produced any; cells a source lacks
            are null; a disabled kind adds no columns).
        reversals: Only the reversal records.
        fraud_events: Only the fraud burst records.
        settlements: The settlement batch results.
        stats: Summary statistics for the run.
    """

    TABLES: ClassVar[tuple[str, ...]] = ("transactions", "reversals", "fraud_events", "settlements")

    transactions: pa.Table
    reversals: pa.Table
    fraud_events: pa.Table
    settlements: pa.Table
    stats: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"FinancialStreamResult(transactions={self.transactions.num_rows}, "
            f"reversals={self.reversals.num_rows}, fraud_events={self.fraud_events.num_rows}, "
            f"settlements={self.settlements.num_rows})"
        )


class FinancialStreamSimulator:
    """Generate financial transaction stream anomalies on top of transactions and accounts."""

    def __init__(
        self,
        transactions: Any = None,
        accounts: Any = None,
        config: FinancialStreamConfig | None = None,
        *,
        tables: Any = None,
    ) -> None:
        if tables is not None:
            mapping = table_mapping(tables)
            transactions, accounts = mapping.get("transaction"), mapping.get("account")
        if transactions is None or accounts is None:
            raise ValueError("Provide either tables= or both transactions and accounts")
        self._transactions = as_table(transactions)
        self._accounts = as_table(accounts)
        for table, column, label in (
            (self._transactions, "account_id", "transactions"),
            (self._transactions, "amount", "transactions"),
            (self._accounts, "account_id", "accounts"),
        ):
            if column not in table.column_names:
                raise ValueError(f"{label} need a {column!r} column")
        self._config = config or FinancialStreamConfig()
        self._rng = np.random.default_rng(self._config.seed)
        self._time_col = next(
            (c for c in _TIME_COLUMNS if c in self._transactions.column_names), None
        )
        self._time_us = np.empty(0, dtype=np.int64)
        self._time_valid = np.empty(0, dtype=bool)
        self._tz: str | None = None
        if self._time_col is not None:
            self._time_us, self._time_valid, self._tz = timestamp_us(
                self._transactions.column(self._time_col)
            )
        elif self._transactions.num_rows:
            self._time_valid = np.zeros(self._transactions.num_rows, dtype=bool)
        self._amount, _ = float_values(self._transactions.column("amount"))
        self._start_us = (
            int(self._time_us[self._time_valid].min())
            if self._time_valid.any()
            else parse_start(self._config.start_time)
        )
        self._window_hours = self._window()
        self._check_window()

    def _window(self) -> float:
        """The simulated window in hours: ``duration_hours``, else the span of the transactions
        plus the settlement lag (a transaction settles when its batch ends, so one batch), else
        24 hours for transactions without a time."""
        cfg = self._config
        if cfg.duration_hours is not None:
            return float(cfg.duration_hours)
        if not self._time_valid.any():
            return 24.0
        times = self._time_us[self._time_valid]
        span_hours = float(times.max() - times.min()) / _HOUR_US
        return span_hours + float(cfg.settlement_batch_hours)

    def _check_window(self) -> None:
        cfg = self._config
        hours = self._window_hours
        if not hours <= MAX_WINDOW_HOURS:  # also refuses NaN
            if cfg.duration_hours is not None:
                where = f"duration_hours is {cfg.duration_hours}"
            else:
                times = self._time_us[self._time_valid]
                first, last = (
                    np.datetime64(int(times.min()), "us"),
                    np.datetime64(int(times.max()), "us"),
                )
                where = (
                    f"the transactions run from {first} to {last}; a far-future time such as "
                    "9999-12-31 is usually a placeholder: drop those rows or set duration_hours"
                )
            raise ValueError(
                f"the simulated window is {hours:.0f} hours, more than the "
                f"{MAX_WINDOW_HOURS} (100 years) a run covers: {where}"
            )
        if cfg.settlement_enabled and cfg.settlement_batch_hours > 0:
            batches = hours / cfg.settlement_batch_hours
            if batches > MAX_SETTLEMENT_BATCHES:
                raise ValueError(
                    f"settlement_batch_hours {cfg.settlement_batch_hours} over a {hours:.0f}-hour "
                    f"window makes {batches:.0f} settlement batches, more than "
                    f"{MAX_SETTLEMENT_BATCHES}: raise settlement_batch_hours or shorten "
                    "duration_hours"
                )

    # ---- public -----------------------------------------------------------------------------

    def run(self) -> FinancialStreamResult:
        """Execute the simulation and return a :class:`FinancialStreamResult`."""
        cfg = self._config
        reversals = self._reversals() if cfg.reversal_enabled else self._empty_reversals()
        fraud = self._fraud_bursts() if cfg.fraud_burst_enabled else self._empty_fraud()
        settlements = self._settlements() if cfg.settlement_enabled else self._empty_settlements()
        # The columns follow the configuration, not the run: a stream with no reversal or fraud
        # burst by chance keeps the columns of those it could have had.
        parts = [self._transactions]
        if cfg.reversal_enabled:
            parts.append(reversals)
        if cfg.fraud_burst_enabled:
            parts.append(fraud)
        combined = combine(parts) if len(parts) > 1 else self._transactions
        stats = {
            "original_transaction_count": self._transactions.num_rows,
            "reversal_count": reversals.num_rows,
            "fraud_event_count": fraud.num_rows,
            "settlement_batch_count": settlements.num_rows,
            "combined_transaction_count": combined.num_rows,
            "duration_hours": self._window_hours,
            "seed": cfg.seed,
        }
        return FinancialStreamResult(combined, reversals, fraud, settlements, stats=stats)

    # ---- reversals --------------------------------------------------------------------------

    def _account_type(self) -> pa.DataType:
        account_type: pa.DataType = self._accounts.schema.field("account_id").type
        return account_type

    def _empty_reversals(self) -> pa.Table:
        id_type = (
            self._transactions.schema.field("transaction_id").type
            if "transaction_id" in self._transactions.column_names
            else pa.string()
        )
        return pa.schema(
            [
                ("reversal_id", pa.string()),
                ("original_transaction_id", id_type),
                ("account_id", self._transactions.schema.field("account_id").type),
                ("amount", pa.float64()),
                ("reversal_reason", pa.string()),
                ("reversed_at", pa.timestamp("us", self._tz)),
            ]
        ).empty_table()

    def _reversals(self) -> pa.Table:
        """Pick transactions to reverse; each reversal mirrors its original with a negative
        amount and points back at it through ``original_transaction_id``."""
        cfg, rng = self._config, self._rng
        n = self._transactions.num_rows
        if n == 0:
            return self._empty_reversals()
        chosen = np.flatnonzero(rng.random(n) < cfg.reversal_probability)
        if len(chosen) == 0:
            return self._empty_reversals()
        delay_us = np.round(rng.uniform(0.1, cfg.reversal_delay_hours_max, len(chosen)) * _HOUR_US)
        reasons = pick(
            rng, [r for r, _ in _REVERSAL_REASONS], len(chosen), [w for _, w in _REVERSAL_REASONS]
        )
        if self._time_col is not None:
            at = self._time_us[chosen] + delay_us.astype(np.int64)
            valid = self._time_valid[chosen]
        else:
            at = self._start_us + delay_us.astype(np.int64)
            valid = np.ones(len(chosen), dtype=bool)
        idx = pa.array(chosen, pa.int64())
        if "transaction_id" in self._transactions.column_names:
            original = self._transactions.column("transaction_id").take(idx).combine_chunks()
        else:
            original = pa.array(uuid_strings(rng, len(chosen)), pa.string())
        table = pa.table(
            {
                "reversal_id": pa.array(uuid_strings(rng, len(chosen)), pa.string()),
                "original_transaction_id": original,
                "account_id": self._transactions.column("account_id").take(idx).combine_chunks(),
                "amount": float_array(-np.abs(self._amount[chosen])),
                "reversal_reason": pa.array(reasons, pa.string()),
                "reversed_at": timestamps(at, self._tz, valid),
            },
        )
        return table

    # ---- fraud bursts -----------------------------------------------------------------------

    def _empty_fraud(self) -> pa.Table:
        return pa.schema(
            [
                ("fraud_tx_id", pa.string()),
                ("account_id", self._account_type()),
                ("amount", pa.float64()),
                ("merchant_category", pa.string()),
                ("transaction_time", pa.timestamp("us", self._tz)),
                ("is_fraud", pa.bool_()),
            ]
        ).empty_table()

    def _fraud_bursts(self) -> pa.Table:
        """Rapid-fire fraud transactions from compromised accounts: each hour of the window has
        an independent chance of a burst, and a burst sends ``fraud_burst_count`` transactions
        from one randomly chosen account within two minutes."""
        cfg, rng = self._config, self._rng
        accounts = self._accounts.column("account_id")
        if self._accounts.num_rows == 0:
            return self._empty_fraud()
        n_hours = int(np.ceil(self._window_hours))
        hours = np.flatnonzero(rng.random(n_hours) < cfg.fraud_burst_probability)
        if len(hours) == 0 or cfg.fraud_burst_count <= 0:
            return self._empty_fraud()
        count, lo, hi = cfg.fraud_burst_count, *cfg.fraud_burst_amount_range
        who = rng.integers(0, self._accounts.num_rows, size=len(hours))
        offset = rng.uniform(0, 120, size=(len(hours), count))
        amount = rng.uniform(lo, hi, size=(len(hours), count))
        category = pick(rng, _FRAUD_MERCHANT_CATEGORIES, len(hours) * count)
        at = (
            self._start_us
            + hours[:, None] * _HOUR_US
            + np.round(offset * 1_000_000).astype(np.int64)
        ).ravel()
        owner = accounts.take(pa.array(np.repeat(who, count), pa.int64())).combine_chunks()
        return pa.table(
            {
                "fraud_tx_id": pa.array(uuid_strings(rng, len(at)), pa.string()),
                "account_id": owner,
                "amount": float_array(np.round(amount.ravel(), 2)),
                "merchant_category": pa.array(category, pa.string()),
                "transaction_time": timestamps(at, self._tz),
                "is_fraud": pa.array(np.ones(len(at), dtype=bool)),
            }
        )

    # ---- settlements ------------------------------------------------------------------------

    def _empty_settlements(self) -> pa.Table:
        return pa.schema(
            [
                ("batch_id", pa.string()),
                ("settled_at", pa.timestamp("us", self._tz)),
                ("transaction_count", pa.int64()),
                ("total_amount", pa.float64()),
                ("status", pa.string()),
                ("failure_reason", pa.string()),
            ]
        ).empty_table()

    def _settlements(self) -> pa.Table:
        """Periodic settlement batches: how many transactions each window settled, their total,
        and whether the batch settled, settled partially or failed."""
        cfg, rng = self._config, self._rng
        n_batches = max(1, int(np.ceil(self._window_hours / cfg.settlement_batch_hours)))
        batch_us = int(round(cfg.settlement_batch_hours * _HOUR_US))
        n = self._transactions.num_rows
        amount = np.nan_to_num(self._amount, nan=0.0)
        if self._time_valid.any():
            times = self._time_us[self._time_valid]
            order = np.argsort(times, kind="stable")
            times = times[order]
            running = np.concatenate([[0.0], np.cumsum(amount[self._time_valid][order])])
            lo = self._start_us + np.arange(n_batches, dtype=np.int64) * batch_us
            left = np.searchsorted(times, lo, side="left")
            right = np.searchsorted(times, lo + batch_us, side="left")
            count = right - left
            total = running[right] - running[left]
        else:
            # near-equal consecutive batches that together hold every transaction
            bounds = (np.arange(n_batches + 1, dtype=np.int64) * n) // n_batches
            start, end = bounds[:-1], bounds[1:]
            running = np.concatenate([[0.0], np.cumsum(amount)])
            count = end - start
            total = running[end] - running[start]
        batch_end = self._start_us + (np.arange(n_batches, dtype=np.int64) + 1) * batch_us

        rate = cfg.settlement_success_rate
        roll = rng.random(n_batches)
        settled = roll < rate
        partial = ~settled & (roll < rate + (1 - rate) * 0.5)
        failed = ~settled & ~partial
        reason = pick(rng, _SETTLEMENT_FAILURE_REASONS, n_batches)
        share = rng.uniform(0.3, 0.9, n_batches)
        total = np.where(partial, np.round(total * share, 2), np.where(failed, 0.0, total))
        status = np.where(settled, "settled", np.where(partial, "partial", "failed"))
        return pa.table(
            {
                "batch_id": pa.array(uuid_strings(rng, n_batches), pa.string()),
                "settled_at": timestamps(batch_end, self._tz),
                "transaction_count": pa.array(count.astype(np.int64), pa.int64()),
                "total_amount": float_array(np.round(total, 2)),
                "status": pa.array(status, pa.string()),
                "failure_reason": pa.array(reason, pa.string(), mask=settled),
            }
        )
