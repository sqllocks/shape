# LIVE-FIX

Branch `fix/live-findings`, from `d09731e5`. Owner live acceptance pending.

## Defects and evidence

Versions, gates and tolerances are unchanged. No live service was available to this session;
the Owner must repeat live acceptance against the pushed branch.

| Defect | Cause and fix | Regression evidence |
|---|---|---|
| 1: MySQL without TLS | PyMySQL can crash during `caching_sha2_password` full authentication. `shape_databases/mysql.py` translates that specific no-TLS crash into a message naming verified TLS and supported server/user authentication configuration. It does not fetch an untrusted RSA key or add an optional RSA dependency. | `test_owner_mysql_auth.py`: actionable-error case fails on old code; unrelated driver error remains unchanged. |
| 2: CLI cloud credentials | Reader credentials lacked storage environment fallback and CLI references. `security/azure_credentials.py` and `cli/source_credentials.py` provide command-scoped, reference-only `--source-config`, with explicit credentials before account key, SAS, connection string, then notebook/default identity. | `test_live_findings.py` and `test_live_cloud_credentials.py`: environment selection and env/file/kv CLI references fail before, pass after. |
| 3: permanent sign-in errors | Azure authentication errors reached retry loops and duplicate SDK logging. The guarded credential and connector retry classifier turn permanent errors into one Shape error while preserving transient exception types and retries. Duplicate permanent SDK messages are filtered only in the active authentication context. | `test_live_cloud_credentials.py`: old code retries; fixed code fails once, suppresses duplicates, and preserves transient failures. |
| 4: Warehouse keys | Shared T-SQL emitted inline keys for Warehouse and Synapse. `_tsql.create_table_sql` creates the table, then declares `PRIMARY KEY NONCLUSTERED ... NOT ENFORCED` in an ALTER statement. SQL Server/Azure SQL remain unchanged. | `test_owner_warehouse_ddl.py`: old DDL fails the improved Warehouse fake; new DDL passes. Warehouse scenario regenerated with the plugin recorder. |
| 5: staging cleanup | Storage already reused the upload filesystem. However, adlfs reintroduced an ambient account key beside explicit OneLake identity while constructing/reconnecting its client. `_adlfs.py` preserves only Shape's resolved storage keys, so upload and cleanup use the same selected identity/filesystem even with unrelated storage environment variables. | Failed COPY contract leaves no staged files and constructs one filesystem; old code emits the reproduced shared-key warning and fails. Stock adlfs construction reproduced the SDK error; the configured subclass constructed successfully. |
| 6a: hour buckets | The date parser interpreted the end of a dateless time range as a UTC offset. `dtparse.py` excludes time-range text from timestamp parsing. | Time-range profile fails before, passes after; 22 existing zoned-text compatibility tests pass. |
| 6b: database profile interruption | Optional relationship inference profiled all sampled columns together and propagated `ZonedTextError`. `shape_sqlserver/profiler.py` excludes only the offending text column from that inference and adds its column note, keeping catalog types, other columns and declared relationships. | Two `test_owner_profile_findings.py` cases fail before, pass after. |
| 7: ignored prefix | CLI/local Delta and Fabric sink adapters dropped `table_prefix`. Supported writers now receive/apply it; unsupported output modes refuse it before writing. | Prefix and refusal cases fail before, pass after. |
| 8: schema-enabled Lakehouse | Delta and Spark used the classic `Tables/<table>` layout; Demo manifests also assumed it. Explicit Delta `schema_enabled`, schema selection, Spark/Demo `--lakehouse-schema`, and the folder helper support `Tables/<schema>/<table>` with `dbo` as the Delta schema default. Classic layout remains available. | Improved Lakehouse fake rejects unrecognized layouts; old writes fail, new classic/dbo/custom-schema writes pass. Spark and Demo regressions fail before, pass after. |
| 9: absent Delta table | The reader exposed Delta's kernel log-segment error. It now reports `no Delta table at <path>` with URI credentials redacted. | Missing-table regression fails before, passes after. |

The combined old-product control retained the improved fakes: **28 failed, five unaffected
controls passed**. All 33 cases pass with the fixes. Separate old-Demo control fails with the
unsupported schema argument; the fixed Demo/Spark related suite passes 31 tests.

