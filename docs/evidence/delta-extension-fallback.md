# Fresh local Delta extension installation and real fallback

Status: available.

Private account identifiers, service usernames, workspace names and key-file paths are replaced with angle-bracket placeholders where present. Public GitHub run, job and ruleset IDs remain so you can trace the results. Recorded findings, dates, row counts and exit statuses are unchanged.

Captured **2026-10-12 UTC**, product base `8d10aa0a768383a947fa77d9547b066f8cfbd9cc`, Shape **0.9.1**, Python **3.12.14**, DuckDB **1.5.6**, PyArrow **25.0.1**, deltalake **1.6.6**, Linux **6.18.44**, rust kernel. This is a real local checkout receipt. Only published dependency software was downloaded; no VM or live data/cloud account was used. The existing repository deletion-vector fixture is synthetic and committed.

## Fresh installation

Command, with a read-only check proving this is not a pre-existing extension cache:

```bash
python -c "import duckdb; c=duckdb.connect(); print('Delta installed before:', c.execute(\"SELECT installed FROM duckdb_extensions() WHERE extension_name='delta'\").fetchone()[0]); c.execute('INSTALL delta'); c.execute('LOAD delta'); print('DuckDB Delta extension installed and loaded'); c.close()"
```

Complete stdout, exit **0**:

```text
Delta installed before: False
DuckDB Delta extension installed and loaded
```

## Real CLI fallback and artifact view

From the checkout:

```bash
shape profile tests/fixtures/delta_fallback/dv -o /tmp/owner-notes2-delta-fallback/dv.shape
shape cat /tmp/owner-notes2-delta-fallback/dv.shape
```

Complete profile stream, exit **0**:

```text
shape: note: dv: the Delta table uses reader feature(s) deletionVectors that deltalake cannot read; reading it with DuckDB delta_scan instead
{"provenance": {"as_of": null, "fallback_reason": "dv: the Delta table uses reader feature(s) deletionVectors that deltalake cannot read; reading it with DuckDB delta_scan instead", "format": "delta", "reader": "duckdb", "reader_features": ["deletionVectors"], "timestamp": "2026-10-03T03:48:46.451000+00:00", "version": 2}, "shape_content_id": "138321eea84d2b15e1bcdc49b2f67c96b6bf78c93d75b5c59d205274aa58549a", "written": "/tmp/owner-notes2-delta-fallback/dv.shape"}
```

Complete cat stream, exit **0** (the unsigned-origin warning is retained, not suppressed):

