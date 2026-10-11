# Live test playbook: BigQuery

Shape 0.9.1 · **no BigQuery plugin at 0.9.1** · checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on BigQuery.** Nothing directly: `shape profile bigquery://...` exits 2
("no installed source plugin reads ...") and `shape generate --to bigquery://...` exits 2 ("no
sink writes bigquery:// targets"). W9-13 (issue #14, lane `lane/W9-13`) builds the sink and
source and is `todo`, blocked on a decimal-mapping question. This playbook therefore tests
Shape's core on BigQuery data through the `bq` and `gsutil` tools: export to Parquet, profile,
generate, load back. Every Shape command below is a released 0.9.1 command; the BigQuery steps
are Google's tools, not Shape's.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Account | A Google Cloud project with billing, or the BigQuery sandbox (no billing; loads and exports still work, with the sandbox's 10 GB storage limit). |
| Compute | None to provision: on-demand queries are billed per byte scanned; the tables here are a few hundred megabytes. |
| Permissions | `roles/bigquery.dataEditor` on the test dataset, `roles/bigquery.jobUser` on the project, `roles/storage.objectAdmin` on the export bucket. |
| Real table to profile | `bigquery-public-data.thelook_ecommerce.orders` (public, about 125 k rows, 9 columns). |
| Tools | `gcloud`, `bq` and `gsutil` signed in (`gcloud auth login`, `gcloud config set project $GCP_PROJECT`); Python 3.11 to 3.14. |

Create the dedicated dataset and bucket:

```bash
bq mk --dataset --location=US "$GCP_PROJECT:shape_test"
gsutil mb -l US "gs://$GCS_BUCKET"
```

## 2. Credentials

Shape reads no BigQuery credential at 0.9.1 (there is nothing to connect to). The Google tools
use the `gcloud` session or `GOOGLE_APPLICATION_CREDENTIALS` (a service-account key file path).
Never commit that file; keep it mode 600.

## 3. Install

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1"
```

No plugin to install. (`README.md`, "Install", explains `$SHAPE_WHEELS`.)

## 4. Test steps

Work in an empty directory.

1. **Preflight.**
   ```bash
   shape doctor
   bq query --use_legacy_sql=false 'SELECT 1'
   ```
   Success: `Result: OK` (exit 0), and `bq` prints one row.

2. **Export the real table to Parquet.** A public table cannot be extracted directly; copy a
   sample into the test dataset first, then extract:
   ```bash
   bq query --use_legacy_sql=false --destination_table "$GCP_PROJECT:shape_test.orders_sample" \
       --replace 'SELECT * FROM `bigquery-public-data.thelook_ecommerce.orders`'
   bq extract --destination_format=PARQUET "$GCP_PROJECT:shape_test.orders_sample" \
       "gs://$GCS_BUCKET/shape_test/orders/*.parquet"
   mkdir -p orders_parquet && gsutil -m cp "gs://$GCS_BUCKET/shape_test/orders/*.parquet" orders_parquet/
   ```
   Success: `orders_parquet/` holds `.parquet` files; `bq show shape_test.orders_sample`
   reports the row count (write it in the results table).

3. **Profile the real table.**
   ```bash
   shape profile ./orders_parquet --name orders -o orders.shape --json orders-summary.json
   ```
   Success: exit 0 and `{"shape_content_id": "...", "written": "orders.shape"}`;
   `shape inspect orders.shape` shows the same row count as `bq show`.

4. **Generate from the profile and load it back.**
   ```bash
   shape generate --from orders.shape --rows 50000 -o generated --format parquet
   gsutil cp generated/orders.parquet "gs://$GCS_BUCKET/shape_test/generated/orders.parquet"
   bq load --source_format=PARQUET --replace "$GCP_PROJECT:shape_test.orders_generated" \
       "gs://$GCS_BUCKET/shape_test/generated/orders.parquet"
   bq query --use_legacy_sql=false 'SELECT COUNT(*) AS n FROM `shape_test.orders_generated`'
   ```
   Success: `Wrote 1 parquet files to generated: 50,000 rows in 1 tables`, the load job
   succeeds, and the count is 50000. (The `_shape_provenance.json` next to the Parquet file is
   not loaded.)

5. **Contract check.**
   ```bash
   shape proposals propose orders.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o orders.contract.json
   shape check orders.shape orders.contract.json
   ```
   Success: exit 0 and `{"passed": true, "violations": []}`.
   ```bash
   shape profile generated/orders.parquet --capture full -o orders-generated.shape
   shape check orders-generated.shape orders.contract.json
   ```
   Expected: exit 0, or exit 1 with the violated rules listed (record them).
   `shape contract emit orders.contract.json --to ddl` has no BigQuery dialect (`tsql`,
   `tsql-fabric-warehouse`, `postgres`, `mysql` only); use `--to jsonschema --table orders`.

6. **Drift check.**
   ```bash
   shape profile ./orders_parquet --name orders -o orders-again.shape
   shape diff orders.shape orders-again.shape --fail-on-drift
   ```
   Success: exit 0 and `bump: none (0 breaking, 0 additive, 0 cosmetic)`.
   ```bash
   shape diff orders.shape orders-generated.shape --fail-on breaking --json drift.json
   shape drift ./orders_parquet generated --psi --json -o drift-psi.json
   ```
   Success for `diff`: exit 0 and `0 breaking`. `drift --psi` is expected to exit 1 on generated
   data; record the drifted columns.

7. **Emit.** Not applicable (no BigQuery or Pub/Sub emitter in 0.9.1).

## 5. Cleanup and cost

```bash
bq rm -r -f -d "$GCP_PROJECT:shape_test"
gsutil -m rm -r "gs://$GCS_BUCKET/shape_test"
gsutil rb "gs://$GCS_BUCKET"
```

Locally: `rm -rf orders_parquet generated *.shape *.json`. If you created a service-account key,
delete it (`gcloud iam service-accounts keys delete`).

**Cost.** The query in step 2 scans a few hundred megabytes (within the free 1 TB per month of
on-demand queries); storage and the extract and load jobs are free or cents. Nothing keeps
running: BigQuery has no compute to stop, and the bucket is removed above.

## 6. Results

| Step | Command | Pass | Fail | Notes (rows, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor`, `bq query` | | | |
| 2 | `bq query` + `bq extract` + `gsutil cp` | | | rows: |
| 3 | `shape profile` | | | content id: |
| 4 | `shape generate` + `bq load` | | | `COUNT(*)`: |
| 5a | `shape check` (real profile) | | | |
| 5b | `shape check` (generated, full capture) | | | rules: |
| 6a | `shape diff` same data | | | |
| 6b | `shape diff` real vs generated `--fail-on breaking` | | | bump: |
| 6c | `shape drift --psi` | | | drifted columns: |
| 7 | emit | n/a | n/a | |

Tester, date, Shape version: ______

## Gaps found while writing this playbook

- No `bigquery://` source or sink at 0.9.1 (W9-13, issue #14; the lane is blocked on the
  decimal mapping of Wanted item 3). Every BigQuery step above is `bq`/`gsutil`, not Shape.
- No BigQuery DDL dialect in `shape contract emit --to ddl`.
