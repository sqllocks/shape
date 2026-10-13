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

- 0.9.1 emitted the Warehouse primary key inside `CREATE TABLE`, which a Fabric Warehouse rejects
  with error 24584. Fixed after 0.9.1: the key is added by a separate
  `ALTER TABLE ... ADD CONSTRAINT ... PRIMARY KEY NONCLUSTERED (...) NOT ENFORCED` after the table is
  created. On 0.9.1, create the destination tables yourself without inline keys, optionally add that
  `ALTER TABLE`, and write with `--write-mode append`.
- Schema-enabled Lakehouses require `Tables/<schema>/<table>` (normally `dbo`), whereas
  0.9.1's Delta target appended a table directly below the supplied directory, so the tables did
  not appear in the SQL analytics endpoint or in semantic models. Fixed in 0.9.2: the Delta sink
  and `fabric_spark` ask Fabric which kind of Lakehouse the target is and write the schema folder
  (`dbo` by default); they stop with a clear error rather than guess. On 0.9.1, target a URI ending
  in `Tables/dbo` (or another existing schema). Files landing zones were never affected. See
  [Lakehouse table layouts](plugins/cloud-sources.md#lakehouse-table-layouts).
- **Fabric Warehouse `COPY INTO` can fall back to row-by-row `INSERT`.** Shape stages Parquet in
  OneLake and loads it with `COPY INTO`. When the Warehouse answers SQL error 13840 ("Access token
  couldn't be fetched for storage path"), it rolls back the statement, reapplies an uncommitted
  truncate or replace, and loads the same staged files with parameterized `INSERT` statements in
  5,000-row batches. The load stays all-or-nothing and the row count is verified, but it is far
  slower than `COPY INTO` and Microsoft advises against singleton-style inserts for ingestion.
  Shape never hides this: it prints `WARNING:` on stderr naming the table, the error, the row
  count and the slower path, raises a `RuntimeWarning`, and adds the same line to
  `WriteResult.notes`, which `WriteResult.summary()` prints. Any other `COPY INTO` error is still
  an error. Microsoft's documented constraints that bear on it:
  - `COPY INTO` reads OneLake with the executing user's Microsoft Entra identity unless a
    credential is given; a workspace identity can be named with
    `CREDENTIAL = (IDENTITY = 'Workspace Identity')`, which needs the identity added to the
    source workspace as Contributor and the executing identity to hold at least Viewer on the
    Warehouse's workspace
    ([Ingest data into the warehouse](https://learn.microsoft.com/fabric/data-warehouse/ingest-data)).
    Shape does not switch identities for you.
  - A new service principal has no Fabric security token until it has made a first successful
    Fabric REST API call; connecting to the SQL endpoint does not create one, and until then
    `COPY INTO` and `OPENROWSET` against storage fail with missing-file or permission errors
    even though the principal holds the permissions. Fabric also expects the token to be
    renewed every 30 days
    ([Service principals in Fabric Data Warehouse](https://learn.microsoft.com/fabric/data-warehouse/service-principals)).
  - With workspace outbound access protection on, `COPY INTO` may read only from the same
    workspace or from OneLake
    ([Workspace outbound access protection for Data Warehouse](https://learn.microsoft.com/fabric/security/workspace-outbound-access-protection-data-warehouse)).
  - `COPY INTO` accepts CSV, JSONL and Parquet from ADLS Gen2, Blob Storage and OneLake, and
    Microsoft recommends files of at least 4 MB
    ([Ingest data into the warehouse](https://learn.microsoft.com/fabric/data-warehouse/ingest-data)).

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
