# Public B combined delivery

PR: https://github.com/sqllocks/shape/pull/48

This integration contains the previously reviewed implementations for issues #10, #17, #20, #6 and #14. Each issue retains its complete Wanted list and original tests-first evidence in its existing lane record. The integration changes no required CI check, comparison threshold, tolerance, assertion, recording or live-account requirement.

## Decision log

| Date | Authority and decision | Result |
|---|---|---|
| 2026-10-20 | Owner: CI is the full-suite gate; targeted local acceptance, independent diff review and prompt merges. | No local full suite is required or run for this integration. |
| 2026-10-25 | Owner: take the steps needed to speed delivery. Lead B implementation choice: consolidate the remaining reviewed lanes in one lead PR. | One final combined head receives every triggered CI job, preserving all issue acceptance and independent review. |

## Included work

| Issue | Original reviewed PR | Lane record |
|---|---|---|
| #10 W9-09 binary broker formats | https://github.com/sqllocks/shape/pull/34 | [W9-09](W9-09.md) |
| #17 W9-16 Iceberg | https://github.com/sqllocks/shape/pull/31 | [W9-16](W9-16.md) |
| #20 FRZ-01 frozen comparisons | https://github.com/sqllocks/shape/pull/42 | [FRZ-01](FRZ-01.md) |
| #6 W9-05 nested child profiling | https://github.com/sqllocks/shape/pull/43 | [W9-05](W9-05.md) |
| #14 W9-13 BigQuery | https://github.com/sqllocks/shape/pull/47 | [W9-13](W9-13.md) |

The latest merged main includes Snowflake and Databricks sources. Combined plugin discovery and doctor both exit 0 with 191 registrations, including every BigQuery, Iceberg, Snowflake and Databricks source and sink.

## Integration acceptance

- Broker conflict: preserve typed binary decoding and explicitly requested JSON nesting. Both discarded merge alternatives reproduce failures in the new six-case matrix (1 failed/5 passed and 2 failed/4 passed). Combined implementation: 6 passed in each kernel; existing targeted broker/child selection: 223 passed in each kernel.
- New cloud-source interface: 12 targeted failures in each kernel before forwarding validated depth; all 90 cloud-source tests pass in each kernel after the minimal fix. Unknown options and invalid depths still fail before connecting.
- BigQuery shared profiling: all 92 tests pass in each kernel, including default depth 8 and explicit 0/1/64 with preserved sampling, declared types and provenance. No product failure was claimed for the corrected test spy.
- Iceberg specialized depth forwarding: 4 failed and 2 default-depth cases passed in each kernel before the one-line fix; all 136 targeted cases passed in each kernel after the fix, zero deselections and four existing artifact warnings per run. Table and namespace snapshot IDs and summary row counts remain intact.
- BigQuery async job persistence: 9 failed and 4 controls passed in each kernel before the shared fragment scrub; all 40 new and existing Iceberg credential cases passed in each kernel afterward; the combined security selection including 30 existing query/adversarial controls passed 70 cases in each kernel. Rejected encoded, JSON, whitespace and multiline credential fragments never reach saved requests or open a connection. Allowed query options and existing URI redaction remain covered.
- Embedded BigQuery URI performance: a repeated-scheme input reproduced the three-second subprocess timeout in each kernel before repair; whole-token matching with a fragment callback passes the bounded control and preserves unchanged text.
- Windows staging privacy: hosted database tests isolated one POSIX-bit assumption (929 passed, 1 failed). Runtime now applies the existing current-user ACL helper to the staging directory and each empty file before writing. The fake verifies actual Windows ACLs; POSIX mode assertions and the original all-private assertion remain unchanged. Three new tests fail before repair and all 95 BigQuery tests pass in each local kernel afterward. Native Windows ACL validation remains required in CI.
- Database plugin conformance: 9 passed. Ruff and format checks pass; mypy reports no issues in 711 source files; public-wording check is clean.

Every test ran through the subreaper with locked uv. Independent integration review checks every original Wanted list, security and scope; it does not rerun the full suite. Every triggered hosted check on the final exact head must pass before merge. Pending real-account and emulator actions remain as recorded in each original lane; no live success is claimed.

## Frozen comparison reporting and closure

The following remain NOT COMPARABLE, never passes and never timing credit:

| Workload | Authenticated recorded 0.9.1 result |
|---|---|
| `profile:edge/x_pq_list.parquet` | TypeError on list-valued Parquet; no recorded profile values. |
| `profile:edge/x_pq_nested.parquet` | TypeError on struct-valued Parquet; no recorded profile values. |

All workloads with recorded values retain their original comparisons and thresholds. This PR references #20 without closing it. After merge, dispatch Nightly on main with no recording confirmation; all five parity shards, parity-publish and sqlserver-e2e must pass. Link that qualifying run and close #20 only through a subsequent acceptance PR.
