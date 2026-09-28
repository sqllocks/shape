# External Gates after 0.12

These are intentionally not self-certified:

1. Arrow execution matrix — PyArrow is absent from this runtime; retained tests and package metadata cover the intended surface.
2. Managed-service connectors — Kafka/Event Hub/Kinesis/Fabric/Snowflake/Databricks/SQL Server require drivers plus live services/test containers and credentials/endpoints.
3. Independent interoperability — a meaningful cross-language claim requires a separately implemented reader/writer.
4. Independent security/privacy review — independence cannot be supplied by the implementation itself.
5. FIPS — applies to a validated cryptographic module/provider and deployment configuration.
6. Classified-system authorization — requires the actual authorizing organization, environment, controls and procedures.
7. Production-scale performance claims — require representative target hardware/workloads. A 1M materialized local attempt exceeded this environment's execution window and is not claimed.
8. Production geography bundle — requires final redistribution/licensing/product decisions for chosen reference datasets.
