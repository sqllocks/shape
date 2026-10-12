# SQL Server 2022 (local Docker) — Owner-run receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below is reproduced as supplied, including placeholders and abbreviated output. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
3. SQL Server 2022 (local Docker):
   Write: SHAPE_MSSQL_PASSWORD set
   shape generate retail --scale small --seed 42 --to "mssql://sa@<host>:1433/shape_test?schema=dbo" --write-mode create --yes --sink-config "sqlserver.password=env://SHAPE_MSSQL_PASSWORD" --sink-config "sqlserver.trust_server_certificate=true"
   -> "Wrote 21,750 rows in 9 tables to mssql://sa@<host>:1433/shape_test?schema=dbo (5.68s)", exit 0. Verified via sys.partitions: same nine counts.
   Read back: SHAPE_SQLSERVER_CONNECTION_STRING set (ODBC Driver 18, SQL login, Encrypt=yes, TrustServerCertificate=yes)
   shape profile-db --auth sql --database shape_test --schema dbo --sample-rows 1000 -o shape_test_mssql.shape
   -> {"written": "...shape_test_mssql.shape", "shape_content_id": "675387f1e60ec56f1d2c342c222058021b5aee6d3ffddbad55dfbff8f8f5436f", "tables": 9, "relationships": 8}, exit 0.
```

## Note disposition

Evidence exists, not an exact match for a remaining owner note. No note is resolved by this receipt; see the [evidence index](remaining-live-receipts.md) for the command and service mismatches.
