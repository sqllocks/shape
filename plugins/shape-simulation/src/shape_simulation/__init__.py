"""Shape plugin: simulation scenarios.

The pattern simulators (``clickstream_patterns``, ``financial_patterns``, ``iot_patterns``,
``operational_log_patterns``, ``pulse_patterns``) take a configuration and, where they layer on
existing data, Arrow tables, and return Arrow tables and summary statistics. ``shape simulate``
runs them from the command line. Names import lazily, so ``import shape_simulation`` is cheap
(T-18).
"""

from __future__ import annotations

import importlib
from typing import Any

SHAPE_API = "1.0"
"""The plugin targets the plugin API major version this Shape provides."""

_EXPORTS: dict[str, str] = {
    "ClickstreamConfig": "clickstream_patterns",
    "ClickstreamResult": "clickstream_patterns",
    "ClickstreamSimulator": "clickstream_patterns",
    "FinancialStreamConfig": "financial_patterns",
    "FinancialStreamResult": "financial_patterns",
    "FinancialStreamSimulator": "financial_patterns",
    "IoTTelemetryConfig": "iot_patterns",
    "IoTTelemetryResult": "iot_patterns",
    "IoTTelemetrySimulator": "iot_patterns",
    "OperationalLogConfig": "operational_log_patterns",
    "OperationalLogResult": "operational_log_patterns",
    "OperationalLogSimulator": "operational_log_patterns",
    "PulseDemandConfig": "pulse_patterns",
    "PulseDemandSimulator": "pulse_patterns",
    "PulseSimResult": "pulse_patterns",
    "SimulateCommand": "simulate",
}

__all__ = sorted(_EXPORTS) + ["SHAPE_API"]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)
