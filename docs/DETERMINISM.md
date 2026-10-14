# Determinism Contract

Status: experimental.


Stateless generation strategies are index-deterministic: `(seed, row_index, plan)` identifies a row independent of partition boundaries or execution order.

A strategy that looks at earlier rows, such as `FirstPerParent` ("first row of this parent"), is still a function of `(seed, row_index, plan)`: the plan finds the earlier rows itself, so the answer for row `i` never depends on which rows were requested before.

The generation engine's contract (row-addressed random streams, chunk-independent output) is in [GENERATION_ENGINE.md](GENERATION_ENGINE.md).
