# RQ-8 — Production Streaming Foundation

## Completed locally
- Mergeable bounded numeric evidence: moments, min/max, HLL cardinality, KLL quantiles.
- Mergeable bounded text evidence: length evidence, HLL cardinality and SpaceSaving heavy hitters.
- TTL + maximum-cardinality keyed state with checkpointable snapshot/restore.
- Vectorized text profiling and conservative vector semantic candidate detection.
- Kafka and Azure Event Hubs adapters with optional SDK dependencies.
- Connector contract qualification: 100k identical logical records through Kafka/Event Hubs adapters produce zero Shape drift versus the direct columnar reference.
- RQ-7 streaming combination suite remains applicable above the adapters.

## Text performance
1M email-like strings: vector text profile + semantic candidate detection measured ~666k rows/sec locally. This is a large acceleration path but does **not** yet satisfy the 1M/sec full text+semantic objective.

## Connector status
The connector code is implemented, but this execution environment has neither `confluent-kafka` nor `azure-eventhub` installed and no broker/Event Hubs credentials/endpoints were supplied. Therefore live network qualification cannot truthfully be marked complete. The offline adapter/conformance suite passes with zero evidence drift.

Live qualification requires external infrastructure:
- Kafka bootstrap servers, topic and credentials/TLS configuration;
- Event Hubs namespace/connection details, hub, consumer group and Azure credentials;
- an agreed destructive/non-production test namespace/topic.

## Remaining evidence expansion
The bounded model now covers numeric moments/quantiles/cardinality and text length/cardinality/heavy hitters. Full Platinum streaming still needs bounded/mergeable implementations for nonlinear dependence, missingness dependence, temporal evidence, and general relational/geographic evidence before “entire Shape model bounded” can be claimed without qualification.
