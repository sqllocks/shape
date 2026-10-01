# Determinism Contract

Stateless generation strategies are index-deterministic: `(seed, row_index, plan)` identifies a row independent of partition boundaries or execution order.

A strategy that looks at earlier rows, such as `FirstPerParent` ("first row of this parent"), is still a function of `(seed, row_index, plan)`: the plan finds the earlier rows itself, so the answer for row `i` never depends on which rows were requested before. The cost is a scan of rows `0..i` the first time a plan answers for row `i`, which later calls reuse.

The generation engine's contract (row-addressed random streams, chunk-independent output) is in [GENERATION_ENGINE.md](GENERATION_ENGINE.md).

Streaming replay is checkpoint-deterministic for deterministic handlers/sources. External side effects require an idempotent/transactional sink to obtain stronger delivery guarantees.