```text
shape: note: /tmp/owner-notes2-delta-fallback/dv.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
manifest.capture.k: 5
manifest.capture.mode: "safe"
manifest.format: "shape"
manifest.format_version: 2
manifest.kind: "profile"
manifest.min_shape_version: "0.9.0"
manifest.name: "dv"
manifest.provenance.as_of: null
manifest.provenance.fallback_reason: "dv: the Delta table uses reader feature(s) deletionVectors that deltalake cannot read; reading it with DuckDB delta_scan instead"
manifest.provenance.format: "delta"
manifest.provenance.reader: "duckdb"
manifest.provenance.reader_features[0]: "deletionVectors"
manifest.provenance.timestamp: "2026-10-03T03:48:46.451000+00:00"
manifest.provenance.version: 2
manifest.redaction_manifest.k_default: 5
manifest.redaction_manifest.tables.dv.amount.bounds_winsorized: true
manifest.redaction_manifest.tables.dv.amount.categories_dropped: 0
manifest.redaction_manifest.tables.dv.amount.cells_suppressed: 0
manifest.redaction_manifest.tables.dv.amount.k: 5
manifest.redaction_manifest.tables.dv.amount.pattern_only: true
manifest.redaction_manifest.tables.dv.amount.reason: "high_cardinality"
manifest.redaction_manifest.tables.dv.amount.sensitive: true
manifest.redaction_manifest.tables.dv.amount.suppressed.distribution_params: "sensitive"
manifest.redaction_manifest.tables.dv.amount.suppressed.max_value: "sensitive"
manifest.redaction_manifest.tables.dv.amount.suppressed.min_value: "sensitive"
manifest.redaction_manifest.tables.dv.amount.suppressed.placeholders: "sensitive"
manifest.redaction_manifest.tables.dv.amount.suppressed.value_counts_ext: "sensitive"
manifest.redaction_manifest.tables.dv.id.bounds_winsorized: true
manifest.redaction_manifest.tables.dv.id.categories_dropped: 0
manifest.redaction_manifest.tables.dv.id.cells_suppressed: 0
manifest.redaction_manifest.tables.dv.id.k: 5
manifest.redaction_manifest.tables.dv.id.pattern_only: true
manifest.redaction_manifest.tables.dv.id.reason: "high_cardinality"
manifest.redaction_manifest.tables.dv.id.sensitive: true
manifest.redaction_manifest.tables.dv.id.suppressed.distribution_params: "sensitive"
manifest.redaction_manifest.tables.dv.id.suppressed.max_value: "sensitive"
manifest.redaction_manifest.tables.dv.id.suppressed.min_value: "sensitive"
manifest.redaction_manifest.tables.dv.id.suppressed.placeholders: "sensitive"
manifest.redaction_manifest.tables.dv.id.suppressed.value_counts_ext: "sensitive"
manifest.redaction_manifest.tables.dv.name.bounds_winsorized: false
manifest.redaction_manifest.tables.dv.name.categories_dropped: 0
manifest.redaction_manifest.tables.dv.name.cells_suppressed: 0
manifest.redaction_manifest.tables.dv.name.k: 5
manifest.redaction_manifest.tables.dv.name.pattern_only: true
manifest.redaction_manifest.tables.dv.name.reason: "high_cardinality"
manifest.redaction_manifest.tables.dv.name.sensitive: true
manifest.redaction_manifest.tables.dv.name.suppressed.max_value: "sensitive"
manifest.redaction_manifest.tables.dv.name.suppressed.min_value: "sensitive"
manifest.redaction_manifest.tables.dv.name.suppressed.value_counts_ext: "sensitive"
manifest.redaction_manifest.unsafe: false
manifest.shape_version: "0.9.1"
manifest.version: 2
columns.amount.adequacy.min_detectable_share: 0.032738033833534574
columns.amount.adequacy.non_null: 90
columns.amount.adequacy.null_rate_se: 0.0
columns.amount.bounds.hi: 147.165
columns.amount.bounds.lo: 2.835
columns.amount.cardinality: 90
columns.amount.cardinality_ratio: 1.0
columns.amount.distribution: null
columns.amount.distribution_params: null
columns.amount.dow_histogram: null
columns.amount.dtype: "float"
columns.amount.enum_values: null
columns.amount.fit_score: null
columns.amount.fk_ref_table: null
columns.amount.hour_histogram: null
columns.amount.is_enum: false
columns.amount.is_foreign_key: false
columns.amount.is_primary_key: false
columns.amount.is_unique: true
columns.amount.max_value: null
columns.amount.mean: 75.0
columns.amount.min_value: null
columns.amount.name: "amount"
columns.amount.null_count: 0
columns.amount.null_rate: 0.0
columns.amount.outlier_rate: 0.0
columns.amount.pattern: null
columns.amount.pattern_contains_rates: null
columns.amount.pattern_rates: null
columns.amount.precision: null
columns.amount.quantiles.p0_5: 2.1675
columns.amount.quantiles.p1: 2.835
columns.amount.quantiles.p10: 16.2
columns.amount.quantiles.p25: 37.875
columns.amount.quantiles.p5: 8.175
columns.amount.quantiles.p50: 75.0
columns.amount.quantiles.p75: 112.125
columns.amount.quantiles.p90: 133.8
columns.amount.quantiles.p95: 141.825
columns.amount.quantiles.p99: 147.165
columns.amount.quantiles.p99_5: 147.8325
columns.amount.redacted.distribution_params: "sensitive"
columns.amount.redacted.max_value: "sensitive"
columns.amount.redacted.min_value: "sensitive"
columns.amount.redacted.placeholders: "sensitive"
columns.amount.redacted.value_counts_ext: "sensitive"
columns.amount.scale: null
columns.amount.std: 43.500290584073994
columns.amount.string_length: null
columns.amount.temporal_histogram: null
columns.amount.type_inference.confidence: 1.0
columns.amount.type_inference.declared: "float"
columns.amount.type_inference.identifier: null
columns.amount.type_inference.inferred: "float"
columns.amount.type_inference.parse_shares.boolean: 0.0
columns.amount.type_inference.parse_shares.date: 0.0
columns.amount.type_inference.parse_shares.datetime: 0.0
columns.amount.type_inference.parse_shares.float: 1.0
columns.amount.type_inference.parse_shares.integer: 0.444444
columns.amount.type_inference.source: "declared"
columns.amount.type_inference.type: "float"
columns.amount.value_counts_ext: null
columns.amount.value_counts_ext_order: null
columns.id.adequacy.min_detectable_share: 0.032738033833534574
columns.id.adequacy.non_null: 90
columns.id.adequacy.null_rate_se: 0.0
columns.id.bounds.hi: 98.11
columns.id.bounds.lo: 1.89
columns.id.cardinality: 90
columns.id.cardinality_ratio: 1.0
columns.id.distribution: null
columns.id.distribution_params: null
columns.id.dow_histogram: null
columns.id.dtype: "integer"
columns.id.enum_values: null
columns.id.fit_score: null
columns.id.fk_ref_table: null
columns.id.hour_histogram: null
columns.id.is_enum: false
columns.id.is_foreign_key: false
columns.id.is_primary_key: true
columns.id.is_unique: true
columns.id.max_value: null
columns.id.mean: 50.0
columns.id.min_value: null
columns.id.name: "id"
columns.id.null_count: 0
columns.id.null_rate: 0.0
columns.id.outlier_rate: 0.0
columns.id.pattern: null
columns.id.pattern_contains_rates: null
columns.id.pattern_rates: null
columns.id.precision: null
columns.id.quantiles.p0_5: 1.445
columns.id.quantiles.p1: 1.89
columns.id.quantiles.p10: 10.8
columns.id.quantiles.p25: 25.25
columns.id.quantiles.p5: 5.45
columns.id.quantiles.p50: 50.0
columns.id.quantiles.p75: 74.75
columns.id.quantiles.p90: 89.2
columns.id.quantiles.p95: 94.55
columns.id.quantiles.p99: 98.11
columns.id.quantiles.p99_5: 98.555
columns.id.redacted.distribution_params: "sensitive"
columns.id.redacted.max_value: "sensitive"
columns.id.redacted.min_value: "sensitive"
columns.id.redacted.placeholders: "sensitive"
columns.id.redacted.value_counts_ext: "sensitive"
columns.id.scale: null
columns.id.std: 29.000193722715995
columns.id.string_length: null
columns.id.temporal_histogram: null
columns.id.type_inference.confidence: 1.0
columns.id.type_inference.declared: "integer"
columns.id.type_inference.identifier: null
columns.id.type_inference.inferred: "integer"
columns.id.type_inference.parse_shares.boolean: 0.011111
columns.id.type_inference.parse_shares.date: 0.0
columns.id.type_inference.parse_shares.datetime: 0.0
columns.id.type_inference.parse_shares.float: 1.0
columns.id.type_inference.parse_shares.integer: 1.0
columns.id.type_inference.source: "declared"
columns.id.type_inference.type: "integer"
columns.id.value_counts_ext: null
columns.id.value_counts_ext_order: null
columns.name.adequacy.min_detectable_share: 0.032738033833534574
columns.name.adequacy.non_null: 90
columns.name.adequacy.null_rate_se: 0.0
columns.name.cardinality: 90
columns.name.cardinality_ratio: 1.0
columns.name.distribution: null
columns.name.distribution_params: null
columns.name.dow_histogram: null
columns.name.dtype: "string"
columns.name.enum_values: null
columns.name.fit_score: null
columns.name.fk_ref_table: null
columns.name.hour_histogram: null
columns.name.is_enum: false
columns.name.is_foreign_key: false
columns.name.is_primary_key: false
columns.name.is_unique: true
columns.name.max_value: null
columns.name.mean: null
columns.name.min_value: null
columns.name.name: "name"
columns.name.null_count: 0
columns.name.null_rate: 0.0
columns.name.outlier_rate: null
columns.name.pattern: null
columns.name.pattern_contains_rates: null
columns.name.pattern_rates: null
columns.name.precision: null
columns.name.quantiles: null
columns.name.redacted.max_value: "sensitive"
columns.name.redacted.min_value: "sensitive"
columns.name.redacted.value_counts_ext: "sensitive"
columns.name.scale: null
columns.name.std: null
columns.name.string_length.max: 3.0
columns.name.string_length.mean: 2.9
columns.name.string_length.min: 2.0
columns.name.string_length.p95: 3.0
columns.name.temporal_histogram: null
columns.name.type_inference.confidence: 1.0
columns.name.type_inference.declared: "string"
columns.name.type_inference.identifier: null
columns.name.type_inference.inferred: "string"
columns.name.type_inference.parse_shares.boolean: 0.0
columns.name.type_inference.parse_shares.date: 0.0
columns.name.type_inference.parse_shares.datetime: 0.0
columns.name.type_inference.parse_shares.float: 0.0
columns.name.type_inference.parse_shares.integer: 0.0
columns.name.type_inference.source: "declared"
columns.name.type_inference.type: "string"
columns.name.value_counts_ext: null
columns.name.value_counts_ext_order: null
correlation_matrix.amount.id: 1.0
correlation_matrix.id.amount: 1.0
detected_fks: {}
joint.associations: []
joint.conditionals: []
joint.dependencies: []
joint.dependency_pairs_evaluated: 0
joint.implausible_by_dependency: 0.0
joint.implausible_by_placeholder: 0.011111
joint.implausible_rate: 0.011111
joint.keys: []
joint.multi_determinant_capped: false
joint.multi_determinant_pairs_evaluated: 0
joint.rows_analyzed: 90
joint.sampled: false
joint.version: 1
name: "dv"
primary_key[0]: "id"
row_count: 90
sampling.adequacy: "limited"
sampling.adequacy_reason: "a value must be at least 3.27% of the rows to be seen with 95% probability, above 1%: rarer values may be missing"
sampling.internal: []
sampling.method: "none"
sampling.population_rows: 90
sampling.requested: null
sampling.sampled_rows: 90
sampling.seed: null
```

The real reader reports `DuckDB delta_scan`, `deletionVectors`, reader `duckdb`, version 2 and **90 retained rows**. No mocked reader is used.

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
