# Simulation patterns (`shape-simulation`)

`sqllocks-shape-simulation` also holds the file-drop, stream and workflow simulators, described in
[SIMULATION_FILES_EVENTS.md](../SIMULATION_FILES_EVENTS.md). This page covers the pattern
simulators: it generates the kinds of data a plain table generator does not: web
sessions, financial anomalies, IoT telemetry, service logs and traces, and a rideshare's live
telemetry and finance marts. Each simulator takes a configuration (and, where it layers
anomalies on existing data, Arrow tables) and returns Arrow tables and summary statistics.

```bash
pip install 'sqllocks-shape[simulation]'
```

Nothing is imported until a simulator runs. `shape plugins list` shows
`shape.commands:simulate`; the command has one sub-command per simulator.

## The simulators

| Module | What it generates | Needs |
|---|---|---|
| `shape_simulation.clickstream_patterns` | sessions, page views, conversion funnels, bot traffic | nothing |
| `shape_simulation.financial_patterns` | reversals, fraud bursts, settlement batches | `transaction` and `account` tables |
| `shape_simulation.iot_patterns` | sensor drift, missing readings, alert storms, battery drain, fleet status | `reading` and `device` tables (and `sensor`, for readings per sensor) |
| `shape_simulation.operational_log_patterns` | service logs, distributed traces, latency spikes, outages, error bursts, service health | nothing |
| `shape_simulation.pulse_patterns` | enriched trips, trip events, surge signals, driver pings, revenue and earnings marts | `trip` table of the `pulse` domain |

Every simulator is deterministic: **the same configuration and seed give the same tables**,
ids included. Inputs may be Arrow tables, record batches, mappings of columns or pandas frames,
and are never modified. Results are dataclasses of Arrow tables with a `stats` dictionary.

```python
from shape.api import generate
from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator
from shape_simulation.financial_patterns import FinancialStreamConfig, FinancialStreamSimulator

web = ClickstreamSimulator(ClickstreamConfig(users=1000, duration_hours=24, seed=7)).run()
web.sessions, web.page_views, web.funnels        # pyarrow.Table
web.stats["funnel_conversion_rate"]

base = generate("financial", scale="small", seed=7)
money = FinancialStreamSimulator(tables=base.tables, config=FinancialStreamConfig(seed=7)).run()
money.transactions, money.reversals, money.fraud_events, money.settlements
```

Every result has `table_map()` (the tables by name), `events(table)` (the table's rows as flat
stream events, below) and `write(directory, fmt)` (`parquet`, `csv` or `jsonl`, plus `stats.json`).

### Clickstream

`ClickstreamConfig`: `users`, `duration_hours`, `start_time` (ISO-8601, default
`2024-01-01T00:00:00`; the window never starts at the clock), `avg_sessions_per_user`,
`avg_pages_per_session`, `bounce_rate`, the funnel (`funnel_enabled`, `funnel_stages`,
`funnel_drop_rate`), bots (`bot_traffic_enabled`, `bot_fraction`, `bot_pages_per_session`),
`page_pool` (`{id}` and `{slug}` expand), `referrer_sources`, `device_types`, `seed`.
Sessions start with a daytime weighting (peak at noon, never below 10% of it). Tables:
`sessions`, `page_views` (each session's pages in order, dwell times log-normal for people and
short for bots), `funnels` (only for people who did not bounce; each stage can end the visit).

### Financial

`FinancialStreamConfig`: `duration_hours`, `start_time` (used when the transactions have no
time), reversals (`reversal_enabled`, `reversal_probability`, `reversal_delay_hours_max`), fraud
bursts (`fraud_burst_enabled`, `fraud_burst_probability` per hour, `fraud_burst_count`,
`fraud_burst_amount_range`), settlements (`settlement_enabled`, `settlement_batch_hours`,
`settlement_success_rate`), `seed`. The time column is `transaction_time` or the domain's
`transaction_date`. `transactions` is the original rows followed by the reversals and the fraud
events; **its columns follow the configuration, not the run** (a run with no fraud burst still
has the fraud columns, null), so a stream's schema does not change between runs.
**The default window is the whole period.** `duration_hours` is unset by default, and the window
is then the span of the transactions (first to last transaction time) plus one settlement batch,
the lag a transaction waits to settle, so the last transactions settle too. A table that covers
months gets settlements, fraud-burst chances and clearing for every month; `stats["duration_hours"]`
reports the window used. Set `duration_hours` to override it (the window starts at the earliest
transaction). A window over 100 years (usually a placeholder time such as 9999-12-31), or more
than a million settlement batches, is refused with an error that names the setting to change. Without a time column the default is 24 hours. The lag itself is unchanged: a
transaction settles when its batch ends, and the share of settled, partial and failed batches
follows `settlement_success_rate`. `settlements` count and total the transactions in each window
exactly; a partial or failed batch carries a failure reason.

