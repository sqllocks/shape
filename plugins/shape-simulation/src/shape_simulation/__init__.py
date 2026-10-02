"""Shape plugin: simulation scenarios (P6-04).

Each simulator lives in its own module and is also importable from here, lazily (importing the
package loads no simulator, and none of Arrow or NumPy):

* ``file_drop``: :class:`FileDropSimulator`, an upstream source landing files over a date range;
* ``scd2_file_drops``: :class:`SCD2FileDropSimulator`, a full load and daily versioned deltas;
* ``stream_emit``: :class:`StreamEmitter`, tables as enveloped events on the emit runtime;
* ``hybrid``: :class:`HybridSimulator`, a file drop and a stream of the same tables, linked;
* ``state_machine``: :class:`WorkflowSimulator`, business-process events with dwell times;
* ``clickstream_patterns``, ``financial_patterns``, ``iot_patterns``,
  ``operational_log_patterns``, ``pulse_patterns``: the pattern simulators (sessions and funnels,
  reversals and fraud bursts, sensor drift and alert storms, service logs and traces, rideshare
  telemetry and marts), which take a configuration and, where they layer on existing data, Arrow
  tables, and return Arrow tables and summary statistics;
* ``simulate``: ``shape simulate``, the command that runs the pattern simulators.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

SHAPE_API = "1.0"
"""The plugin targets the plugin API major version this Shape provides."""

# name -> module that defines it (one line per name; each simulator adds its own lines)
_EXPORTS: dict[str, str] = {
    "ClickstreamConfig": "shape_simulation.clickstream_patterns",
    "ClickstreamResult": "shape_simulation.clickstream_patterns",
    "ClickstreamSimulator": "shape_simulation.clickstream_patterns",
    "FileDropConfig": "shape_simulation.file_drop",
    "FileDropResult": "shape_simulation.file_drop",
    "FileDropSimulator": "shape_simulation.file_drop",
    "FinancialStreamConfig": "shape_simulation.financial_patterns",
    "FinancialStreamResult": "shape_simulation.financial_patterns",
    "FinancialStreamSimulator": "shape_simulation.financial_patterns",
    "HybridConfig": "shape_simulation.hybrid",
    "HybridResult": "shape_simulation.hybrid",
    "HybridSimulator": "shape_simulation.hybrid",
    "IoTTelemetryConfig": "shape_simulation.iot_patterns",
    "IoTTelemetryResult": "shape_simulation.iot_patterns",
    "IoTTelemetrySimulator": "shape_simulation.iot_patterns",
    "OperationalLogConfig": "shape_simulation.operational_log_patterns",
    "OperationalLogResult": "shape_simulation.operational_log_patterns",
    "OperationalLogSimulator": "shape_simulation.operational_log_patterns",
    "PulseDemandConfig": "shape_simulation.pulse_patterns",
    "PulseDemandSimulator": "shape_simulation.pulse_patterns",
    "PulseSimResult": "shape_simulation.pulse_patterns",
    "SCD2FileDropConfig": "shape_simulation.scd2_file_drops",
    "SCD2FileDropResult": "shape_simulation.scd2_file_drops",
    "SCD2FileDropSimulator": "shape_simulation.scd2_file_drops",
    "SimulateCommand": "shape_simulation.simulate",
    "StateDefinition": "shape_simulation.state_machine",
    "StreamEmitConfig": "shape_simulation.stream_emit",
    "StreamEmitResult": "shape_simulation.stream_emit",
    "StreamEmitter": "shape_simulation.stream_emit",
    "TransitionRule": "shape_simulation.state_machine",
    "WorkflowConfig": "shape_simulation.state_machine",
    "WorkflowResult": "shape_simulation.state_machine",
    "WorkflowSimulator": "shape_simulation.state_machine",
    "get_preset_workflow": "shape_simulation.state_machine",
}

__all__ = [
    "ClickstreamConfig",
    "ClickstreamResult",
    "ClickstreamSimulator",
    "FileDropConfig",
    "FileDropResult",
    "FileDropSimulator",
    "FinancialStreamConfig",
    "FinancialStreamResult",
    "FinancialStreamSimulator",
    "HybridConfig",
    "HybridResult",
    "HybridSimulator",
    "IoTTelemetryConfig",
    "IoTTelemetryResult",
    "IoTTelemetrySimulator",
    "OperationalLogConfig",
    "OperationalLogResult",
    "OperationalLogSimulator",
    "PulseDemandConfig",
    "PulseDemandSimulator",
    "PulseSimResult",
    "SCD2FileDropConfig",
    "SCD2FileDropResult",
    "SCD2FileDropSimulator",
    "SimulateCommand",
    "StateDefinition",
    "StreamEmitConfig",
    "StreamEmitResult",
    "StreamEmitter",
    "TransitionRule",
    "WorkflowConfig",
    "WorkflowResult",
    "WorkflowSimulator",
    "get_preset_workflow",
]

if TYPE_CHECKING:
    from shape_simulation.clickstream_patterns import (
        ClickstreamConfig,
        ClickstreamResult,
        ClickstreamSimulator,
    )
    from shape_simulation.file_drop import FileDropConfig, FileDropResult, FileDropSimulator
    from shape_simulation.financial_patterns import (
        FinancialStreamConfig,
        FinancialStreamResult,
        FinancialStreamSimulator,
    )
    from shape_simulation.hybrid import HybridConfig, HybridResult, HybridSimulator
    from shape_simulation.iot_patterns import (
        IoTTelemetryConfig,
        IoTTelemetryResult,
        IoTTelemetrySimulator,
    )
    from shape_simulation.operational_log_patterns import (
        OperationalLogConfig,
        OperationalLogResult,
        OperationalLogSimulator,
    )
    from shape_simulation.pulse_patterns import (
        PulseDemandConfig,
        PulseDemandSimulator,
        PulseSimResult,
    )
    from shape_simulation.scd2_file_drops import (
        SCD2FileDropConfig,
        SCD2FileDropResult,
        SCD2FileDropSimulator,
    )
    from shape_simulation.simulate import SimulateCommand
    from shape_simulation.state_machine import (
        StateDefinition,
        TransitionRule,
        WorkflowConfig,
        WorkflowResult,
        WorkflowSimulator,
        get_preset_workflow,
    )
    from shape_simulation.stream_emit import StreamEmitConfig, StreamEmitResult, StreamEmitter


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'shape_simulation' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])
