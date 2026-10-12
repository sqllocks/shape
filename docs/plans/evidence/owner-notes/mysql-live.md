# MySQL 8.4 (local Docker), over TLS with the server's own CA — Owner-run receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below is reproduced as supplied, including placeholders and abbreviated output. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
2. MySQL 8.4 (local Docker), over TLS with the server's own CA:
   SHAPE_MYSQL_PASSWORD set
   shape generate retail --scale small --seed 42 --to "mysql://root@<host>:3306/shape_test?ssl_ca=<path>/mysql-ca.pem&ssl-mode=VERIFY_CA" --write-mode replace --yes
   -> "Wrote 21,750 rows in 9 tables to mysql://root@<host>:3306/shape_test?ssl_ca=...&ssl-mode=VERIFY_CA (0.98s)", exit 0.
   Verified: order 5000, order_line 12500, customer 1000.
   Note: the same command with ?ssl=false failed against MySQL 8.4's default caching_sha2_password with "could not connect ... (AttributeError): 'NoneType' object has no attribute 'is_auth_switch_request'" — a defect being fixed (fix/live-findings #1). Do not document ssl=false as working for MySQL 8.4 until that lands.
```

## Note disposition

Evidence exists, not an exact match for a remaining owner note. No note is resolved by this receipt; see the [evidence index](remaining-live-receipts.md) for the command and service mismatches.
