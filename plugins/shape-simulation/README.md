# sqllocks-shape-simulation

Shape plugin: simulation scenarios. Turns generated tables into what an upstream system would
produce over time.

| Simulator | Module | `shape simulate` |
|---|---|---|
| `FileDropSimulator` | `shape_simulation.file_drop` | `file-drop` |
| `SCD2FileDropSimulator` | `shape_simulation.scd2_file_drops` | `scd2` |
| `StreamEmitter` | `shape_simulation.stream_emit` | `stream` |
| `HybridSimulator` | `shape_simulation.hybrid` | `hybrid` |
| `WorkflowSimulator` | `shape_simulation.state_machine` | `workflow` |

Each takes Arrow tables (or a generation result) and is deterministic for a seed. The simulators,
their settings and the events they write are described in `docs/SIMULATION_FILES_EVENTS.md` in the
repository.

Install with `pip install 'sqllocks-shape[simulation]'`. It registers one plugin command, `simulate`
(`shape.commands`); the simulators are importable from `shape_simulation` without it.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.
