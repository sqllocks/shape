"""Pulse rideshare simulation layer: telemetry and marts derived from the ``pulse`` domain's trips.

Takes the base ``pulse`` tables (``trip`` is the one that is read; ``rider``, ``driver`` and
``vehicle`` are accepted and left alone) and produces the store-shaped outputs of a four-store
demo:

* live telemetry (an eventhouse): ``driver_pings``, ``trip_events``, ``surge_signals``;
* finance marts (a warehouse): ``fact_revenue_daily``, ``fact_driver_earnings``.

It also enriches ``trip`` with geography (pickup and dropoff coordinates around the city
centroids) and lifecycle timestamps (accepted, started, completed, wait time, promised and actual
ETA), so a transactional store and a lakehouse can be loaded from the same tables.

Usage::

    from shape.api import generate
    from shape_simulation.pulse_patterns import PulseDemandConfig, PulseDemandSimulator

    base = generate("pulse", scale="small", seed=7)
    out = PulseDemandSimulator(tables=base.tables, config=PulseDemandConfig()).run()
    out.tables["trip"], out.tables["driver_pings"], out.stats

The same configuration (seed included) gives the same tables.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape_simulation._patterns import (
    TablesResult,
    as_table,
    float_array,
    float_values,
    pick,
    table_mapping,
    timestamp_us,
    timestamps,
)

# city_id -> (name, state, latitude, longitude): the four metros of the pulse domain.
CITY_CENTROIDS: dict[int, tuple[str, str, float, float]] = {
    1: ("Seattle", "WA", 47.6062, -122.3321),
    2: ("Austin", "TX", 30.2672, -97.7431),
    3: ("Atlanta", "GA", 33.7490, -84.3880),
    4: ("Chicago", "IL", 41.8781, -87.6298),
}
_METRO_RADIUS_DEG = 0.14  # metro-sized scatter around a centroid
_PLATFORM_FEE = 0.25  # the platform's take rate on driver earnings
_GPS_JITTER_DEG = 1.0 / 111_000.0  # one metre, in degrees of latitude
_NUMERIC_TRIP_COLUMNS = ("distance_mi", "surge_mult", "fare", "tip", "rating_given", "duration_min")
_CANCEL_REASONS = ["rider_no_show", "driver_cancel", "long_wait", "changed_mind"]
_SURGE_TRIGGERS = ["weather", "event", "imbalance"]
_ZONES = ["downtown", "airport", "north", "south", "east", "west"]
_MIN_US = 60_000_000
_SEC_US = 1_000_000


@dataclass
class PulseDemandConfig:
    """Variability dials for the pulse simulator.

    Args:
        seed: Random seed for reproducibility.
        surge_events_per_week: Mean surge events per city per week.
        surge_multiplier_range: Peak multiplier of a surge event.
        surge_duration_minutes: Range of a surge event's length in minutes.
        surge_recent_days: Days (back from the latest trip) that ``surge_signals`` covers.
        surge_bucket_minutes: Width of a surge signal bucket in minutes.
        eta_noise_minutes: Standard deviation of the actual ETA around the trip duration.
        gps_jitter_meters: GPS noise of a driver ping, in metres.
        live_window_minutes: Pings are emitted for trips in the most recent window.
        ping_interval_seconds: Seconds between a driver's pings.
        max_live_trips: Cap on the trips that get pings (bounds the telemetry volume).
    """

    seed: int = 42
    surge_events_per_week: float = 3.0
    surge_multiplier_range: tuple[float, float] = (1.3, 3.5)
    surge_duration_minutes: tuple[int, int] = (30, 120)
    surge_recent_days: int = 7
    surge_bucket_minutes: int = 10
    eta_noise_minutes: float = 2.5
    gps_jitter_meters: float = 15.0
    live_window_minutes: int = 120
    ping_interval_seconds: int = 20
    max_live_trips: int = 400


@dataclass
class PulseSimResult(TablesResult):
    """The simulator's output: ``tables`` maps ``trip`` (enriched), ``trip_events``,
    ``surge_signals``, ``driver_pings``, ``fact_revenue_daily`` and ``fact_driver_earnings``
    to Arrow tables; ``stats`` has their row counts and ``live_now``."""

    tables: dict[str, pa.Table] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def table_map(self) -> dict[str, pa.Table]:
        return self.tables

    def __repr__(self) -> str:
        return (
            "PulseSimResult(" + ", ".join(f"{k}={v.num_rows}" for k, v in self.tables.items()) + ")"
        )


def _exact_sums(column: pa.ChunkedArray) -> np.ndarray:
    """The sum of each group's values (a column of lists), correctly rounded and ignoring
    nulls, so that a rounded total never depends on the order the values were added in."""
    return np.array(
        [math.fsum(v for v in values if v is not None) for values in column.to_pylist()]
    )


def _exact_means(column: pa.ChunkedArray) -> np.ndarray:
    """The mean of each group's values (a column of lists); NaN where a group has none."""
    out = []
    for values in column.to_pylist():
        kept = [v for v in values if v is not None]
        out.append(math.fsum(kept) / len(kept) if kept else math.nan)
    return np.array(out)


