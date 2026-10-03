# Integrations plugin

`sqllocks-shape-integrations` is one first-party plugin with a thin adapter for each tool a team
may already use. Its version equals core's and it is released with core. It is built only on
[plugin API v1](api-v1.md) and the run manifest, and core never depends on any of the tools:
each one is an **extra** you install on its own.

| Integration | Extra | Entry point | What it does |
|---|---|---|---|
| OpenLineage | `openlineage` | `shape lineage emit` | run manifest to lineage events |
| MLflow | `mlflow` | `shape mlflow log` | run manifest, profile and verify report to a tracking run |
| Presidio | `presidio` | detector `presidio` | PII labels for text columns |
| SDMetrics | `sdmetrics` | `shape evaluate sdmetrics` | quality report of synthetic against real |
| Anonymeter | `anonymeter` | `shape evaluate anonymeter` | privacy-risk report of synthetic against real |
| Ibis / DuckDB | `ibis` | source `duckdb`, `shape_integrations.ibis.connect` | read a DuckDB table; explore an output folder |

```
pip install 'sqllocks-shape-integrations[openlineage]'      # one adapter
pip install 'sqllocks-shape-integrations[mlflow,presidio]'  # several
```

Importing `shape_integrations` imports none of these libraries. Using an adapter whose library
is missing exits with code 2 and says how to install it:

```
OpenLineage needs the 'openlineage' extra: pip install 'sqllocks-shape-integrations[openlineage]'
```

The adapters run **after the fact**, on files you give them. Nothing hooks into other Shape
commands, and nothing is sent anywhere unless you name the destination.

Tests that need a tool's library are marked `integration` and run in a dedicated CI job; where
the library is missing they are skipped with the reason, never counted as passes.

## OpenLineage

**Install:** `pip install 'sqllocks-shape-integrations[openlineage]'` (`openlineage-python` 1.x).

**Command:**

```
shape lineage emit MANIFEST.json --to URL|file://PATH [--namespace NS] [--token-env VAR]
```

It turns a run manifest into OpenLineage `RunEvent`s: `START`, then `COMPLETE`, or `FAIL` when
the manifest records a failed gate. `--namespace` defaults to `shape`; it is the namespace of
the job (`shape.<domain>`) and of the datasets.

- `file://PATH` appends one JSON event per line (NDJSON), creating the folder if needed.
- An `http` or `https` URL gets one `POST` of `application/json` per event. A token comes from
  the environment variable named by `--token-env`; there is deliberately no option that takes
  the token itself. A token is sent over plain `http` only to `localhost`, `127.0.0.1` or `::1`,
  and a redirect is an error, so the token never travels to another host.

**What is sent:**

- the run id: OpenLineage needs a UUID, so `run.runId` is a UUID derived from the Shape run id
  (the same manifest always gives the same UUID), and the Shape run id itself is in the `shape`
  run facet;
- one output dataset per table, named after the table. Its `schema` facet lists the column names
  and Arrow types. The manifest records only a column *count*, so the columns are read from the
  schema of the table's output files (`file_paths`, relative to the manifest or absolute). A table
  whose files are gone is listed without a `schema` facet;
- the run facet `shape` ([schema](../../plugins/shape-integrations/src/shape_integrations/schemas/shape-run-facet.json)):
  `version` (1), `runId`, `engineVersion`, and, when the manifest has them, `reproducibility`
  (the reproducibility tuple) and `datasetId`.

**What is never sent:** row values, file contents, file paths, the spec, or any secret. The
token goes only in the `Authorization` header and is never printed.

**Exit codes:** 0 sent; 1 an HTTP error (the message has the status and never the token), no
connection, or a write error; 2 a bad manifest, destination or token variable, or a missing
extra.

## MLflow

**Install:** `pip install 'sqllocks-shape-integrations[mlflow]'` (`mlflow` 3.x).

**Command:**

```
shape mlflow log MANIFEST.json [--profile P.shape] [--verify-report R.json]
                 [--experiment NAME] [--tracking-uri URI] [--allow-duplicate]
```

It logs one MLflow run (experiment `shape` unless `--experiment` says otherwise; created when
missing; the tracking URI is `--tracking-uri` or MLflow's own default):

- **params:** the reproducibility tuple of the manifest, when it has one;
- **metrics**, from the `shape verify` JSON report: `passed`; per gate `gate.<name>.passed`
  (1 or 0), `gate.<name>.errors` and `gate.<name>.warnings` (counts); and every numeric entry of
  the gate's `details` as `gate.<name>.<key>`;
- **artifacts:** the manifest, the profile and the report, exactly as given;
- **tags:** `shape.run_id` and, when the manifest has it, `shape.dataset_id`.

A second log of the same run id to the same experiment is refused with exit 1 unless
`--allow-duplicate` is given. The same run id in another experiment is not a duplicate.

**What is written:** only the list above, to the tracking store you chose. The report's error
and warning *texts* are not metrics, but the report file is attached as given, so check what it
contains before logging it to a shared store.

**Exit codes:** 0 logged; 1 duplicate, or MLflow failed (an unreachable store); 2 a bad input
file or a missing extra.

## Presidio

**Install:** `pip install 'sqllocks-shape-integrations[presidio]'` (`presidio-analyzer` 2.x).

**API:** the semantic detector `presidio` (entry-point group `shape.detectors`). It takes a
bounded, seeded sample of the non-empty values of a text column (200 values by default;
`SHAPE_PRESIDIO_SAMPLE` changes it; the choice depends only on the values and the size), runs
Presidio's analyzer on each value and returns a `Detection`:

- `label`: for the entity type found in the most sampled values, its Shape label from the table
  below, or `presidio:<TYPE>` when the type has no entry;
- `confidence`: the share of sampled values with that type times the mean of Presidio's score for
  it (its best score per value).

Columns that are not text, and empty or all-null columns, give `None`.

| Presidio entity type | Shape label |
|---|---|
| `EMAIL_ADDRESS` | `email` |
| `US_SSN` | `us_ssn` |
| `PHONE_NUMBER` | `phone` |
| `CREDIT_CARD` | `credit_card` |
| `IBAN_CODE` | `iban` |
| `IP_ADDRESS` | `ip_address` |

Any other entity type, for example `URL`, `PERSON` or `LOCATION`, gives `presidio:<TYPE>`.

**No downloads.** Presidio normally downloads a language model on first use. The detector builds
its analyzer without that step: by default it runs Presidio's pattern recognizers (the types in
the table, URLs and similar) on a blank English pipeline. To add name and place recognition,
install a spaCy model yourself and name it in `SHAPE_PRESIDIO_MODEL`; a model that is not
installed is an error, never a download.

**What is never sent or kept:** the analysis runs in-process; nothing leaves the machine. Values
are never logged, printed, put in an error message or included in any report: the result is a
label and a number.
