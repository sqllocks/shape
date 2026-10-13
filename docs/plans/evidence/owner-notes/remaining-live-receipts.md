# Remaining Owner-run live receipts — 2026-10-11

Shape **0.9.1** from PyPI; clean Windows 11 VM; Python **3.12.10**; VM clock synced. These are Owner-run receipts, not runs independently executed by the documentation editor. Exact service commands, supplied output, authentication references and persisted checks are retained in the linked files. Secrets are not recorded. Individual elapsed times are not benchmark measurements.

## Receipts

| Service | Evidence | Disposition |
|---|---|---|
| PostgreSQL 17, local Docker over Hyper-V | [Generate/write](postgresql-live.md) | Evidence exists, not an exact match. |
| MySQL 8.4, local Docker, server CA TLS | [Generate/write](mysql-live.md) | Evidence exists, not an exact match. |
| SQL Server 2022, local Docker | [Generate/write and profile-db](sqlserver-live.md) | Evidence exists, not an exact match. |
| Kafka, local apache/kafka, PLAINTEXT | [Emit and bounded stream-profile](kafka-live.md) | Evidence exists, not an exact match. |
| Azure Event Hubs, Basic | [Emit and bounded stream-profile](eventhubs-live.md) | Evidence exists, not an exact match. |
| ADLS Gen2, account-key reference | [Parquet generate/write only](adls-gen2-write.md) | Evidence exists, not an exact match; no read note resolved. |
| Fabric Eventhouse, trial capacity | [Emit and KQL count](fabric-eventhouse-live.md) | Evidence exists, not an exact match. |
| Fabric Eventstream, Custom endpoint | [Emit and data preview](fabric-eventstream-live.md) | Evidence exists; no remaining note requests this exact command. |

## Exact-match review

Locations refer to the retained baseline in OWNER_NOTES_TRIAGE.md. Every listed note stays unchanged. No owner note is resolved in this task: **31 remain (B=25, C=3, D=3)**.

| Note | Disposition and missing exact evidence |
|---|---|
| docs/databases/postgresql.md:46 | Evidence exists, not an exact match: generate succeeded, but no `\copy` export or `shape profile orders.csv` receipt; supplied scale is small rather than the example's tiny. |
| docs/databases/mysql.md:55 | Evidence exists, not an exact match: TLS generate succeeded, but no `SELECT ... INTO OUTFILE`, CSV header/copy or `shape profile orders.csv` receipt; replace/small differs from create/tiny. |
| docs/SINKS.md:151 | Evidence exists, not an exact match: SQL Server generate receipt exists, but the example also requires PostgreSQL `shape stream ... --commit-rows 500 --realtime --rate 100`, not PostgreSQL generate. |
| docs/TESTING_WITH_SHAPE.md:94 | Evidence exists, not an exact match: supplied database commands are generate/profile-db, not the four `shape seed` commands. |
| docs/plugins/sqlserver.md:50 | Evidence exists, not an exact match: SQL Server profile-db uses `--auth sql` and the environment connection string; the example requests `--server ...` and `--json shop-summary.json` on an Azure SQL/Fabric endpoint. Neither that connection path nor the summary file is recorded. |
| docs/plugins/fabric-auth.md:41 | Evidence exists, not an exact match: SQL Server password-based write does not exercise Kerberos, keytab or principal. |
| docs/plugins/streaming.md:58 | Evidence exists, not an exact match: both bounded reads succeeded, but Event Hubs tumbling windows, lateness, `--windows`, checkpoint and `--follow` were not run. |
| docs/EMIT.md:281 | Evidence exists, not an exact match: JSON Kafka emit is not Avro order_line emit with authenticated Schema Registry. |
| docs/EMIT.md:370 | Evidence exists, not an exact match: Kafka emit did not run the drift plan, row overrides, realtime/day-seconds or answer-key example. |
| docs/SINKS.md:66 | Evidence exists, not an exact match: ADLS Parquet/manifest generate does not run the table-format CSV override, rolling realtime stream or complete OneLake Delta example. Fabric Lakehouse defects below do not supply a successful receipt. |
| docs/plugins/fabric-commands.md:59 | Evidence exists, not an exact match: Eventhouse `shape emit` is not `shape publish`; no complete four-target publish example is recorded. |
| docs/CLI.md:369 and docs/TROUBLESHOOTING.md:18 | Evidence exists, not an exact match: service writes/reads are not the requested `shape doctor` broker, Fabric and Delta commands. |

All other outstanding service, Owner-decision and machine-labelled-performance notes also remain open. No public status or limitation statement is changed. In particular, no Fabric Warehouse or Lakehouse writing claim is added and no ADLS reading claim is added.

## Defects reported by the Owner — no resolutions

The supplied failure descriptions are recorded below, not independently reproduced. Fixes are being developed on `fix/live-findings`; this documentation branch changes no product code and asserts no fix has landed.

- **#1, MySQL:** the otherwise same command with `?ssl=false` failed against MySQL 8.4 default `caching_sha2_password`: `could not connect ... (AttributeError): 'NoneType' object has no attribute 'is_auth_switch_request'`. The successful receipt uses the server CA and VERIFY_CA; it does not establish that ssl=false works.
- **#2/#3, ADLS read:** read-back failed because the CLI path has no key/SAS option and the Entra fallback encountered a tenant mismatch. Only the write receipt is recorded.

Supplied Fabric defect block (#4–#9; the Owner did not supply a per-defect number mapping or complete commands/logs):

```text
9. Defects found tonight (record in the evidence index, resolve nothing for them): Fabric Lakehouse Delta write succeeded (21,750 rows, 9 tables, 9.96s) but ignored --table-prefix and wrote Tables/<table> into a schema-enabled Lakehouse (shown as schemas with "Unidentified" items); reading it back failed with "No files in log segment"; the Fabric Warehouse load failed on PRIMARY KEY in CREATE TABLE; staging cleanup failed under --auth cli; profile-db on Fabric's sample warehouse failed on HourlyBucket '18:00-18:59' (ZonedTextError). All on fix/live-findings (#4-#9).
```

These findings do not resolve any owner note and do not establish supported Fabric Warehouse or Lakehouse write behaviour.

## Follow-up export and doctor receipts

The later Owner-supplied receipts complete the [PostgreSQL export/profile](postgresql-export-profile.md) and [MySQL export/profile](mysql-export-profile.md) requests, together with their earlier writes. Both database owner notes are now resolved. The [doctor receipts](doctor-additional-environments.md) resolve the troubleshooting note's request for additional environments; CLI example 5 remains open because the Delta target was missing and only WARN was recorded. The preceding inventory and exact-match table describe the earlier evidence-only commit, before these new receipts. Current count: **28 unresolved (B=22, C=3, D=3)**.
