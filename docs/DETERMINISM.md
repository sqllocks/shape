# Determinism Contract

Stateless generation strategies are index-deterministic: `(seed, row_index, plan)` identifies a row independent of partition boundaries or execution order.

Stateful strategies such as `FirstPerParent` additionally depend on ordered prior state and therefore require an ordered execution plan/checkpoint. They MUST NOT be advertised as random-access deterministic.

Streaming replay is checkpoint-deterministic for deterministic handlers/sources. External side effects require an idempotent/transactional sink to obtain stronger delivery guarantees.
