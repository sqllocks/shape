# Streaming Semantics — Draft 1

Shape distinguishes event time from processing time. Stateful operators declare windowing, allowed lateness and checkpoint semantics. Watermarks determine closable windows; events older than the watermark are handled by an explicit late-data policy. State MUST be bounded or externally checkpointed. Replay MUST be deterministic for deterministic sources/operators. Source delivery guarantees and sink commit guarantees are reported separately; Shape does not claim exactly-once when an underlying connector cannot provide it.
