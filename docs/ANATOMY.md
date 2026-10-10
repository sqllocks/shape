# Anatomy of a .shape file

Inspect a real saved profile and understand its fields.

Status: available.

The [committed orders.shape](assets/orders.shape) comes from
[Your first profile](tutorials/01-first-profile.md). The matching
[profile JSON](assets/orders-profile.json) and [manifest JSON](assets/orders-manifest.json)
show the payload and container metadata field by field.
`src/shape/profile/reference/profile.py` defines the profile fields and
`src/shape/artifact/` defines the container and manifest.

## Container and manifest

A `.shape` file is a bounded container, not a row export. The manifest identifies the format,
format version, payload kind and content id. The profile component is `profile.json`.
Readers check capabilities and component integrity before loading. An unsigned file produces
the warning shown in the tutorials: a content hash checks content, not who created it.
Delta source provenance can include a version, commit time and reader; that metadata is
separate from the measured profile content.

| Manifest field | Meaning |
|---|---|
| `format`, `format_version`, `version` | `shape`, with container format version 2. |
| `kind` | `profile`; distinguishes this payload from a model. |
| `shape_version` | Writer version 0.9.1. |
| `min_shape_version` | Minimum reader version; 0.9.0 is a format minimum, not the current docs version. |
| `name` | Stable name `orders`. |
| `content_hashes` | Hash of the `profile.json` component. |
| `shape_content_id` | Content identifier for the measured profile payload. |
| `capture` | Safe mode with k=5. |
| `redaction_manifest` | Per-column sensitivity, suppression reasons, bounds policy and unsafe marker. |

## Profile fields

| Field | Meaning in the committed file |
|---|---|
| `name` | `orders`, the explicit stable name chosen by the tutorial. |
| `row_count` | 40 profiled rows. |
| `primary_key` | Detected key columns, including `id`. |
| `columns` | Each named column's recorded evidence. |
| `sampling` | What rows the profiler examines, including internal samples. |
| `joint` | Joint analysis entries when recorded. |
| `detected_fks` | Detected foreign-key references; empty for this table. |
| `correlation_matrix` | Measured correlations between numeric columns. |

Dataset profiles instead organize named tables and their relationships. A single-table
profile is not interchangeable with a dataset contract.

## Column fields

`name` and `dtype` identify the column. `null_count` and `null_rate` measure missing values.
`cardinality` and `cardinality_ratio` describe distinctness. `is_unique`, `is_primary_key`,
`is_foreign_key` and `fk_ref_table` describe detected key roles.
`mean`, `std`, `quantiles`, `min_value` and `max_value` describe numeric spread.
A tagged scalar such as `["int", 10]` preserves its scalar kind. `distribution`,
`distribution_params` and `fit_score` describe the selected fit.
`enum_values` and `value_counts_ext` hold released category proportions. `pattern`,
`pattern_rates`, `string_length`, `precision` and `scale` describe formats.
Temporal histograms and outlier rates hold additional evidence where available.
`type_inference` explains the inferred type and parse shares. `adequacy` records the population
size and uncertainty. `redacted` explains withheld fields; `bounds` gives reduced bounds.

For `id`, the committed safe capture withholds min/max and value counts because it is
unique-like. The `amount` categories each have eight rows and remain listed. `status` has one
category, `paid`, with forty rows. Null fields are not promises that an analysis passed.

## Related

[Concepts](CONCEPTS.md) · [HTML report](READ_REPORT.md) · [What leaves my machine](WHAT_LEAVES.md)
