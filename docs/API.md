# Python API guide

Status: available (profiles, contracts and drift); experimental (other APIs).

[Generated public reference](https://docs.shapedata.ai/reference/api/) documents the exported module surfaces in addition to this guide. The site generates reference/api.md at build time.

Everything `import shape` exports (`shape.__all__`). Each signature below is the code's own
(without annotations); `tests/api/test_public_api.py` fails when one of them and the code
differ. Names are loaded on first use, so `import shape` stays cheap.

Stability: `profile`, `save`, `load`, `check` and `diff` are the early-access surface and keep
the forms below (`README.md`); the modules that are stable are listed in
`docs/specs/SHAPE_2.md` ("Stability levels") and `docs/API_STABILITY.md`. The generation,
model and query functions are documented as they behave today and may change.

## Profiles

<!-- example: 0 -->

Call signature:

```python
shape.profile(source, *, name=None, version=None, as_of=None, delimiter=None, encoding=None, quotechar=None, header=True, string_columns=(), types=None, infer_types='auto', reference_pairs=None, joint=None, sheet=None, include_hidden=False, sketches=False, univariate=False, multivariate=False, sample=None, sample_method='random', sample_seed=None, decisions=None, validators=None, time_column=None)
```

Profiles `source` and returns a `Profile`. `source` is a path (CSV, Parquet, JSONL, `.xlsx`, a
Delta table directory, a glob or a directory of files), a `pyarrow.Table`, a
`pandas.DataFrame`, a list of row dicts, or a `dict` of such sources for several tables (with
foreign-key detection). Every argument after `source` is keyword-only. `version` and `as_of`
select a Delta table version (`README.md`); `delimiter`, `encoding`, `quotechar` and `header`
are CSV options (`docs/PROFILING_NOTES.md`); `reference_pairs` and `joint` control the joint
analysis (`docs/JOINT.md`); `sheet` and `include_hidden` select workbook sheets
(`docs/EXCEL.md`); `string_columns`, `types` and `infer_types` keep identifier columns as text
(`docs/PROFILING_NOTES.md`); `sketches` keeps the mergeable sketch state
(`docs/PROFILE_MERGE.md`); `univariate=True` adds the univariate depth fields to numeric columns
(`docs/PROFILING_NOTES.md`; off by default). `sample`, `sample_method` and `sample_seed` profile
a sample of each table and `decisions` applies the accepted `type` decisions of a decision file
(`docs/PROFILING_NOTES.md`, "Sampling" and "Type inference"); `sketches` cannot be combined with
`sample`. `multivariate=True` adds the multivariate entries of the joint analysis (`docs/JOINT.md`;
off by default). `validators` (`{column: kind}`) records how many values of a column are valid codes
(`docs/REFERENCE_PACKS.md`). `time_column` names the column the seasonality of numeric columns is
measured along, with `univariate=True` (`docs/PROFILING_NOTES.md`).

A `Profile` has `to_dict()` (the full profile), `summary()` (a small JSON-safe dict),
`to_html()` (a self-contained report), `name`, `tables`, `is_dataset` and `provenance`.

Raises `FileNotFoundError` for a missing path and `SourceError` (a `ValueError`) for an
unsupported or empty source.

<!-- example: 1 -->

Call signature:

```python
shape.save(p, path, capture='safe', *, k=5, column_k=None, classifications=None, vault=None, vault_policy=None, kek=None)
```

Writes the profile `p` to the `.shape` artifact `path` and returns its content id (sha256).
`capture='safe'` (the default) keeps statistics and formats only for a sensitive column and
category values only where each category has at least `k` rows (`column_k`, `classifications`);
`capture='full'` keeps real values and the sketch state, and must not be shared
(`docs/PRIVACY_MODEL.md`). With `vault`, `vault_policy` and `kek` (a key reference, never the
key) a safe capture also writes the value vault of the values it withheld (`docs/VAULT.md`).
Raises `TypeError` when `p` is not a `Profile`.

<!-- example: 2 -->

Call signature:

```python
shape.load(path)
```

Reads a `.shape` profile artifact written by `save` and returns the `Profile`. Every defect of
the file raises `ArtifactError` (a `ShapeError`); a missing file raises `FileNotFoundError`.

## Contracts and drift

<!-- example: 3 -->

Call signature:

```python
shape.check(profile, contract, data=None, *, strict=False, enforce_learned=False)
```

Checks a profile against a v1 contract, a `dict` or the path to a JSON file, and returns a
`CheckResult` with `passed` (bool), `violations` (a list of
`{column, rule, expected, observed}`) and `to_dict()`. Every rule is optional; a profile of
several tables needs a contract with a `tables` object. `data` (tables read with
`shape.quality.load_tables`) is what the contract's `timeseries` and `reconcile` rules check
(`docs/VERIFY.md`). A rule that needs a value a safe capture left out is listed in
`not_evaluable` (and `passed` is false). When the contract declares a rule `strength`, a broken
rule that does not fail the check is in `warnings`; `strict=True` puts every broken rule in
`violations`, and `enforce_learned=True` makes `learned` rules fail the check (`docs/CONTRACTS.md`).
A malformed contract raises `ContractError` (a `ValueError`).

<!-- example: 4 -->

Call signature:

```python
shape.diff(baseline, current, *, thresholds=None, ignore_columns=None, column_thresholds=None, only_columns=None, policy=None, planned=None, on=None, source=None, fail_on=None)
```

Compares two profiles (a stream window profile or a profile document works too) with the
defaults in `docs/DRIFT.md` and returns a `DiffResult` with `drifted` (bool), `changes` (a list
of `{column, kind, baseline, current, severity, score}`) and `to_dict()`. Every argument after
`current` is keyword-only. `planned`, `on` and `source` apply planned changes
(`docs/PLANNED_CHANGES.md`); `fail_on` and the change classes are in `docs/DRIFT.md`
("Change classes"). `not_evaluable` lists the comparisons a safe capture made impossible
(never drift), and `notes` says when the two profiles were sampled differently. `shape.diff` is
also the `shape.diff` package; calling it runs this function.

<!-- example: 5 -->

Call signature:

```python
shape.types_report(profile, contract=None, min_confidence=0.99)
```

The columns of `profile` whose declared, inferred or contract types disagree, as a list of dicts
(`table`, `column`, `kind`, `declared`, `inferred`, `confidence`, `option`, `message`); what
`shape types` prints (`docs/PROFILING_NOTES.md`, "Type inference").

## Generation

<!-- example: 6 -->

Call signature:

```python
shape.generate(shape, n=None, seed=None, relationships=None, *, scale=None, mode=None, mixed_copula=False, identifiers=None)
```

What `shape` is decides the form:

| `shape` | Result | Arguments used |
|---|---|---|
| a domain name, a `GenSchema` or a generation schema `dict` (with `"tables"`) | `GenerationResult` (`result.tables`, `result["table"]`) | `seed` (default: the schema's), `scale` (a preset name), `mode` (`3nf` or `star`, domains only), `identifiers` |
| a profile or its `to_dict()` | `GenerationResult` | `n` (row count of a one-table profile), `seed`, `scale`, `identifiers` |
| any other mapping: an evidence document `{"rows": ..., "columns": {...}}` | `(columns, GenerationReport)` | `n`, `seed` (default 0), `relationships` |

Arguments a form does not use are ignored (issue #251). The `shape.Shape` model is not
generation input.

`identifiers` is the run switch of the identifier values (`docs/GENERATION_STRATEGIES.md`,
"Values that cannot belong to a real person"): `"reserved"` or `"realistic"`. Left out, the
schema's top-level `"identifiers"` applies, else `reserved`; a column's own `domains` or `range`
key wins over both. `realistic` prints one line on standard error. Any other value raises
`ValueError`, for every form (an evidence document has no identifier columns, so the switch
changes nothing there). An unknown domain raises `DomainNotFoundError` (a `ShapeError`).

<!-- example: 7 -->

Call signature:

```python
shape.plan(shape)
```

Returns a `ReconstructionPlan`: what data generated from `shape` (a profile, its `to_dict()` or
an evidence document) keeps and what it does not, as `PlanItem(evidence, status, reason)`.

<!-- example: 8 -->

Call signature:

```python
shape.certify(target, observed, **kwargs)
```

Scores how well the evidence document `observed` matches `target` and returns a
`FidelityCertificate` (`score`, `dimensions`, `degraded`, `unavailable`). `kwargs` are
`degraded` and `unavailable` (tuples of evidence names). Both inputs are evidence documents,
not profiles.

<!-- example: 9 -->

Call signature:

```python
shape.timeline(versions)
```

Returns a `ShapeTimeline` over `versions` (objects with `version`, `at` and `shape`, such as
`shape.generation.timeline.VersionedShape`): `shape_at(t)`, `generate_at`, `generate_range`
and `changes()`. Raises `ValueError` for no versions or two at the same time.

## Shape models and queries

<!-- example: 10 -->

Call signature:

```python
shape.query(shape, expression)
```

Evaluates a Shape Query over a Shape model document (v2, or a v1 capture migrated on read):
`rows`, `rows("table")`, `column("name")`, `classification("name")` or
`relationship("a", "b")`, each with an optional `.field.field` path. An unsupported expression
raises `ShapeQueryError` (SH2-028); a document that is not a model, such as a profile, raises
`ModelError`. No code is evaluated (SH2-029). `shape.query` is also the `shape.query` package.

<!-- example: 11 -->

Call signature:

```python
shape.view(shape)
```

Returns a `ShapeView` with `query`, `column`, `relationship` and `classification` methods over
the same documents. The document is read on the first call.

<!-- example: 12 -->

Call signature:

```python
shape.ShapeBuilder()
```

Builds an immutable `Shape`: `add_field(FieldType)`, `add_evidence(key, Evidence)` and
`join_sensitivity(Sensitivity)` return the builder; `finalize()` returns the `Shape`.

<!-- example: 13 -->

Call signature:

```python
shape.Shape(shape_id, fields, sensitivity=<factory>, evidence=<factory>)
```

An immutable Shape model: a UUID, a tuple of `FieldType`, a `Sensitivity` and a read-only
mapping of `Evidence`.

<!-- example: 14 -->

Call signature:

```python
shape.Evidence(provenance, value, method=None)
```

One piece of evidence with its `Provenance` and the method that produced it.

`shape.Provenance` is a string enum: `OBSERVED`, `INFERRED`, `DECLARED`, `DERIVED`,
`INTERPOLATED` and `EXTRAPOLATED`.

<!-- example: 15 -->

Call signature:

```python
shape.Sensitivity(classifications=<factory>, categories=<factory>, compartments=<factory>, restrictions=<factory>)
```

Sensitivity labels, four frozensets of strings (empty by default). `join(*others)` is their
union and `dominates(other)` is true when every set is a superset.

## Types

<!-- example: 16 -->

Call signature:

```python
shape.LogicalType(kind, bit_width=None, precision=None, scale=None, unit=None, timezone=None, value_type=None, key_type=None, fields=())
```

An immutable logical type. `decimal` needs `precision` and `scale`, `list` and `large_list`
need `value_type`, `map` needs `key_type` and `value_type`; otherwise `ValueError`. Other kinds
are not validated (issue #260).

<!-- example: 17 -->

Call signature:

```python
shape.FieldType(name, logical_type, nullable=True)
```

A named, typed field.

<!-- example: 18 -->

Call signature:

```python
shape.from_arrow_type(data_type)
```

Converts a `pyarrow.DataType` to a `LogicalType` without silent narrowing; an unsupported type
(`null`, `float16`, dictionary, union, ...) raises `ShapeTypeError`.

<!-- example: 19 -->

Call signature:

```python
shape.schema_from_arrow(schema)
```

Converts a `pyarrow.Schema` to a tuple of `FieldType`, one per field, in order.

## Errors

`shape.errors` defines `ShapeError`, the base of the expected failures, and its subclasses
`ShapeTypeError`, `ShapeSchemaError`, `ShapeSecurityError` and `ShapeCapabilityError`.
`ArtifactError`, `DomainNotFoundError` and the registry errors derive from it; `ContractError`,
`SourceError`, `ShapeQueryError` and `ModelError` derive from `ValueError` only (issue #249).
Wrong argument types can raise `AttributeError` (issue #249).

## Typing

The package ships `py.typed` and a stub for its native kernel, so type checkers use the
annotations; `shape/__init__.py` declares every name above for them.
