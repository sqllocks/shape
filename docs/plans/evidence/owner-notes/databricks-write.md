# Databricks write receipt

Owner-run receipt supplied by the Owner on **2026-10-11**. The documentation editor did not independently execute this run or query the service. Commands, output and persisted row counts below record the Owner’s supplied evidence; install commands are supplied setup details, not complete pip logs. Private-key contents and token values are not recorded.

Resolves `docs/databases/databricks.md:27` in OWNER_NOTES_TRIAGE.md: the test-account write transcript note.

## Environment and installation

- Same clean Windows 11 VM; Python **3.12.10**.
- Shape **0.9.1** and domains installed from PyPI as for the Snowflake receipt.
- Databricks extra additionally installed with:

```text
pip install "sqllocks-shape-databases[databricks]==0.9.1"
```

No Databricks SQL connector version was supplied, so none is inferred.

## Target and authentication

- Databricks Free Edition.
- Workspace host `dbc-5c97b9f8-44ba.cloud.databricks.com`.
- Serverless SQL warehouse `Serverless Starter`, size **2X-Small**, ID `267f66ef676c5259`.
- Catalog `workspace`; schema `shape_test`.
- Personal access token in `DATABRICKS_TOKEN`, never in the URI; token value not recorded.

## Captured create run

Command:

```text
shape generate retail --scale small --seed 42 --to "databricks://dbc-5c97b9f8-44ba.cloud.databricks.com/sql/1.0/warehouses/267f66ef676c5259?catalog=workspace&schema=shape_test" --write-mode create --yes
```

Supplied output (clock string reproduced as supplied):

```text
Sunday, October 11, 2026 6:55:25 PM
shape 0.9.1
Wrote 21,750 rows in 9 tables to databricks://dbc-5c97b9f8-44ba.cloud.databricks.com/sql/1.0/warehouses/267f66ef676c5259?catalog=workspace&schema=shape_test (108.30s)
```

Exit code: **0**. The elapsed time is one run’s output, not a benchmark or throughput claim.

## Persisted-row verification

The Owner verified `COUNT(*)` for each table in the Databricks SQL Editor, catalog `workspace`, schema `shape_test`:

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

This receipt does not execute the ADLS/OneLake, SQL Server/PostgreSQL or `shape seed` examples requested by `docs/SINKS.md:66`, `docs/SINKS.md:151` or `docs/TESTING_WITH_SHAPE.md:94`. Those notes remain open. It is a write receipt and supplies no Databricks profiling evidence.
