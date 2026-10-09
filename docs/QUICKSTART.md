# Quickstart

Follow one short route from local rows to a reviewed baseline and generated data.

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" QUICKSTART
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for QUICKSTART
    ```


Start with [Your first profile](tutorials/01-first-profile.md). Its tested commands create
21,750 retail rows across nine tables, profile the folder and save a `.shape` file. The walkthrough also shows the dataset safe-validator refusal. You can
open the HTML file locally; it needs no account.

Next, [write a contract](tutorials/04-contract.md). You state that the id must be unique
and non-null and that an amount cannot be negative. Run the check against the saved profile
and read the observed output. A zero exit code means those rules pass; an unavailable rule
is not a pass.

Then [commit a shape and catch drift](tutorials/03-drift.md). Keep the profile in an isolated
Git repository, change the input, profile again and compare the artifacts. The example includes
the expected nonzero drift exit code. Review a change before accepting a baseline.

Finally, [generate dev data from a profile](tutorials/05-generate-profile.md). Use an explicit
format and output directory. Generation from a profile is available and is being hardened.
Inspect the rows instead of assuming every property of the original data is reproduced.

## What's next

[Read the report](tutorials/02-read-report.md) or
[generate a domain into DuckDB](tutorials/06-domain-duckdb.md).

## Models examples

Status: experimental. These restored examples use the model commands described in [Models](MODELS.md). The starter route above uses profiles.

[Run this example](#local-example-0).

[Run this example](#local-example-1).

[Run this example](#local-example-2).

[Run this example](#local-example-3).

## Related

[Install](INSTALL.md) · [Learning paths](LEARNING_PATHS.md) · [Known limitations](KNOWN_LIMITATIONS.md)


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape capture customers.csv -o customers.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "538b3a461b6a9ae33816fab39a08ff71d47c54011aacce03c99efcf84d13427d", "written": "customers.shape"}
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape show customers.shape
shape query customers.shape 'column("age").mean'
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: customers.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"kind": "model", "manifest": {"classification": "PUBLIC", "content_hashes": {"shape.json": "538b3a461b6a9ae33816fab39a08ff71d47c54011aacce03c99efcf84d13427d"}, "fidelity": "gold", "format": "shape", "format_version": 2, "metadata": {}, "min_shape_version": "0.9.0", "name": "customers", "shape_content_id": "538b3a461b6a9ae33816fab39a08ff71d47c54011aacce03c99efcf84d13427d", "shape_version": "0.9.1", "version": 2}, "shape": {"engine": "shape-capture-v1", "mode": "bounded", "name": "customers", "schema_version": 2, "tables": {"customers": {"columns": [{"arrow_type": "unknown", "count": 20, "distinct": 20.012410819218463, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "float", "max": 20, "mean": 10.5, "min": 1, "name": "customer_id", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 5.0, "0.5": 10.0, "0.75": 15.0}, "top": [[1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0], [5, 1, 0], [6, 1, 0], [7, 1, 0], [8, 1, 0], [9, 1, 0], [10, 1, 0]], "variance_population": 33.25}, {"arrow_type": "unknown", "count": 20, "distinct": 20.012410819218463, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "float", "max": 20, "mean": 10.5, "min": 1, "name": "id", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 5.0, "0.5": 10.0, "0.75": 15.0}, "top": [[1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0], [5, 1, 0], [6, 1, 0], [7, 1, 0], [8, 1, 0], [9, 1, 0], [10, 1, 0]], "variance_population": 33.25}, {"arrow_type": "unknown", "count": 20, "distinct": 20.01240684419727, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "float", "max": 40, "mean": 30.5, "min": 21, "name": "age", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 25.0, "0.5": 30.0, "0.75": 35.0}, "top": [[21, 1, 0], [22, 1, 0], [23, 1, 0], [24, 1, 0], [25, 1, 0], [26, 1, 0], [27, 1, 0], [28, 1, 0], [29, 1, 0], [30, 1, 0]], "variance_population": 33.25}, {"arrow_type": "unknown", "count": 20, "distinct": 20.012409913325534, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 9, "mean": 8.55, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 9.0, "q75": 9.0, "topk": [[9, 11, 0], [8, 9, 0]], "variance_population": 0.24749999999999997}, "name": "name", "null_count": 0, "top": [["Person 1", 1, 0], ["Person 10", 1, 0], ["Person 11", 1, 0], ["Person 12", 1, 0], ["Person 13", 1, 0], ["Person 14", 1, 0], ["Person 15", 1, 0], ["Person 16", 1, 0], ["Person 17", 1, 0], ["Person 18", 1, 0]]}, {"arrow_type": "unknown", "count": 20, "distinct": 20.012409300307738, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 21, "mean": 20.55, "min": 20, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 20.0, "q50": 21.0, "q75": 21.0, "topk": [[21, 11, 0], [20, 9, 0]], "variance_population": 0.24749999999999997}, "name": "email", "null_count": 0, "top": [["person10@example.test", 1, 0], ["person11@example.test", 1, 0], ["person12@example.test", 1, 0], ["person13@example.test", 1, 0], ["person14@example.test", 1, 0], ["person15@example.test", 1, 0], ["person16@example.test", 1, 0], ["person17@example.test", 1, 0], ["person18@example.test", 1, 0], ["person19@example.test", 1, 0]]}, {"arrow_type": "unknown", "count": 20, "distinct": 2.000109379736731, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 20, 0]], "variance_population": 0.0}, "name": "region", "null_count": 0, "top": [["north", 10, 0], ["south", 10, 0]]}, {"arrow_type": "unknown", "count": 20, "distinct": 1.0000241666629792, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 20, 0]], "variance_population": 0.0}, "name": "city", "null_count": 0, "top": [["New York", 20, 0]]}, {"arrow_type": "unknown", "count": 20, "distinct": 1.0000241679541289, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 10, "mean": 10.0, "min": 10, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 10.0, "q50": 10.0, "q75": 10.0, "topk": [[10, 20, 0]], "variance_population": 0.0}, "name": "born", "null_count": 0, "top": [["1990-01-01", 20, 0]]}, {"arrow_type": "unknown", "count": 20, "distinct": 20.01240774908, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "float", "max": 120, "mean": 110.5, "min": 101, "name": "income", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 105.0, "0.5": 110.0, "0.75": 115.0}, "top": [[101, 1, 0], [102, 1, 0], [103, 1, 0], [104, 1, 0], [105, 1, 0], [106, 1, 0], [107, 1, 0], [108, 1, 0], [109, 1, 0], [110, 1, 0]], "variance_population": 33.25}, {"arrow_type": "unknown", "count": 20, "distinct": 2.000109384901645, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 5, "mean": 4.5, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.5, "q75": 5.0, "topk": [[4, 10, 0], [5, 10, 0]], "variance_population": 0.25}, "name": "churned", "null_count": 0, "top": [["False", 10, 0], ["True", 10, 0]]}], "name": "customers", "rows": 20}}, "x_legacy": {"columns": {"age": {"count": 20, "distinct_estimate": 20.01240684419727, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "numeric", "max": 40, "mean": 30.5, "min": 21, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 25.0, "q50": 30.0, "q75": 35.0, "topk": [[21, 1, 0], [22, 1, 0], [23, 1, 0], [24, 1, 0], [25, 1, 0], [26, 1, 0], [27, 1, 0], [28, 1, 0], [29, 1, 0], [30, 1, 0]], "variance_population": 33.25}, "born": {"count": 20, "distinct_estimate": 1.0000241679541289, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 10, "mean": 10.0, "min": 10, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 10.0, "q50": 10.0, "q75": 10.0, "topk": [[10, 20, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["1990-01-01", 20, 0]]}, "churned": {"count": 20, "distinct_estimate": 2.000109384901645, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 5, "mean": 4.5, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.5, "q75": 5.0, "topk": [[4, 10, 0], [5, 10, 0]], "variance_population": 0.25}, "null_count": 0, "topk": [["False", 10, 0], ["True", 10, 0]]}, "city": {"count": 20, "distinct_estimate": 1.0000241666629792, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 20, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["New York", 20, 0]]}, "customer_id": {"count": 20, "distinct_estimate": 20.012410819218463, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "numeric", "max": 20, "mean": 10.5, "min": 1, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 10.0, "q75": 15.0, "topk": [[1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0], [5, 1, 0], [6, 1, 0], [7, 1, 0], [8, 1, 0], [9, 1, 0], [10, 1, 0]], "variance_population": 33.25}, "email": {"count": 20, "distinct_estimate": 20.012409300307738, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 21, "mean": 20.55, "min": 20, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 20.0, "q50": 21.0, "q75": 21.0, "topk": [[21, 11, 0], [20, 9, 0]], "variance_population": 0.24749999999999997}, "null_count": 0, "topk": [["person10@example.test", 1, 0], ["person11@example.test", 1, 0], ["person12@example.test", 1, 0], ["person13@example.test", 1, 0], ["person14@example.test", 1, 0], ["person15@example.test", 1, 0], ["person16@example.test", 1, 0], ["person17@example.test", 1, 0], ["person18@example.test", 1, 0], ["person19@example.test", 1, 0]]}, "id": {"count": 20, "distinct_estimate": 20.012410819218463, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "numeric", "max": 20, "mean": 10.5, "min": 1, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 10.0, "q75": 15.0, "topk": [[1, 1, 0], [2, 1, 0], [3, 1, 0], [4, 1, 0], [5, 1, 0], [6, 1, 0], [7, 1, 0], [8, 1, 0], [9, 1, 0], [10, 1, 0]], "variance_population": 33.25}, "income": {"count": 20, "distinct_estimate": 20.01240774908, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 20, "kind": "numeric", "max": 120, "mean": 110.5, "min": 101, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 105.0, "q50": 110.0, "q75": 115.0, "topk": [[101, 1, 0], [102, 1, 0], [103, 1, 0], [104, 1, 0], [105, 1, 0], [106, 1, 0], [107, 1, 0], [108, 1, 0], [109, 1, 0], [110, 1, 0]], "variance_population": 33.25}, "name": {"count": 20, "distinct_estimate": 20.012409913325534, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 2.0, "finite_count": 20, "max": 9, "mean": 8.55, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 9.0, "q75": 9.0, "topk": [[9, 11, 0], [8, 9, 0]], "variance_population": 0.24749999999999997}, "null_count": 0, "topk": [["Person 1", 1, 0], ["Person 10", 1, 0], ["Person 11", 1, 0], ["Person 12", 1, 0], ["Person 13", 1, 0], ["Person 14", 1, 0], ["Person 15", 1, 0], ["Person 16", 1, 0], ["Person 17", 1, 0], ["Person 18", 1, 0]]}, "region": {"count": 20, "distinct_estimate": 2.000109379736731, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 20, "distinct_estimate": 1.0, "finite_count": 20, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 20, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["north", 10, 0], ["south", 10, 0]]}}, "rows": 20}}, "signature": {"key_id": null, "status": "unsigned", "verified": false}}
    shape: note: customers.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"result": 30.5}
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape capture customers_today.csv -o customers-today.shape
shape compatibility customers.shape customers-today.shape --mode backward
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "0855ec68d0268a68c1cd2f78115a379e87e9f33a1d63607183df22a06318e616", "written": "customers-today.shape"}
    shape: note: customers.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: customers-today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"compatible": true, "issues": [], "mode": "backward"}
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape capture orders.parquet -o orders-base.shape
shape capture orders-today.parquet -o orders-today.shape
shape compatibility orders-base.shape orders-today.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "written": "orders-base.shape"}
    {"shape_content_id": "3db012279effb591c5c20f9d053c296849287d97ee137090352b73fc28f0a667", "written": "orders-today.shape"}
    shape: note: orders-base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: orders-today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"compatible": true, "issues": [], "mode": "backward"}
    ```