The plugin suite also exposed a concurrent mutation in the fake SQL connection snapshot.
Snapshot copying now freezes the dictionary keys before deep-copying its values. The new
deterministic mutation control fails before and passes after; the original Synapse test also
passes without assertion changes.

The CLI surface snapshot was regenerated using `python scripts/cli_surface.py --write`.
It adds only `--source-config` and the two `--lakehouse-schema` registrations; existing flags
and compatibility assertions remain unchanged. All 24 CLI surface tests pass.

## Fabric audit

| Surface | Finding and disposition |
|---|---|
| Warehouse, Synapse, warehouse-aware SQL database/SQL Server writers | Shared DDL helper fixed; SQL Server/Azure SQL keep their enforced inline keys. Warehouse fake rejects inline PRIMARY KEY/UNIQUE with 24584 and accepts only the supported ALTER constraint form. |
| Warehouse staging | Same cached filesystem is used for upload/removal; resolved identity protected from adlfs ambient-key fallback. Failed-load cleanup covered. |
| `shape publish` Warehouse | Uses the corrected shared writer; recorded Warehouse interaction regenerated through the scenario tool. |
| `shape publish` Lakehouse | Writes a Files landing zone, independent of table schemas. Existing remote Delta publication refusal is retained. |
| Lakehouse writer / scale router Files sink | Uses Files rather than managed Tables. Prefix now reaches the adapter; no schema-specific Files layout is needed. |
| Delta managed-table sink | Explicit classic/schema-enabled target selection, prefix and existing schema support; layouts exercised against the improved fake. |
| `onelake://` source | Already accepts both table layouts; regression coverage and documentation added. Missing Delta error corrected. |
| `profile-db` Warehouse / Fabric SQL database | Catalog types retained; time buckets and isolated sampled-column problems handled without discarding the database profile. |
| Spark worker, notebook and Demo seeding | Explicit schema flows into the worker and recorded Demo table paths. Classic job configuration bytes remain unchanged when no schema is selected. |
| Deploy/setup notebooks | Existing landing writes target Files; bundled Spark table writer now receives the optional schema configuration. |
| Core Fabric SQL script output | Already omits physical keys and emits informational key comments; no change needed. |
| Semantic-model Lakehouse M export | `shape_fabric/semantic_model.py::_lakehouse` retains its existing navigation template. It needs Owner verification of classic/schema-enabled navigation before changing the pinned semantic-model output; this is outside the table-writing fix. |

## Unattended live coverage

`test_live_owner_matrix.py` adds classic and schema-enabled Delta/Files, prefixes and OneLake
read-back; Warehouse COPY with/without keys and failed-load cleanup; SQL database write/profile;
sample Warehouse profiling; and Lakehouse/Warehouse publication. Existing modules retain
Eventhouse, Eventstream and deploy coverage. The docs list service-principal/resource variables,
permissions, noninteractive authentication and cleanup requirements.
The existing Synapse live test now forwards its documented service-principal variables instead
of creating empty SPN settings. Its old credential expression fails the new contract; the
fixed helper passes.

Missing-variable skips in live tests are explicitly authorized by add-on C and name the absent
variable. Service failures never skip or xfail. The final all-module absence exercise reports
31 skips and 22 non-live cases deselected;
that is not live-service acceptance. Existing non-live assertions have not been weakened.

The 0.9.1 limitations include both Warehouse inline-key and schema-enabled-layout workarounds.
Both say **a fix is in progress** until the lead merges the fix.

## Verification

The complete `make check` passes: 18,082 normal tests, 52 heavy tests (including the
unchanged ten-minute rate soak), 450 Python-kernel tests and 36 Rust tests. Coverage is
94.18% against the unchanged 86% gate. The normal stage retains 18 existing optional
Great Expectations/Pandera absence skips; no new non-live skip was introduced. Local
parallel scheduling isolates timing tests; no repository test or assertion is altered
by that scheduling. Rust formatting and clippy pass. The packaging metadata suite
also passes all 14 tests after repairing the local maturin executable path. Strict MkDocs build, wording, source
links and user-facing checks pass. All 36 new regression cases pass. The touched core suites pass **2,732 tests**;
the touched plugin suites pass **1,835 tests** with 96 live/emulator cases deselected.
The additional Fabric notebook/demo suite passes **266 tests** against the real Shape API,
using its declared UDF dependency in an isolated test overlay and one Spark session. No workflow is dispatched by this work package.
