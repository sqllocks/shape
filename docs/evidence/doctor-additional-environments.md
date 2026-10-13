# Doctor additional-environment receipts

Status: available.

Needs a Fabric account and Kafka or Event Hubs services. Not run in CI. Commands and output are historical evidence, not commands executed by the docs harness.

Private account identifiers, service usernames, workspace names and key-file paths are replaced with angle-bracket placeholders where present. Public GitHub run, job and ruleset IDs remain so you can trace the results. Recorded findings, dates, row counts and exit statuses are unchanged.

Owner-run evidence supplied by the Owner, dated **2026-10-11**, using Shape **0.9.1** from PyPI. The documentation editor did not execute these service commands. Statements and output below reproduce the supplied receipt, not a complete independently captured log. No credentials are recorded.

## Environment

Clean Windows **11** VM, Python **3.12.10**, Shape **0.9.1** from PyPI, **rust** kernel. Hosts and namespaces remain placeholders as supplied. These are selected checks and summaries supplied by the Owner, not reconstructed full stdout. Package versions and individual observations are recorded without inferring omitted client versions.

## Supplied results

- `shape doctor` -> Required OK numpy 2.5.4, pyarrow 26.0.0; optional lines (yaml, scipy, sklearn missing; cryptography, pandas, deltalake, openpyxl, tzdata OK); "Result: OK", exit 0.
- `shape doctor --fabric onelake://<workspace>/<lakehouse> --auth cli` -> PASS fabric.onelake (DNS onelake.dfs.fabric.microsoft.com), PASS TCP :443, PASS TLS (TLSv1.3), PASS fabric.auth "signed in with --auth cli", PASS fabric.item "workspace and item exist (<workspace>/<lakehouse>.Lakehouse)"; Result: OK, exit 0.
- `shape doctor --broker kafka://<host>:9092/shape_emit` -> PASS broker.dns, PASS broker.tcp, PASS broker.auth "the broker answered a metadata request"; Result: OK, exit 0.
- `shape doctor --broker eventhubs://<namespace>.servicebus.windows.net/shape-emit` -> PASS broker.dns, PASS broker.tcp :5671, PASS broker.tls (TLSv1.3), PASS broker.auth "signed in for <namespace>"; Result: OK, exit 0.
- `shape doctor --delta-table <missing local path>` -> WARN delta.limits "could not read the Delta table (Local path ... does not exist ...)", "Next: check the path and the sign-in", Result: OK, exit 0.

No local Delta table was created: the setup command had an operator error. There is no PASS receipt for a readable Delta table and no evidence of its feature/limits checks.


## Defect observations supplied by the Owner

These observations are recorded only as evidence, not as documentation fixes or claims that fixes have landed:

1. The check-name column runs into the message for long names, for example `fabric.onelakeonelake.dfs...`.
2. A `--delta-table` check that cannot read the table reports WARN with exit 0. The supplied receipt is not evidence that a Delta table was checked successfully.

These observations do not establish that a fix has landed.

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
