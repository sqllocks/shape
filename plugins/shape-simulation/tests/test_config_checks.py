"""Issue #433: the pattern configurations refuse settings they cannot use, naming the setting,
so ``shape simulate`` exits 2 with a message instead of a traceback (exit 1)."""

from __future__ import annotations

import pytest
from shape_simulation.clickstream_patterns import ClickstreamConfig
from shape_simulation.financial_patterns import FinancialStreamConfig
from shape_simulation.iot_patterns import IoTTelemetryConfig
from shape_simulation.operational_log_patterns import OperationalLogConfig
from shape_simulation.pulse_patterns import PulseDemandConfig

from shape.cli.main import main

REFUSED = [
    (OperationalLogConfig, "latency_mean_ms", 0),
    (OperationalLogConfig, "service_count", -1),
    (OperationalLogConfig, "service_count", 9),
    (OperationalLogConfig, "outage_error_rate", 1.5),
    (OperationalLogConfig, "events_per_hour", -1),
    (IoTTelemetryConfig, "reading_interval_seconds", 0),
    (IoTTelemetryConfig, "alert_storm_rate_multiplier", 0),
    (IoTTelemetryConfig, "missing_probability", -0.1),
    (FinancialStreamConfig, "settlement_batch_hours", 0),
    (FinancialStreamConfig, "settlement_batch_hours", -4),
    (FinancialStreamConfig, "reversal_probability", 2),
    (PulseDemandConfig, "surge_bucket_minutes", 0),
    (PulseDemandConfig, "ping_interval_seconds", 0),
    (ClickstreamConfig, "users", "abc"),
    (ClickstreamConfig, "users", -5),
    (ClickstreamConfig, "avg_pages_per_session", -1),
    (ClickstreamConfig, "bounce_rate", float("nan")),
    (ClickstreamConfig, "page_pool", []),
    (ClickstreamConfig, "device_types", []),
]


@pytest.mark.parametrize(("config", "name", "value"), REFUSED)
def test_a_setting_that_cannot_work_is_refused_with_its_name(config, name, value):
    with pytest.raises(ValueError, match=rf"^{name} "):
        config(**{name: value})


def test_a_service_without_a_name_is_refused():
    with pytest.raises(ValueError, match="services"):
        OperationalLogConfig(services=[{"level": "INFO"}])


def test_the_defaults_and_edge_values_that_work_are_accepted():
    for config in (
        ClickstreamConfig,
        FinancialStreamConfig,
        IoTTelemetryConfig,
        OperationalLogConfig,
        PulseDemandConfig,
    ):
        config()
    OperationalLogConfig(service_count=8, latency_std_ms=0, events_per_hour=0)
    ClickstreamConfig(users=0, bounce_rate=1.0, bot_traffic_enabled=False, bot_pages_per_session=0)
    FinancialStreamConfig(duration_hours=0.5, reversal_probability=0.0)


@pytest.mark.parametrize(
    "argv",
    [
        ["operational-log", "--set", "latency_mean_ms=0"],
        ["iot", "--set", "reading_interval_seconds=0"],
        ["clickstream", "--set", "users=abc"],
    ],
)
def test_the_command_exits_2_and_names_the_setting(argv, capsys):
    assert main(["simulate", *argv]) == 2
    name = argv[-1].partition("=")[0]
    assert f"shape: error: {name}" in capsys.readouterr().err
