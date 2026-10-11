# Live test playbook: Amazon Redshift

Shape 0.9.1 · **no Redshift plugin at 0.9.1** · checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Redshift.** Nothing directly: `shape profile redshift://...` exits 2
("no installed source plugin reads ...") and `shape generate --to redshift://...` exits 2 ("no
sink writes redshift:// targets"). W9-14 (issue #15, lane `lane/W9-14`) builds the sink and
source and is `todo`, blocked on the `COPY` parameter policy. Do not point the `postgres` sink
at Redshift: it loads with `COPY ... FROM STDIN`, which Redshift does not implement (Redshift's
`COPY` reads from S3, DynamoDB, EMR or SSH only). This playbook tests Shape's core on Redshift
data through `UNLOAD`, `COPY` and the AWS CLI.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Account | An AWS account; Redshift Serverless (a free trial credit is offered to new accounts). |
| Compute | A Redshift Serverless workgroup at the minimum base capacity (8 RPU), or a single-node `ra3.large` provisioned cluster you pause afterwards. |
| Permissions | A database user with `CREATE` on the test schema; an IAM role attached to the workgroup with `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on the test bucket (`REDSHIFT_IAM_ROLE_ARN`). |
| Real table to profile | The `tickit` sample (`sample_data_dev.tickit.sales`, loaded by the console's sample-data option; about 170 k rows, 10 columns). |
| Tools | AWS CLI signed in (`aws sts get-caller-identity`), the Redshift query editor v2 or `psql`; Python 3.11 to 3.14. |

Create the dedicated schema and bucket:

```sql
CREATE SCHEMA IF NOT EXISTS shape_test;
```

```bash
aws s3 mb "s3://$S3_BUCKET"
```

## 2. Credentials

Shape reads no Redshift credential at 0.9.1. The AWS CLI uses its profile
(`AWS_PROFILE`, or `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`); `psql` reads `PGPASSWORD`.
The IAM role ARN is not a secret and goes in the SQL below as `REDSHIFT_IAM_ROLE_ARN`.

## 3. Install

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1"
```

No plugin to install (`README.md`, "Install", explains `$SHAPE_WHEELS`).

## 4. Test steps

Work in an empty directory.

1. **Preflight.**
   ```bash
   shape doctor
   aws s3 ls "s3://$S3_BUCKET"
   ```
   Success: `Result: OK` (exit 0); the bucket lists (empty).

2. **Unload the real table to Parquet** (query editor, as the test user):
   ```sql
   UNLOAD ('SELECT * FROM sample_data_dev.tickit.sales')
     TO 's3://<S3_BUCKET>/shape_test/sales/'
     IAM_ROLE '<REDSHIFT_IAM_ROLE_ARN>' FORMAT AS PARQUET ALLOWOVERWRITE;
   SELECT COUNT(*) FROM sample_data_dev.tickit.sales;
   ```
   ```bash
   mkdir -p sales_parquet && aws s3 cp --recursive "s3://$S3_BUCKET/shape_test/sales/" sales_parquet/
   ```
   Success: `sales_parquet/` holds `.parquet` files; write the `COUNT(*)` in the results table.

3. **Profile the real table.**
   ```bash
   shape profile ./sales_parquet --name sales -o sales.shape --json sales-summary.json
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "sales.shape"}`;
   `shape inspect sales.shape` shows the row count of step 2 and 10 columns.

4. **Generate from the profile and load it back.**
   ```bash
   shape generate --from sales.shape --rows 50000 -o generated --format parquet
   aws s3 cp generated/sales.parquet "s3://$S3_BUCKET/shape_test/generated/sales.parquet"
   ```
   ```sql
   CREATE TABLE shape_test.sales_generated (LIKE sample_data_dev.tickit.sales);
   COPY shape_test.sales_generated FROM 's3://<S3_BUCKET>/shape_test/generated/sales.parquet'
     IAM_ROLE '<REDSHIFT_IAM_ROLE_ARN>' FORMAT AS PARQUET;
   SELECT COUNT(*) FROM shape_test.sales_generated;
   ```
   Success: `Wrote 1 parquet files to generated: 50,000 rows in 1 tables`; `COPY` loads 50000
   rows (`SELECT COUNT(*)` returns 50000). A `COPY` type error means the generated Parquet type
   differs from the table's (for example a `BIGINT` where the table has `SMALLINT`): record the
   column; W9-03 (issue #4) covers type fidelity on write.

5. **Contract check.**
   ```bash
   shape proposals propose sales.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o sales.contract.json
   shape check sales.shape sales.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   ```bash
   shape profile generated/sales.parquet --capture full -o sales-generated.shape
   shape check sales-generated.shape sales.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules listed (record them).
   `shape contract emit sales.contract.json --to ddl --dialect postgres -o sales.sql` gives DDL
   close to Redshift's; there is no Redshift dialect.

6. **Drift check.**
   ```bash
   shape profile ./sales_parquet --name sales -o sales-again.shape
   shape diff sales.shape sales-again.shape --fail-on-drift
   ```
   Success: exit 0 and `bump: none (0 breaking, 0 additive, 0 cosmetic)`.
   ```bash
   shape diff sales.shape sales-generated.shape --fail-on breaking --json drift.json
   shape drift ./sales_parquet generated --psi --json -o drift-psi.json
   ```
   Success for `diff`: exit 0 and `0 breaking`. `drift --psi` is expected to exit 1 on generated
   data; record the drifted columns.

7. **Emit.** Not applicable (no Redshift or Kinesis emitter in 0.9.1).

## 5. Cleanup and cost

```sql
DROP TABLE IF EXISTS shape_test.sales_generated;
DROP SCHEMA IF EXISTS shape_test CASCADE;
```

```bash
aws s3 rm --recursive "s3://$S3_BUCKET/shape_test/"
aws s3 rb "s3://$S3_BUCKET"
```

Then **delete the serverless workgroup and namespace** (or pause the provisioned cluster:
`aws redshift pause-cluster --cluster-identifier ...`). Locally:
`rm -rf sales_parquet generated *.shape *.json`.

**Cost.** Redshift Serverless bills RPU-hours per second only while queries run, with a
60-second minimum per query burst; this playbook is a few minutes at 8 RPU, a few dollars at
list price. A provisioned `ra3.large` node bills per hour until paused or deleted. S3 storage
for the unloaded files is cents. Check in the console that the workgroup shows no running
queries and that the cluster is **Paused** or deleted.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor`, `aws s3 ls` | | | |
| 2 | `UNLOAD` + `aws s3 cp` | | | rows: |
| 3 | `shape profile` | | | content id: |
| 4 | `shape generate` + `COPY` | | | `COUNT(*)`: |
| 5a | `shape check` (real profile) | | | |
| 5b | `shape check` (generated, full capture) | | | rules: |
| 6a | `shape diff` same data | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 6c | `shape drift --psi` | | | drifted columns: |
| 7 | emit | n/a | n/a | |

Tester, date, Shape version, workgroup capacity: ______

## Gaps found while writing this playbook

- No `redshift://` source or sink at 0.9.1 (W9-14, issue #15; the lane is blocked on the
  `COPY` parameter policy question). Every Redshift step above is SQL or the AWS CLI.
- The `postgres` sink cannot stand in: Redshift has no `COPY FROM STDIN`.
- No Redshift DDL dialect in `shape contract emit --to ddl`.
