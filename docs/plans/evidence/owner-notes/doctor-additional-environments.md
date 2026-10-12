# Doctor additional-environment receipts

Owner-run evidence supplied by the Owner, dated **2026-10-11**, using Shape **0.9.1** from PyPI. The documentation editor did not execute these service commands. Statements and output below reproduce the supplied receipt, not a complete independently captured log. No credentials are recorded.

## Environment

Clean Windows **11** VM, Python **3.12.10**, Shape **0.9.1** from PyPI, **rust** kernel. Hosts and namespaces remain placeholders as supplied. These are selected checks and summaries supplied by the Owner, not reconstructed full stdout. Package versions and individual observations are recorded without inferring omitted client versions.

## Supplied results

- `shape doctor` -> Required OK numpy 2.5.4, pyarrow 26.0.0; optional lines (yaml, scipy, sklearn missing; cryptography, pandas, deltalake, openpyxl, tzdata OK); "Result: OK", exit 0.
- `shape doctor --fabric onelake://shape-test/shape_lh --auth cli` -> PASS fabric.onelake (DNS onelake.dfs.fabric.microsoft.com), PASS TCP :443, PASS TLS (TLSv1.3), PASS fabric.auth "signed in with --auth cli", PASS fabric.item "workspace and item exist (shape-test/shape_lh.Lakehouse)"; Result: OK, exit 0.
- `shape doctor --broker kafka://<host>:9092/shape_emit` -> PASS broker.dns, PASS broker.tcp, PASS broker.auth "the broker answered a metadata request"; Result: OK, exit 0.
- `shape doctor --broker eventhubs://<namespace>.servicebus.windows.net/shape-emit` -> PASS broker.dns, PASS broker.tcp :5671, PASS broker.tls (TLSv1.3), PASS broker.auth "signed in for <namespace>"; Result: OK, exit 0.
- `shape doctor --delta-table <missing local path>` -> WARN delta.limits "could not read the Delta table (Local path ... does not exist ...)", "Next: check the path and the sign-in", Result: OK, exit 0.

No local Delta table was created: the setup command had an operator error. There is no PASS receipt for a readable Delta table and no evidence of its feature/limits checks.

## Note dispositions

- **Resolved:** `docs/TROUBLESHOOTING.md:18` (baseline): asks for doctor transcripts for additional environments. The Owner-run Fabric/OneLake, Kafka and Event Hubs connectivity checks provide those additional environments. This note does not specify a successful Delta command or the entirety of CLI example 5; the stricter four-command requirement in the historical triage was a proposed artifact, not wording in this note.
- **Evidence exists, not an exact match:** `docs/CLI.md:369`, example 5. Fabric and broker checks passed, but the `--delta-table` command checked a missing path and only warned. That note remains unchanged until a readable Delta table has a PASS receipt.

## Defect observations supplied by the Owner

These observations are recorded only as evidence, not as documentation fixes or claims that fixes have landed:

1. The check-name column runs into the message for long names, for example `fabric.onelakeonelake.dfs...`.
2. A `--delta-table` check that cannot read the table reports WARN with exit 0. The supplied receipt is not evidence that a Delta table was checked successfully.

Neither observation resolves any defect or the CLI owner note. No status or limitation statement is changed.
