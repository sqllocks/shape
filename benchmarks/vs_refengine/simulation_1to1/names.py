"""The mapping from the baseline's names to Shape's, and the named allow-list of intentional
differences (D-13; the owner's standing decision of 2026-10-01).

Standard library only. Nothing in the package or its documentation uses these baseline names;
they exist so the harness can run the baseline and read its output.

Both lanes of P6-04 (``case_*.py`` modules) use ``MODULES``; the pattern simulators (P6-04b) are
the first five entries.
"""

from __future__ import annotations

# The pattern cases of P6-04b (``case_<name>.py``, run by ``verify_patterns.py``); the other
# lane's cases (file_drop, ...) use ``verify.py``.
PATTERN_CASES = ("clickstream", "financial", "iot", "operational_log", "pulse")

# baseline module (under sqllocks_refengine.simulation) -> Shape module (under shape_simulation).
# The module names are the same; the baseline's class names are kept by Shape too, so the
# mapping is the identity for classes. What does change is listed in PARAMETERS and FIELDS.
MODULES = {
    "clickstream_patterns": "clickstream_patterns",
    "financial_patterns": "financial_patterns",
    "iot_patterns": "iot_patterns",
    "operational_log_patterns": "operational_log_patterns",
    "pulse_patterns": "pulse_patterns",
}

# constructor parameters: baseline name -> Shape name (pandas frames became Arrow tables).
PARAMETERS = {
    "FinancialStreamSimulator": {"transactions_df": "transactions", "accounts_df": "accounts"},
    "IoTTelemetrySimulator": {"readings_df": "readings", "devices_df": "devices"},
}

# Result containers: baseline fields are pandas frames, Shape's are Arrow tables with the same
# names. Output column names are identical (none of them names the baseline), so there is no
# per-column mapping.
FIELDS: dict[str, dict[str, str]] = {}

# Types the two sides legitimately differ in, as (table, column): the accepted pair.
# The baseline's counts of a merged mart are float64 or int64 depending on whether any group
# lacked a completed trip (a pandas merge artefact); Shape's are always int64.
TYPE_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    ("fact_revenue_daily", c): ("double", "int64")
    for c in ("completed_trips", "unique_riders", "unique_drivers", "trips", "cancelled_trips")
}

# The allow-list: baseline behaviour that harms the user's trust, which Shape fixes. Each entry
# has a probe in its case module that shows the baseline exhibits the defect and Shape does not;
# every other difference fails the verifier.
ALLOWED = {
    "SIM-1": "A seed does not reproduce a run. The baseline draws ids from uuid4 (not seeded) and "
    "starts the clickstream window at the moment of the run; Shape draws ids from the seeded "
    "generator and starts at `start_time`.",
    "SIM-2": "Operational logs: `latency_spike_enabled` and `outage_enabled` are ignored by the "
    "baseline (spikes and outages occur with the flag off); Shape honours them.",
    "SIM-3": "Operational logs: the baseline's `trace_id` and `span_id` on a log event never match "
    "any row of the traces table; Shape's sampled events carry their trace's ids.",
    "SIM-4": "Operational logs: a duration under one hour (or with a fractional hour) generates "
    "no events (or drops the fraction) in the baseline; Shape honours the window.",
    "SIM-5": "IoT: with `alert_storm_enabled=False` the baseline generates no alerts at all, not "
    "even the baseline-rate ones; Shape generates them whether or not storms are on.",
    "SIM-8": "Operational logs: with `trace_enabled=False` the baseline's error-burst events still "
    "carry trace and span ids; Shape's carry none, as every other event of such a run.",
    "SIM-7": "Financial: the columns of the combined transactions table depend on chance in the "
    "baseline (a run with no fraud burst has no fraud columns, one with none of reversals has no "
    "reversal columns); Shape's follow the configuration, so a stream's schema does not change "
    "between runs. The harness conforms the baseline's table to Shape's column set (columns the "
    "baseline run lacked are null) before comparing.",
    "SIM-9": "Financial: the default window is 24 hours from the first transaction in the "
    "baseline, so a table that covers months settles only its first day; Shape's default is the "
    "whole span of the transactions (first to last, plus one settlement batch so the last "
    "transactions settle), so settlements, fraud-burst chances and clearing cover the whole "
    "period. `duration_hours` overrides it in both; the parity cases pin it to 24 hours to "
    "compare everything else.",
    "SIM-6": "IoT and financial inputs from the shipped domains: the baseline does not know the "
    "domains' column names (`reading_timestamp`, `transaction_date`) or that readings are per "
    "sensor, so fleet status and settlements ignore time and per-device readings; Shape does.",
}
