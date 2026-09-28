# RQ-7 Matrix Summary

Qualified combinations include:
- profiling × arbitrary batching;
- profiling × drift × schema evolution;
- profiling × FK constraints × geographic columns;
- profiling × non-finite numeric values;
- history × checkpointing;
- checkpointing × multiple injected crash points;
- privacy detection × conservative release assessment;
- event time × out-of-order × watermark × late-event rejection;
- bounded aggregate windows × 100k events;
- profiling × drift × quality × durable checkpointing × 10M-row sustained throughput;
- 100 randomized batch-boundary equivalence trials.

This matrix is extensible: every new Strategy, evidence type, streaming operator, Pack, or connector must add at least one invariant/property case and one dangerous-combination case.
