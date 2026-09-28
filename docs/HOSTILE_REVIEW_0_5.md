# Hostile Engineering Review — 0.5

P0 findings addressed in this pass:
- artifact crypto was primitives-only -> integrated signed/encrypted envelope
- dependency semantics were too shallow -> candidate-key and functional-dependency evidence
- generation lacked explicit relational/lifecycle primitives -> parent/child + SCD2
- no usable command surface -> operational CLI added
- no repeatable local performance baseline -> benchmark harness added

Remaining engineering risks:
- canonical JSON is intentionally a strict subset, not RFC 8785; floats are rejected rather than normalized
- Arrow tests cannot execute in this environment
- dependency profiling currently materializes exact categorical maps and needs bounded/sketched high-cardinality modes
- plugin runtime is process-isolated but not an OS sandbox
- enterprise/broker connectors need real systems for integration behavior
- geo production assets need an explicit build/release pipeline and redistribution decision
- Python reference paths require scale profiling before any Rust acceleration decision
