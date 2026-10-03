# Python API reference

Everything `import shape` exports (`shape.__all__`). Each signature below is the code's own
(without annotations); `tests/api/test_public_api.py` fails when one of them and the code
differ. Names are loaded on first use, so `import shape` stays cheap.

Stability: `profile`, `save`, `load`, `check` and `diff` are the early-access surface and keep
the forms below (`README.md`); the modules that are stable are listed in
`docs/specs/SHAPE_2.md` ("Stability levels") and `docs/API_STABILITY.md`. The generation,
model and query functions are documented as they behave today and may change.

## Profiles

```python
shape.profile(source, *, name=None, version=None, as_of=None, delimiter=None, encoding=None, quotechar=None, header=True, reference_pairs=None, joint=None, sheet=None, include_hidden=False)
```

Profiles `source` and returns a `Profile`. `source` is a path (CSV, Parquet, JSONL, `.xlsx`, a
Delta table directory, a glob or a directory of files), a `pyarrow.Table`, a
`pandas.DataFrame`, a list of row dicts, or a `dict` of such sources for several tables (with
foreign-key detection). Every argument after `source` is keyword-only. `version` and `as_of`
select a Delta table version (`README.md`); `delimiter`, `encoding`, `quotechar` and `header`
are CSV options (`docs/PROFILING_NOTES.md`); `reference_pairs` and `joint` control the joint
analysis (`docs/JOINT.md`); `sheet` and `include_hidden` select workbook sheets
(`docs/EXCEL.md`).

A `Profile` has `to_dict()` (the full profile), `summary()` (a small JSON-safe dict),
`to_html()` (a self-contained report), `name`, `tables`, `is_dataset` and `provenance`.

Raises `FileNotFoundError` for a missing path and `SourceError` (a `ValueError`) for an
unsupported or empty source.

```python
shape.save(p, path)
```

Writes the profile `p` to the `.shape` artifact `path` and returns its content id (sha256).
Raises `TypeError` when `p` is not a `Profile`.

```python
shape.load(path)
```

Reads a `.shape` profile artifact written by `save` and returns the `Profile`. Every defect of
the file raises `ArtifactError` (a `ShapeError`); a missing file raises `FileNotFoundError`.

## Contracts and drift

```python
shape.check(profile, contract)
```

Checks a profile against a v1 contract, a `dict` or the path to a JSON file, and returns a
`CheckResult` with `passed` (bool), `violations` (a list of
`{column, rule, expected, observed}`) and `to_dict()`. Every rule is optional; a profile of
several tables needs a contract with a `tables` object. A malformed contract raises
`ContractError` (a `ValueError`).

```python
shape.diff(baseline, current, *, thresholds=None, ignore_columns=None, column_thresholds=None, only_columns=None, policy=None)
```

Compares two profiles (a stream window profile or a profile document works too) with the
defaults in `docs/DRIFT.md` and returns a `DiffResult` with `drifted` (bool), `changes` (a list
of `{column, kind, baseline, current, severity, score}`) and `to_dict()`. Every argument after
`current` is keyword-only. `shape.diff` is also the `shape.diff` package; calling it runs this
function.

## Generation

```python
shape.generate(shape, n=None, seed=None, relationships=None, *, scale=None, mode=None)
```

What `shape` is decides the form:

| `shape` | Result | Arguments used |
|---|---|---|
| a domain name, a `GenSchema` or a generation schema `dict` (with `"tables"`) | `GenerationResult` (`result.tables`, `result["table"]`) | `seed` (default: the schema's), `scale` (a preset name), `mode` (`3nf` or `star`, domains only) |
| a profile or its `to_dict()` | `GenerationResult` | `n` (row count of a one-table profile), `seed`, `scale` |
| any other mapping: an evidence document `{"rows": ..., "columns": {...}}` | `(columns, GenerationReport)` | `n`, `seed` (default 0), `relationships` |

Arguments a form does not use are ignored (issue #251). The `shape.Shape` model is not
generation input. An unknown domain raises `DomainNotFoundError` (a `ShapeError`).

```python
shape.plan(shape)
```

Returns a `ReconstructionPlan`: what data generated from `shape` (a profile, its `to_dict()` or
an evidence document) keeps and what it does not, as `PlanItem(evidence, status, reason)`.

```python
shape.certify(target, observed, **kwargs)
```

Scores how well the evidence document `observed` matches `target` and returns a
`FidelityCertificate` (`score`, `dimensions`, `degraded`, `unavailable`). `kwargs` are
`degraded` and `unavailable` (tuples of evidence names). Both inputs are evidence documents,
not profiles.

```python
shape.timeline(versions)
```

Returns a `ShapeTimeline` over `versions` (objects with `version`, `at` and `shape`, such as
`shape.generation.timeline.VersionedShape`): `shape_at(t)`, `generate_at`, `generate_range`
and `changes()`. Raises `ValueError` for no versions or two at the same time.

## Shape models and queries

```python
shape.query(shape, expression)
```

Evaluates a Shape Query over a Shape model document (v2, or a v1 capture migrated on read):
`rows`, `rows("table")`, `column("name")`, `classification("name")` or
`relationship("a", "b")`, each with an optional `.field.field` path. An unsupported expression
raises `ShapeQueryError` (SH2-028); a document that is not a model, such as a profile, raises
`ModelError`. No code is evaluated (SH2-029). `shape.query` is also the `shape.query` package.

```python
shape.view(shape)
```

Returns a `ShapeView` with `query`, `column`, `relationship` and `classification` methods over
the same documents. The document is read on the first call.

```python
shape.ShapeBuilder()
```

Builds an immutable `Shape`: `add_field(FieldType)`, `add_evidence(key, Evidence)` and
`join_sensitivity(Sensitivity)` return the builder; `finalize()` returns the `Shape`.

```python
shape.Shape(shape_id, fields, sensitivity=<factory>, evidence=<factory>)
```

An immutable Shape model: a UUID, a tuple of `FieldType`, a `Sensitivity` and a read-only
mapping of `Evidence`.

```python
shape.Evidence(provenance, value, method=None)
```

One piece of evidence with its `Provenance` and the method that produced it.

`shape.Provenance` is a string enum: `OBSERVED`, `INFERRED`, `DECLARED`, `DERIVED`,
`INTERPOLATED` and `EXTRAPOLATED`.

```python
shape.Sensitivity(classifications=<factory>, categories=<factory>, compartments=<factory>, restrictions=<factory>)
```

Sensitivity labels, four frozensets of strings (empty by default). `join(*others)` is their
union and `dominates(other)` is true when every set is a superset.

## Types

```python
shape.LogicalType(kind, bit_width=None, precision=None, scale=None, unit=None, timezone=None, value_type=None, key_type=None, fields=())
```

An immutable logical type. `decimal` needs `precision` and `scale`, `list` and `large_list`
need `value_type`, `map` needs `key_type` and `value_type`; otherwise `ValueError`. Other kinds
are not validated (issue #260).

```python
shape.FieldType(name, logical_type, nullable=True)
```

A named, typed field.

```python
shape.from_arrow_type(data_type)
```

Converts a `pyarrow.DataType` to a `LogicalType` without silent narrowing; an unsupported type
(`null`, `float16`, dictionary, union, ...) raises `ShapeTypeError`.

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
