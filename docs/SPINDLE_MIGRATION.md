# Migrating from Spindle

Shape intentionally does not preserve Spindle class names. Preserve semantics instead: domain/reference generation maps to Packs; profiling/inference maps to Capture/Profile; comparison to Drift; incremental/time travel to History/ShapeSeries; streaming to event-time Streaming; chaos to Scenario/failure injection; sinks to Connectors; address record sampling to coherent Location/Address generation.

Spindle MIT code can be reused under its license, but Shape's preference is clean reimplementation against documented behavior. Third-party data retains its own license/provenance.
