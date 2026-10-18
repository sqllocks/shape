# Shape model v2 and the .shape format v2

Status: experimental.


Status: P1-09/P1-10. The normative statements are in `SHAPE_2.md`; this note describes the
model and the file.

## The model

One JSON Schema, `src/shape/schemas/shape-v2.schema.json`, describes the statistical model that
every path produces and reads: the profile engine (exact and bounded mode emit the same layout),
migrated v1 captures and `.shape` files.

```
{ "schema_version": 2, "engine": str, "mode": "exact" | "bounded",
  "name"?: str, "shape_version"?: str, "relationships"?: [...], "metadata"?: {...},
  "tables": { name: { "name", "rows", "columns": [column...], "primary_key"? } },
  "x_legacy"?: {...} }
```

A column uses the profile engine's vocabulary: `kind` (`int float bool text temporal other`),
`count`, `null_count`, the non-finite counts, `min`/`max`, `mean`/`m2`/variances, `distinct` and
`distinct_exact`, `top`, `quantiles`, `length`, `patterns`, histograms and `error_models`.
`distinct_exact` and the error model say whether a figure is exact (exact mode) or bounded
(sketches), which is what contract checks need (P14).

`x_legacy` is the verbatim v1 document of a migrated capture. Contracts, drift, diff, quality and
query (and the relationship entries of `relations`) read the v2 columns, through
`shape.spec.view` (P1-10); generation, privacy, streaming and the registry still read the v1
document, so `x_legacy` and `shape.spec.migrate.legacy_view` stay until those move.

`relationships` is a flat list of `{kind, source, target, ...}` entries (v1 grouped them by
kind); `classifications` maps a field name to its level, and a column may carry its own
`classification`.

## The file

A zip archive of `manifest.json` and `shape.json` (the model). Manifest: `format: "shape"`,
`format_version: 2`, `name`, `shape_content_id` (SHA-256 of `shape.json`), `fidelity`,
`classification`, `metadata`, `content_hashes`. Keys of `shape.json` are sorted; everything
ordered is a list.

Values are JSON, with three tagged objects so that `load(save(x)) == x` (P8, P20):

| in memory | in the file |
|---|---|
| `nan`, `inf`, `-inf` | `{"$float": "nan" \| "inf" \| "-inf"}` |
| a tuple | `{"$tuple": [...]}` |
| a mapping with a key that starts with `$` | `{"$dict": [[key, value], ...]}` |

The bare JSON constants `NaN` and `Infinity` are refused on read. Non-string keys and values of
other types are refused on write.

## Reading and migrating

`read_model(path)` returns `(manifest, model)`. A version 1 file (a v1 capture in `shape.json`)
goes through the registered migration `capture-v1-to-model-v2`; the manifest then carries
`migrated_from` and `source_content_id`. `to_model` also migrates a profile-engine v1 document
(`schema_version: 1`). Migration is read-only: nothing writes version 1.

Every defect of a file raises `ArtifactError` (a `ValueError`; `ArtifactFormatError`, also a
`zipfile.BadZipFile`, when it is not an archive at all). A missing path keeps its `OSError`.

## Entry points

`write_model` / `read_model` speak the v2 model. `write_shape` / `read_shape` are the names the
v1 consumers use: `write_shape` accepts a v2 model or a v1 capture; `read_shape` returns the v1
document of a migrated capture. `shape.save` / `shape.load` (profiles) use the same codec.
