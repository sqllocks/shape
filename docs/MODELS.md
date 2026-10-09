# Models

Use the model command surface when you need model evidence or model queries.

Status: available (shipped model commands); experimental (model analyses outside the CLI stable set).
The supported early-access learning workflow remains profiles, contracts and drift.
The 1.x CLI compatibility policy is coming.

A model is a different payload from a profile. Capture creates a model; show reads its evidence;
query evaluates its query language; compatibility compares model schemas. The following local
transcript is checked by the docs harness. Queries reject profile artifacts with exit 2.

## Local command transcript

```bash {.runnable}
python - <<'PYDATA'
from pathlib import Path
Path("model.csv").write_text("id,value\n1,10\n2,20\n3,30\n")
print("Wrote model.csv: 3 rows")
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote model.csv: 3 rows
    ```

```bash {.runnable}
shape capture model.csv -o model.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "72b29963a23980f9caca88188ad712c97d63d28d0ede40117f2d9a91bc2ea852", "written": "model.shape"}
    ```

```bash {.runnable}
shape show model.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: model.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"kind": "model", "manifest": {"classification": "PUBLIC", "content_hashes": {"shape.json": "72b29963a23980f9caca88188ad712c97d63d28d0ede40117f2d9a91bc2ea852"}, "fidelity": "gold", "format": "shape", "format_version": 2, "metadata": {}, "min_shape_version": "0.9.0", "name": "model", "shape_content_id": "72b29963a23980f9caca88188ad712c97d63d28d0ede40117f2d9a91bc2ea852", "shape_version": "0.9.1", "version": 2}, "shape": {"engine": "shape-capture-v1", "mode": "bounded", "name": "model", "schema_version": 2, "tables": {"model": {"columns": [{"arrow_type": "unknown", "count": 3, "distinct": 3.0002795333274483, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 3, "kind": "float", "max": 3, "mean": 2.0, "min": 1, "name": "id", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 1.0, "0.5": 2.0, "0.75": 2.0}, "top": [[1, 1, 0], [2, 1, 0], [3, 1, 0]], "variance_population": 0.6666666666666666}, {"arrow_type": "unknown", "count": 3, "distinct": 3.0002795049989444, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 3, "kind": "float", "max": 30, "mean": 20.0, "min": 10, "name": "value", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 10.0, "0.5": 20.0, "0.75": 20.0}, "top": [[10, 1, 0], [20, 1, 0], [30, 1, 0]], "variance_population": 66.66666666666667}], "name": "model", "rows": 3}}, "x_legacy": {"columns": {"id": {"count": 3, "distinct_estimate": 3.0002795333274483, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 3, "kind": "numeric", "max": 3, "mean": 2.0, "min": 1, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 1.0, "q50": 2.0, "q75": 2.0, "topk": [[1, 1, 0], [2, 1, 0], [3, 1, 0]], "variance_population": 0.6666666666666666}, "value": {"count": 3, "distinct_estimate": 3.0002795049989444, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 3, "kind": "numeric", "max": 30, "mean": 20.0, "min": 10, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 10.0, "q50": 20.0, "q75": 20.0, "topk": [[10, 1, 0], [20, 1, 0], [30, 1, 0]], "variance_population": 66.66666666666667}}, "rows": 3}}, "signature": {"key_id": null, "status": "unsigned", "verified": false}}
    ```

```bash {.runnable}
shape query model.shape 'column("value").mean'
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: model.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"result": 20.0}
    ```

```bash {.runnable}
shape compatibility model.shape model.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: model.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"compatible": true, "issues": [], "mode": "backward"}
    ```

## Limits

A model can record sensitive evidence. Apply your data-handling policy before committing it.
Do not use this page's model flow as a substitute for the profile safe-capture tutorials.

## Related

[Concepts](CONCEPTS.md) · [CLI reference](reference/cli.md) · [Known limitations](KNOWN_LIMITATIONS.md)
