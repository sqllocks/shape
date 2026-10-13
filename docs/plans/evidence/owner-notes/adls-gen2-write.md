# ADLS Gen2 write only (account key reference) — Owner-run receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below is reproduced as supplied, including placeholders and abbreviated output. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
6. ADLS Gen2 write only (account key reference):
   shape generate retail --scale small --seed 7 --to "abfss://shape-test@<account>.dfs.core.windows.net/real" --format parquet --batch-date 2026-10-11 --manifest --sink-config "abfss.account_key=env://AZURE_STORAGE_ACCOUNT_KEY" --yes
   -> "Wrote 21,750 rows in 9 tables to abfss://shape-test@<account>.dfs.core.windows.net/real (7.96s)", exit 0.
   Reading it back failed (the CLI read path has no key/SAS option and the Entra fallback hit a tenant mismatch) — defect fix/live-findings #2/#3. Do not resolve any ADLS read note.
```

## Note disposition

Evidence exists, not an exact match for a remaining owner note. No note is resolved by this receipt; see the [evidence index](remaining-live-receipts.md) for the command and service mismatches.