### IoT

`IoTTelemetryConfig`: `duration_hours`, drift (`drift_enabled`, `drift_probability` of the
sensors, `drift_rate`), missing readings (`missing_enabled`, `missing_probability`; missing
values are nulls), alert storms (`alert_storm_enabled`, `alert_storm_probability` per hour,
`alert_storm_duration_minutes`, `alert_storm_rate_multiplier`, `reading_interval_seconds`),
battery drain (`battery_drain_enabled`, `battery_drain_rate` per hour), `seed`. Alerts at the
baseline rate (about one per device per eight hours) happen whether or not storms are on.
Readings may carry `device_id` or `sensor_id`; for the latter pass the `sensors` table
(`IoTTelemetrySimulator(readings, devices, config, sensors=sensors)`, or `tables=` with a
`sensor` table) so each reading counts for its device. Value columns: `value`,
`reading_value`, `sensor_value`, `measurement`; time columns: `reading_time`,
`reading_timestamp`, `created_at`, `timestamp`, `read_at`.
Tables: `readings`, `alerts`, `fleet_status` (status offline, degraded or online).

### Operational logs

`OperationalLogConfig`: `service_count` or `services`, `duration_hours` (fractions of an hour
count), `start_time`, `events_per_hour`, latency (`latency_mean_ms`, `latency_std_ms`), spikes
(`latency_spike_enabled`, `_probability`, `_multiplier`, `_duration_minutes`), outages
(`outage_enabled`, `outage_probability`, `outage_duration_minutes`, `outage_error_rate`),
traces (`trace_enabled`, `trace_depth_mean`), error bursts (`error_burst_enabled`,
`_probability`, `_count`), `seed`. A spike or outage window covers whole hours. About 30% of
events start a distributed trace; such an event carries the trace's `trace_id` and the id of its
entry span, so `logs` joins to `traces`; a run with `trace_enabled=False` has no trace ids at all,
error bursts included. Tables: `logs`, `traces`, `service_health`.

### Pulse

`PulseDemandConfig`: `surge_events_per_week`, `surge_multiplier_range`,
`surge_duration_minutes`, `surge_recent_days`, `surge_bucket_minutes`, `eta_noise_minutes`,
`gps_jitter_meters`, `live_window_minutes`, `ping_interval_seconds`, `max_live_trips`, `seed`.
`PulseDemandSimulator(tables, config).run()` returns `result.tables`: `trip` (the input plus
geography, lifecycle timestamps and ETAs), `trip_events`, `surge_signals`, `driver_pings`,
`fact_revenue_daily`, `fact_driver_earnings`. The marts are exact functions of the trips.

## The command

```bash
shape simulate clickstream --set users=500 --set duration_hours=12 -o out/
shape simulate iot --domain iot --scale small --seed 7 -o out/ --format csv
shape simulate financial --domain my-schema.json --events settlements
```

`PATTERN` is `clickstream`, `financial`, `iot`, `operational-log` or `pulse`. `--set KEY=VALUE`
sets a field of the configuration (values are Python literals); `--seed` sets the seed of the
simulation and of the base tables. `financial`, `iot` and `pulse` first generate their base
tables with the engine from `--domain` (an installed domain, or a generation schema file; the
pattern's own domain by default) at `--scale`. `-o DIR` writes `<table>.<format>` and
`stats.json`; `--events TABLE` prints that table's rows as JSON-lines events on standard output;
`--json` prints a summary. Exit codes: `0` done, `2` bad input. A setting a simulator cannot use (text for a number, a probability
outside [0, 1], a zero interval it divides by, an empty page or device list, a `service_count` other
than 1 to 8) is refused when the configuration is made, with a message that names it.

## Streams and chaos

`result.events("page_views")` is a record batch of flat events: the row's columns plus
`_shape_table`, `_shape_seq` and, when the table has a date or timestamp column,
`_shape_event_time`; `(_shape_table, _shape_seq)` is the idempotency key. Send it through any
`shape.streaming.emit` sink or emitter (`FileSink`, `MemorySink`, `EmitterSink`), and corrupt it
with `shape.chaos.inject_anomalies` (protect the three `_shape_*` fields so the key survives).
