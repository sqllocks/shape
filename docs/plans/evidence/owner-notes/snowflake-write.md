# Snowflake write receipt

Owner-run receipt supplied by the Owner on **2026-10-11**. The documentation editor did not independently execute this run or query the service. Commands, output and persisted row counts below record the Owner’s supplied evidence; install commands are supplied setup details, not complete pip logs. Private-key contents and token values are not recorded.

Resolves `docs/databases/snowflake.md:27` in OWNER_NOTES_TRIAGE.md: the test-account write transcript note.

## Environment and installation

- Clean Windows 11 VM; Python **3.12.10**.
- Shape core, domains and databases plugin **0.9.1**, installed from PyPI.
- `snowflake-connector-python` **4.8.0**.

```text
pip install "sqllocks-shape[domains]==0.9.1" "sqllocks-shape-databases[snowflake]==0.9.1"
```

## Target and authentication

- Account `LQYKDPS-GI25871`, Azure Central US, Standard trial.
- Service user `SHAPE_SVC`, `TYPE=SERVICE`; key-pair sign-in.
- Role `SHAPE_LOADER`; warehouse `SHAPE_WH`, X-Small.
- Database `SHAPE_TEST`; schema `LIVE`.
- Private key reference: `file://C:/Users/shape/.snowflake/rsa_key.p8`, passed as `--sink-config snowflake.private_key=...`; no key contents included.
- `SHAPE_CONFIRM_REMOTE=1` was set; the command also supplied `--yes`.

## Captured replace run

The Owner places the run at approximately **6:50 PM Eastern on 2026-10-11**. The VM clock was wrong for this run; no VM timestamp is presented as the run’s actual time. A prior `--write-mode create` run also exited 0, according to the Owner; the replace run is the captured receipt below.

Command (with the separately supplied key reference expanded):

```text
shape generate retail --scale small --seed 42 --to "snowflake://SHAPE_SVC@LQYKDPS-GI25871/SHAPE_TEST/LIVE?warehouse=SHAPE_WH&role=SHAPE_LOADER" --write-mode replace --yes --sink-config "snowflake.private_key=file://C:/Users/shape/.snowflake/rsa_key.p8"
```

Supplied output:

```text
shape 0.9.1
Wrote 21,750 rows in 9 tables to snowflake://SHAPE_SVC@LQYKDPS-GI25871/SHAPE_TEST/LIVE?warehouse=SHAPE_WH&role=SHAPE_LOADER (19.51s)
```

Exit code: **0**. The elapsed time is one run’s output, not a benchmark or throughput claim.

## Persisted-row verification

The Owner verified these counts in Snowsight as `SHAPE_LOADER`, using `SHAPE_TEST.INFORMATION_SCHEMA.TABLES` for schema `LIVE`. Table names were lower case and quoted. These are the supplied metadata counts, not a fabricated `COUNT(*)` transcript.

| Table | Persisted rows |
|---|---:|
| `address` | 1,500 |
| `customer` | 1,000 |
| `order` | 5,000 |
| `order_line` | 12,500 |
| `product` | 500 |
| `product_category` | 50 |
| `promotion` | 200 |
| `return` | 850 |
| `store` | 150 |
| **Total** | **21,750** |

## Other unresolved notes

This receipt does not execute the ADLS/OneLake, SQL Server/PostgreSQL or `shape seed` examples requested by `docs/SINKS.md:66`, `docs/SINKS.md:151` or `docs/TESTING_WITH_SHAPE.md:94`. Those notes remain open. It is a write receipt and supplies no Snowflake profiling evidence.
