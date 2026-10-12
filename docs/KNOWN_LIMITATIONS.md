# Known limitations

Check these constraints before you design a workflow.

Status: available (profiles, contracts and drift); experimental (other surfaces).

Shape is early access 0.9.1. Generation from a profile is available and is being hardened.
The 1.x compatibility promises describe future policy.

- `shape generate --from X.shape` writes nothing without `--format`. An output directory alone
  does not select a writer. The Python tutorial explicitly writes its Arrow result to CSV.
- DuckDB write URIs use `duckdb:///rel.duckdb`. Read URIs use `duckdb://rel.duckdb?table=T`.
  They come from different adapters: databases writes; integrations reads.
- `shape profile validate --safe` fails on multi-table `--dataset` profiles in 0.9.1.
  Single-table profiles pass in the tutorials. Do not treat that failure as proof of a leak
  or bypass it as proof that a dataset is safe. <!-- owner: privacy maintainer — dataset validator fix. -->
- Snowflake and Databricks are write targets. Shape does not profile from them yet.
- Model queries read model payloads, not saved profile payloads. A profile passed to a query
  is rejected. See [Models](MODELS.md).
- A safe capture omits values. A contract needing omitted evidence can be unavailable rather
  than pass. Full captures, vault-backed generation and source rows need your source-data controls.
- A safe capture is data minimisation, not anonymisation. Small groups, aggregate statistics
  and repeated releases still require review.
- Delta deletion vectors or column mapping need the optional DuckDB fallback. Its extension
  may need a first-use download. Historical Delta data must still exist, not have been vacuumed.
- A folder is one partitioned table unless you select dataset mode. Mixed column sets are refused
  by the CLI. A table contract and a dataset contract are different inputs.
- Exact profiling can scan all input. Do not infer a memory or elapsed-time guarantee from a
  small local tutorial. Optional analyses and generation may materialize tables.
- Plugins run in the same process. Allow-lists restrict loading, not plugin behavior.

## Fabric in 0.9.1

- Fabric Warehouse rejects the inline `PRIMARY KEY` emitted by 0.9.1 with error 24584.
  Workaround: create the destination tables yourself without inline keys, optionally add
  `ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY NONCLUSTERED (...) NOT ENFORCED`, then
  write with `--write-mode append`. A fix is in progress.
- Schema-enabled Lakehouses require `Tables/<schema>/<table>` (normally `dbo`), whereas
  0.9.1's Delta target appends a table directly below the supplied directory. Workaround:
  target a URI ending in `Tables/dbo` or another existing schema, rather than `Tables`.
  Read back with `onelake://<workspace>/<lakehouse>/Tables/dbo/<table>`. Files landing zones
  are unaffected. A fix is in progress.

## Alpine and musllinux

`confluent-kafka` has no musllinux wheels for ARM64 or x86_64 and builds from source.
The `[kafka]` and `[all]` extras require librdkafka **2.16 or newer**, development
headers and C build tools. Alpine's `librdkafka-dev` is currently older, so installing
that package alone is insufficient. Build the matching native library before
running pip; see the [Alpine installation steps](INSTALL.md#alpine-and-musllinux),
which match the release smoke environment, or
[Confluent's build instructions](https://github.com/confluentinc/librdkafka#building).

## Related

[Troubleshooting](TROUBLESHOOTING.md) · [What leaves my machine](WHAT_LEAVES.md)
