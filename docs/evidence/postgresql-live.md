# PostgreSQL 17 (local Docker, reached from the VM over the Hyper-V network) — Owner-run receipt

Status: available.

Needs a PostgreSQL server. Not run in CI. Commands and output are historical evidence, not commands executed by the docs harness.

Private account identifiers, service usernames, workspace names and key-file paths are replaced with angle-bracket placeholders where present. Public GitHub run, job and ruleset IDs remain so you can trace the results. Recorded findings, dates, row counts and exit statuses are unchanged.

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below preserves the supplied command, counts and outcome, including placeholders and abbreviated output. The count line is labelled as an Owner-reported observation. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
1. PostgreSQL 17 (local Docker, reached from the VM over the Hyper-V network):
   SHAPE_POSTGRES_PASSWORD set; SHAPE_CONFIRM_REMOTE=1
   shape generate retail --scale small --seed 42 --to "postgresql://postgres@<host>:5433/shape_test?sslmode=disable" --write-mode create --yes
   -> "Wrote 21,750 rows in 9 tables to postgresql://postgres@<host>:5433/shape_test?sslmode=disable (4.04s)", exit 0.
   Owner-reported persisted counts from psql (pg_stat_user_tables): address 1500, customer 1000, order 5000, order_line 12500, product 500, product_category 50, promotion 200, return 850, store 150.
```


## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
