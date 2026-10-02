"""The mapping from the baseline's names to Shape's, and the named allow-list of intentional
differences (D-13; the owner's standing decision of 2026-10-01).

Standard library only. Nothing in the package or its documentation uses these baseline names;
they exist so the harness can run the baseline and read its output.

Both lanes of P6-04 (``case_*.py`` modules) use ``MODULES``; the pattern simulators (P6-04b) are
the first five entries.
"""

from __future__ import annotations

# baseline module (under sqllocks_spindle.simulation) -> Shape module (under shape_simulation).
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
    "SIM-6": "IoT and financial inputs from the shipped domains: the baseline does not know the "
    "domains' column names (`reading_timestamp`, `transaction_date`) or that readings are per "
    "sensor, so fleet status and settlements ignore time and per-device readings; Shape does.",
}