class PulseDemandSimulator:
    """Derive telemetry and marts from pulse trips and inject variability."""

    def __init__(self, tables: Any, config: PulseDemandConfig | None = None) -> None:
        self._cfg = config or PulseDemandConfig()
        self._rng = np.random.default_rng(self._cfg.seed)
        mapping = table_mapping(tables)
        if "trip" not in mapping:
            raise ValueError("tables need a 'trip' table")
        trip = as_table(mapping["trip"])
        missing = [
            c
            for c in ("trip_id", "city_id", "requested_at", "status", "duration_min")
            if c not in trip.column_names
        ]
        if missing:
            raise ValueError(f"trip needs the columns {', '.join(missing)}")
        self._trip = trip
        self._req_us, self._req_ok, self._tz = timestamp_us(trip.column("requested_at"))
        if not self._req_ok.any():
            raise ValueError("trip has no requested_at values")

    # ---- run --------------------------------------------------------------------------------

    def run(self) -> PulseSimResult:
        trip = self._enrich_trips()
        events = self._trip_events(trip)
        surge = self._surge_signals()
        pings = self._driver_pings(trip)
        revenue = self._revenue_daily(trip)
        earnings = self._driver_earnings(trip)
        tables = {
            "trip": trip,
            "trip_events": events,
            "surge_signals": surge,
            "driver_pings": pings,
            "fact_revenue_daily": revenue,
            "fact_driver_earnings": earnings,
        }
        stats: dict[str, Any] = {k: v.num_rows for k, v in tables.items()}
        stats["live_now"] = str(self._now_ts())
        return PulseSimResult(tables=tables, stats=stats)

    # ---- helpers ----------------------------------------------------------------------------

    def _now_us(self) -> int:
        return int(self._req_us[self._req_ok].max())

    def _now_ts(self) -> Any:
        import datetime as dt

        moment = dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=self._now_us())
        if self._tz:
            moment = moment.replace(tzinfo=dt.UTC)
        return moment

    def _scatter(self, city: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Latitude and longitude around each trip's city centroid (Seattle for an unknown
        city), rounded to six decimals."""
        rng = self._rng
        n = len(city)
        lat = np.empty(n)
        lon = np.empty(n)
        known = np.zeros(n, dtype=bool)
        for cid, (_, _, clat, clon) in CITY_CENTROIDS.items():
            mask = city == cid
            known |= mask
            k = int(mask.sum())
            if k:
                lat[mask] = clat + rng.normal(0, _METRO_RADIUS_DEG, k)
                lon[mask] = clon + rng.normal(0, _METRO_RADIUS_DEG, k)
        other = ~known
        if other.any():
            k = int(other.sum())
            lat[other] = CITY_CENTROIDS[1][2] + rng.normal(0, _METRO_RADIUS_DEG, k)
            lon[other] = CITY_CENTROIDS[1][3] + rng.normal(0, _METRO_RADIUS_DEG, k)
        return np.round(lat, 6), np.round(lon, 6)

    def _numeric(self, name: str) -> np.ndarray:
        """A trip column as float64 (nulls and values that are not numbers are NaN)."""
        if name not in self._trip.column_names:
            return np.full(self._trip.num_rows, np.nan)
        try:
            return float_values(self._trip.column(name))[0]
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
            return np.full(self._trip.num_rows, np.nan)

    # ---- trips ------------------------------------------------------------------------------

    def _enrich_trips(self) -> pa.Table:
        rng, cfg = self._rng, self._cfg
        t = self._trip
        n = t.num_rows
        num = {c: self._numeric(c) for c in _NUMERIC_TRIP_COLUMNS}
        city = self._numeric("city_id")
        dur = num["duration_min"]
        req = self._req_us

        plat, plon = self._scatter(city)
        bearing = rng.uniform(0, 2 * np.pi, n)
        span = (dur / 60.0) * 0.02
        accept_s = rng.uniform(5, 120, n)
        wait_min = np.round(rng.uniform(1, 8, n), 2)
        accepted = req + np.round(accept_s * _SEC_US).astype(np.int64)
        started = accepted + np.round(wait_min * _MIN_US).astype(np.int64)
        dur_us = np.where(np.isnan(dur), 0, np.round(np.nan_to_num(dur) * _MIN_US)).astype(np.int64)
        completed = started + dur_us

        status = np.asarray(t.column("status").to_pylist(), dtype=object)
        done = status == "completed"
        cancelled = status == "cancelled"
        no_driver = status == "no_driver"
        eta_promised = np.round(dur * rng.uniform(0.9, 1.1, n), 1)
        eta_actual = np.round(dur + rng.normal(0, cfg.eta_noise_minutes, n), 1)
        reason = np.where(cancelled, pick(rng, _CANCEL_REASONS, n), np.array(None, dtype=object))
        fare = np.where(done, num["fare"], np.nan)
        tip = np.where(done, num["tip"], np.nan)

        self._accepted_us = accepted
        self._started_us = started
        self._completed_us = completed
        self._is_done = done & self._req_ok & ~np.isnan(dur)
        self._is_cancel = cancelled

        tz = self._tz
        added: dict[str, pa.Array] = {
            "pickup_lat": float_array(plat),
            "pickup_lon": float_array(plon),
            "dropoff_lat": float_array(np.round(plat + np.cos(bearing) * span, 6)),
            "dropoff_lon": float_array(np.round(plon + np.sin(bearing) * span, 6)),
            "accepted_at": timestamps(accepted, tz, self._req_ok & ~no_driver),
            "started_at": timestamps(started, tz, self._is_done),
            "completed_at": timestamps(completed, tz, self._is_done),
            "wait_min": float_array(np.where(done, wait_min, np.nan)),
            "trip_date": pa.array(
                (req // (86_400 * _SEC_US)).astype("datetime64[D]"),
                type=pa.date32(),
                mask=~self._req_ok,
            ),
            "eta_promised_min": float_array(eta_promised),
            "eta_actual_min": float_array(np.clip(eta_actual, 1, None)),
            "is_cancelled": pa.array(cancelled, pa.bool_()),
            "cancel_reason": pa.array(reason, pa.string()),
            "source_store": pa.array(np.full(n, "SQL Database", dtype=object), pa.string()),
        }
        out = t
        for name in _NUMERIC_TRIP_COLUMNS:
            if name in t.column_names:
                values = fare if name == "fare" else tip if name == "tip" else num[name]
                out = out.set_column(out.column_names.index(name), name, float_array(values))
        for name, column in added.items():
            out = out.append_column(name, column)
        self._fare, self._tip = fare, tip
        return out

    # ---- trip events ------------------------------------------------------------------------

    def _trip_events(self, trip: pa.Table) -> pa.Table:
        """One event per lifecycle step a trip reached: requested, accepted, started and
        completed, or cancelled (30 to 300 seconds after acceptance)."""
        rng = self._rng
        status = np.asarray(trip.column("status").to_pylist(), dtype=object)
        steps: list[tuple[str, np.ndarray, np.ndarray]] = []
        steps.append(("requested", np.flatnonzero(self._req_ok), self._req_us))
        accepted = np.flatnonzero((status != "no_driver") & self._req_ok)
        steps.append(("accepted", accepted, self._accepted_us))
        done = np.flatnonzero(self._is_done)
        steps.append(("started", done, self._started_us))
        steps.append(("completed", done, self._completed_us))
        cancelled = np.flatnonzero(self._is_cancel & self._req_ok)
        cancel_us = self._accepted_us + np.round(
            rng.uniform(30, 300, len(self._accepted_us)) * _SEC_US
        ).astype(np.int64)
        steps.append(("cancelled", cancelled, cancel_us))

        rows = np.concatenate([rows for _, rows, _ in steps]).astype(np.int64)
        at = np.concatenate([us[rows_] for _, rows_, us in steps])
        kind = np.concatenate([np.full(len(rows_), name, dtype=object) for name, rows_, _ in steps])
        order = np.argsort(at, kind="stable")
        rows, at, kind = rows[order], at[order], kind[order]
        idx = pa.array(rows, pa.int64())

        def col(name: str) -> pa.Array:
            return trip.column(name).take(idx).combine_chunks()

        trip_id = col("trip_id")
        event_id = pc.binary_join_element_wise(
            pc.cast(trip_id, pa.string()), pa.array(kind, pa.string()), "-"
        )
        return pa.table(
            {
                "event_id": event_id,
                "trip_id": trip_id,
                "ts": timestamps(at, self._tz),
                "event_type": pa.array(kind, pa.string()),
                "driver_id": col("driver_id")
                if "driver_id" in trip.column_names
                else pa.nulls(len(rows)),
                "rider_id": col("rider_id")
                if "rider_id" in trip.column_names
                else pa.nulls(len(rows)),
                "city_id": col("city_id"),
                "lat": col("pickup_lat"),
                "lon": col("pickup_lon"),
                "source_store": pa.array(
                    np.full(len(rows), "Eventhouse", dtype=object), pa.string()
                ),
            }
        )

    # ---- surge signals ----------------------------------------------------------------------

    def _surge_signals(self) -> pa.Table:
        """Surge demand per city on a time grid over the most recent days: Poisson surge events
        lift the multiplier for a while, and each bucket carries drivers and open requests."""
        cfg, rng = self._cfg, self._rng
        now = self._now_us()
        bucket_us = cfg.surge_bucket_minutes * _MIN_US
        start = now - cfg.surge_recent_days * 86_400 * _SEC_US
        size = int((now - start) // bucket_us) + 1
        grid = start + np.arange(size, dtype=np.int64) * bucket_us
        frames: list[pa.Table] = []
        expected = cfg.surge_events_per_week * (cfg.surge_recent_days / 7.0)
        for cid in CITY_CENTROIDS:
            mult = np.ones(size)
            for _ in range(int(rng.poisson(expected))):
                first = int(rng.integers(0, size))
                length = int(rng.integers(*cfg.surge_duration_minutes))
                width = max(1, length // cfg.surge_bucket_minutes)
                peak = float(rng.uniform(*cfg.surge_multiplier_range))
                end = min(size, first + width)
                mult[first:end] = np.maximum(mult[first:end], peak)
            active = rng.integers(20, 200, size)
            open_requests = np.round(active * mult * rng.uniform(0.3, 1.5, size)).astype(np.int64)
            trigger = np.where(mult > 1.0, pick(rng, _SURGE_TRIGGERS, size), "baseline")
            frames.append(
                pa.table(
                    {
                        "signal_id": pa.array([f"{cid}-{i}" for i in range(size)], pa.string()),
                        "ts": timestamps(grid, self._tz),
                        "city_id": pa.array(np.full(size, cid, dtype=np.int64), pa.int64()),
                        "zone": pa.array(pick(rng, _ZONES, size), pa.string()),
                        "multiplier": float_array(np.round(mult, 2)),
                        "trigger": pa.array(trigger, pa.string()),
                        "active_drivers": pa.array(active.astype(np.int64), pa.int64()),
                        "open_requests": pa.array(open_requests, pa.int64()),
                    }
                )
            )
        out = pa.concat_tables(frames)
        return out.append_column(
            "source_store", pa.array(np.full(out.num_rows, "Eventhouse", dtype=object), pa.string())
        )

    # ---- driver pings -----------------------------------------------------------------------

    def _driver_pings(self, trip: pa.Table) -> pa.Table:
        """GPS pings along the route of every completed trip that overlaps the live window
        (the latest ``live_window_minutes``), at most ``max_live_trips`` of them."""
        cfg, rng = self._cfg, self._rng
        now = self._now_us()
        live = np.flatnonzero(
            self._is_done
            & (self._completed_us >= now - cfg.live_window_minutes * _MIN_US)
            & (self._started_us <= now)
        )
        if len(live) > cfg.max_live_trips:
            live = np.sort(rng.choice(live, size=cfg.max_live_trips, replace=False))
        schema = pa.schema(
            [
                ("ping_id", pa.string()),
                ("ts", pa.timestamp("us", self._tz)),
                (
                    "driver_id",
                    trip.schema.field("driver_id").type
                    if "driver_id" in trip.column_names
                    else pa.null(),
                ),
                ("trip_id", trip.schema.field("trip_id").type),
                ("city_id", trip.schema.field("city_id").type),
                ("lat", pa.float64()),
                ("lon", pa.float64()),
                ("heading", pa.float64()),
                ("speed_mph", pa.float64()),
                ("status", pa.string()),
                ("source_store", pa.string()),
            ]
        )
        if len(live) == 0:
            return schema.empty_table()
        s, e = self._started_us[live], self._completed_us[live]
        steps = np.minimum(
            np.maximum(2, ((e - s) / _SEC_US // cfg.ping_interval_seconds).astype(np.int64)), 60
        )
        rows = np.repeat(live, steps)
        first = np.cumsum(steps) - steps
        within = np.arange(len(rows)) - np.repeat(first, steps)
        frac = within / (np.repeat(steps, steps) - 1)
        jitter = cfg.gps_jitter_meters * _GPS_JITTER_DEG
        plat, plon = trip.column("pickup_lat").to_numpy(), trip.column("pickup_lon").to_numpy()
        dlat, dlon = trip.column("dropoff_lat").to_numpy(), trip.column("dropoff_lon").to_numpy()
        lat = plat[rows] + (dlat[rows] - plat[rows]) * frac + rng.normal(0, jitter, len(rows))
        lon = plon[rows] + (dlon[rows] - plon[rows]) * frac + rng.normal(0, jitter, len(rows))
        at = self._started_us[rows] + np.round(
            (self._completed_us[rows] - self._started_us[rows]) * frac
        ).astype(np.int64)
        heading = np.round(rng.uniform(0, 360, len(rows)), 1)
        speed = np.round(np.clip(rng.normal(22, 8, len(rows)), 0, 80), 1)
        order = np.argsort(at, kind="stable")
        rows, within, at = rows[order], within[order], at[order]
        idx = pa.array(rows, pa.int64())
        trip_id = trip.column("trip_id").take(idx).combine_chunks()
        ping_id = pc.binary_join_element_wise(
            pc.cast(trip_id, pa.string()), pa.array(within.astype(str), pa.string()), "-"
        )
        driver = (
            trip.column("driver_id").take(idx).combine_chunks()
            if "driver_id" in trip.column_names
            else pa.nulls(len(rows))
        )
        return pa.table(
            {
                "ping_id": ping_id,
                "ts": timestamps(at, self._tz),
                "driver_id": driver,
                "trip_id": trip_id,
                "city_id": trip.column("city_id").take(idx).combine_chunks(),
                "lat": float_array(np.round(lat, 6)[order]),
                "lon": float_array(np.round(lon, 6)[order]),
                "heading": float_array(heading[order]),
                "speed_mph": float_array(speed[order]),
                "status": pa.array(np.full(len(rows), "on_trip", dtype=object), pa.string()),
                "source_store": pa.array(
                    np.full(len(rows), "Eventhouse", dtype=object), pa.string()
                ),
            },
            schema=schema,
        )

    # ---- marts ------------------------------------------------------------------------------

    def _date_key(self) -> pa.Array:
        days = (self._req_us // (86_400 * _SEC_US)).astype("datetime64[D]")
        years = days.astype("datetime64[Y]").astype(int) + 1970
        months = days.astype("datetime64[M]").astype(int) % 12 + 1
        dom = (days - days.astype("datetime64[M]")).astype(int) + 1
        return pa.array(years * 10000 + months * 100 + dom, pa.int64(), mask=~self._req_ok)

    def _revenue_daily(self, trip: pa.Table) -> pa.Table:
        """Daily revenue per city: completed trips and what they earned (gross, surge, average
        fare and multiplier, unique riders and drivers) next to all trips and cancellations,
        and the platform's net after its fee. Days and cities with no completed trip are zeros."""
        surge = self._numeric("surge_mult")
        base = pa.table(
            {
                "date_key": self._date_key(),
                "city_id": trip.column("city_id"),
                "trip_id": trip.column("trip_id"),
                "rider_id": trip.column("rider_id")
                if "rider_id" in trip.column_names
                else pa.nulls(trip.num_rows),
                "driver_id": trip.column("driver_id")
                if "driver_id" in trip.column_names
                else pa.nulls(trip.num_rows),
                "fare": float_array(self._fare),
                "surge_mult": float_array(surge),
                "surge_fare": float_array(np.where(surge > 1.0, self._fare, np.nan)),
                "cancelled": pa.array(self._is_cancel.astype(np.int64), pa.int64()),
                "completed": pa.array(self._is_done, pa.bool_()),
            }
        ).filter(pa.array(self._req_ok))
        keys = ["date_key", "city_id"]
        zero_ok = pc.ScalarAggregateOptions(min_count=0)
        all_groups = base.group_by(keys, use_threads=False).aggregate(
            [("trip_id", "count"), ("cancelled", "sum", zero_ok)]
        )
        done = base.filter(base.column("completed"))
        done_groups = done.group_by(keys, use_threads=False).aggregate(
            [
                ("trip_id", "count"),
                ("fare", "list"),
                ("surge_fare", "list"),
                ("surge_mult", "list"),
                ("rider_id", "count_distinct"),
                ("driver_id", "count_distinct"),
            ]
        )
        done_exact = pa.table(
            {
                "date_key": done_groups.column("date_key"),
                "city_id": done_groups.column("city_id"),
                "completed_trips": done_groups.column("trip_id_count"),
                "fare_sum": pa.array(_exact_sums(done_groups.column("fare_list"))),
                "surge_fare_sum": pa.array(_exact_sums(done_groups.column("surge_fare_list"))),
                "fare_mean": pa.array(_exact_means(done_groups.column("fare_list"))),
                "surge_mult_mean": pa.array(_exact_means(done_groups.column("surge_mult_list"))),
                "unique_riders": done_groups.column("rider_id_count_distinct"),
                "unique_drivers": done_groups.column("driver_id_count_distinct"),
            }
        )
        joined = all_groups.join(done_exact, keys=keys, join_type="left outer")
        joined = joined.sort_by([("date_key", "ascending"), ("city_id", "ascending")])

        def num(name: str, integer: bool = False) -> np.ndarray:
            col: np.ndarray = np.nan_to_num(
                joined.column(name).to_numpy().astype(np.float64), nan=0.0
            )
            return col.astype(np.int64) if integer else col  # a day with no completed trip is 0

        fare_sum = num("fare_sum")
        n = joined.num_rows
        return pa.table(
            {
                "date_key": joined.column("date_key"),
                "city_id": joined.column("city_id"),
                "completed_trips": pa.array(num("completed_trips", True), pa.int64()),
                "gross_revenue": float_array(np.round(fare_sum, 2)),
                "surge_revenue": float_array(np.round(num("surge_fare_sum"), 2)),
                "avg_fare": float_array(np.round(num("fare_mean"), 2)),
                "avg_surge_mult": float_array(np.round(num("surge_mult_mean"), 2)),
                "unique_riders": pa.array(num("unique_riders", True), pa.int64()),
                "unique_drivers": pa.array(num("unique_drivers", True), pa.int64()),
                "trips": pa.array(num("trip_id_count", True), pa.int64()),
                "cancelled_trips": pa.array(num("cancelled_sum", True), pa.int64()),
                "net_revenue": float_array(np.round(fare_sum * (1 - _PLATFORM_FEE), 2)),
                "source_store": pa.array(np.full(n, "Warehouse", dtype=object), pa.string()),
            }
        )

    def _driver_earnings(self, trip: pa.Table) -> pa.Table:
        """Daily earnings per driver from completed trips: trips, gross, tips, online hours,
        average rating, the platform fee, the payout and utilisation of an 8-hour day."""
        if "driver_id" not in trip.column_names:
            raise ValueError("trip needs a driver_id column for driver earnings")
        base = pa.table(
            {
                "date_key": self._date_key(),
                "driver_id": trip.column("driver_id"),
                "city_id": trip.column("city_id"),
                "trip_id": trip.column("trip_id"),
                "fare": float_array(self._fare),
                "tip": float_array(self._tip),
                "duration_min": float_array(self._numeric("duration_min")),
                "rating_given": float_array(self._numeric("rating_given")),
            }
        )
        base = base.filter(pa.array(self._is_done & self._req_ok))
        base = base.filter(pc.is_valid(base.column("driver_id")))
        groups = base.group_by(["date_key", "driver_id"], use_threads=False).aggregate(
            [
                ("city_id", "first"),
                ("trip_id", "count"),
                ("fare", "list"),
                ("tip", "list"),
                ("duration_min", "list"),
                ("rating_given", "list"),
            ]
        )
        groups = groups.sort_by([("date_key", "ascending"), ("driver_id", "ascending")])

        gross = np.nan_to_num(_exact_sums(groups.column("fare_list")))
        tips = np.nan_to_num(_exact_sums(groups.column("tip_list")))
        minutes = _exact_sums(groups.column("duration_min_list"))
        hours = np.array([round(float(m) / 60.0, 2) for m in minutes])  # decimal rounding
        rating = _exact_means(groups.column("rating_given_list"))
        n = groups.num_rows
        return pa.table(
            {
                "date_key": groups.column("date_key"),
                "driver_id": groups.column("driver_id"),
                "city_id": groups.column("city_id_first"),
                "trips": pa.array(
                    groups.column("trip_id_count").to_numpy().astype(np.int64), pa.int64()
                ),
                "gross": float_array(np.round(gross, 2)),
                "tips": float_array(np.round(tips, 2)),
                "online_hours": float_array(hours),
                "avg_rating": float_array(np.round(rating, 1)),
                "platform_fee": float_array(np.round(gross * _PLATFORM_FEE, 2)),
                "payout": float_array(np.round(gross * (1 - _PLATFORM_FEE) + tips, 2)),
                "utilization_pct": float_array(np.round(np.clip(hours / 8.0 * 100, 0, 100), 2)),
                "source_store": pa.array(np.full(n, "Warehouse", dtype=object), pa.string()),
            }
        )
