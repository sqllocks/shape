# Spindle -> Shape Migration Matrix
Status: Initial feature-level matrix; source-level audit still required.

Requirement: SHAPE-COMPAT-001.

| Spindle capability | Shape disposition | Shape home |
|---|---|---|
| 13 calibrated domains | SUPERSEDED | Shape Packs |
| named distribution profiles | SUPERSEDED | Pack defaults + Shape/Scenario |
| sequence/uuid/faker/enum/distribution | SUPPORTED | Value generators |
| temporal strategy | SUPERSEDED | Temporal Shape + generator |
| formula/derived/computed | SUPERSEDED | Derivation Model |
| correlated/conditional | SUPERSEDED | Dependency/conditional model |
| lifecycle | SUPERSEDED | EntityLifecycle |
| foreign keys/composite keys | SUPPORTED | Relationships/constraints |
| lookup | SUPPORTED | Derivation/relationship |
| reference_data | SUPPORTED | Pack Reference Assets |
| pattern | SUPPORTED | Semantic/value generator |
| self-reference | SUPPORTED | Relationship model |
| record_sample/record_field | PLANNED | Reference anchoring with privacy rules |
| SCD2 | SUPERSEDED | Temporal Entity Versioning |
| custom .spindle.json | SUPERSEDED | Open Shape Spec + Pack/Policy |
| learn | SUPERSEDED | capture |
| compare | SUPERSEDED | diff/fidelity |
| mask | SUPPORTED | mask() |
| DDL import | SUPERSEDED | generalized import |
| incremental continue | SUPERSEDED | History + incremental generation |
| time travel | SUPERSEDED | History/ShapeSeries |
| composite domains | SUPERSEDED | Pack composition |
| chaos | SUPERSEDED | Data Chaos/Scenario |
| streaming/burst | SUPERSEDED | native streaming/Operational Shape |
| star schema conversion | SUPERSEDED | Shape Transform |
| CDM conversion | PLANNED | Shape Transform/plugin |
| semantic model export | PLANNED | plugin |
| CSV/JSONL/TSV/SQL | SUPPORTED/PLANNED | sinks |
| Parquet/Delta | SUPPORTED/PLANNED | Arrow/connector sinks |
| Excel | PLANNED LOW PRIORITY | optional sink |
| Fabric Lakehouse/Warehouse/SQL DB/Eventhouse | PLANNED | connectors |
| Kafka/Event Hub | PLANNED | streaming sinks |
| MCP | PLANNED | Shape MCP |
| observability | SUPERSEDED | Quality/History/Lineage |
| clickstream simulation | SUPERSEDED | EventSequence |
| IoT telemetry | SUPERSEDED | TelemetrySeries |
| financial streams | SUPERSEDED | TransactionStream |
| operational logs | SUPERSEDED | EventSequence |
| workflow state machines | SUPERSEDED | StateMachine |
| SCD2 file drops | SUPERSEDED | FileArrivalProcess + Temporal Entity Versioning |
| xxl/xxxl scale tiers | SUPERSEDED | compiler scale targets |

## Source audit still required
For every source module/test/fixture:
REUSE CODE | REUSE IDEA | REIMPLEMENT | REDESIGN | DISCARD.
Record original file, license provenance, target Shape requirement and corresponding conformance test.
