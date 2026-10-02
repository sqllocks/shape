# sqllocks-shape-simulation

Shape plugin: simulation scenarios. Turns generated tables into what an upstream system would
produce over time, and generates the telemetry of systems that have no table behind them.

| Simulator | Module | `shape simulate` |
|---|---|---|
| `FileDropSimulator` | `shape_simulation.file_drop` | `file-drop` |
| `SCD2FileDropSimulator` | `shape_simulation.scd2_file_drops` | `scd2` |
| `StreamEmitter` | `shape_simulation.stream_emit` | `stream` |
| `HybridSimulator` | `shape_simulation.hybrid` | `hybrid` |
| `WorkflowSimulator` | `shape_simulation.state_machine` | `workflow` |
| `ClickstreamSimulator` | `shape_simulation.clickstream_patterns` | `clickstream` |
| `FinancialStreamSimulator` | `shape_simulation.financial_patterns` | `financial` |
| `IoTTelemetrySimulator` | `shape_simulation.iot_patterns` | `iot` |
| `OperationalLogSimulator` | `shape_simulation.operational_log_patterns` | `operational-log` |
| `PulseDemandSimulator` | `shape_simulation.pulse_patterns` | `pulse` |

Each takes a configuration and, where it works on existing data, Arrow tables (or a generation
result), and is deterministic for a seed. The first five are described in
`docs/SIMULATION_FILES_EVENTS.md`, the pattern simulators (the last five) in
`docs/plugins/simulation.md`.

```bash
pip install 'sqllocks-shape[simulation]'
shape simulate clickstream --set users=500 -o out/
```

```python
from shape_simulation.clickstream_patterns import ClickstreamConfig, ClickstreamSimulator

result = ClickstreamSimulator(ClickstreamConfig(users=500, seed=7)).run()
result.sessions, result.page_views, result.funnels, result.stats
```

It registers one plugin command, `simulate` (`shape.commands`); the simulators are importable from
`shape_simulation` without it. Its version always equals core's (`sqllocks-shape`), and it is
released together with core. How plugins are written: `docs/plugins/authoring.md` in the repository.
