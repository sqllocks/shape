# AUD-api — audit of the public Python API surface — status

Branch `lane/AUD-api`, from `build/main-plan` at 5c91ea5 (INT-15 merged). Area: everything
`import shape` exports (`shape.__all__`) and the modules `docs/specs/SHAPE_2.md` lists as stable
(`docs/API_STABILITY.md` defers to it): docstrings, type hints under `mypy --strict`, error types,
keyword-only arguments, accidental exports, `py.typed`, API reference docs. Edit paths:
docstrings and annotations in `src/shape/api.py`, `src/shape/__init__.py`, `src/shape/types.py`,
`docs/` API reference pages, and `tests/api/`. Behaviour changes are filed, never made.

No D-xx/T-xx decision, gate or tolerance touched; §11 and §2.3 unedited; nothing escalated;
`$SPINDLE_ROOT` untouched; no workflow edited; no verifier-compared output changed (docstrings,
annotations and docs only).

## Findings

| # | Sev | Where | Finding | Issue | Status |
|---|---|---|---|---|---|
| 1 | medium | `src/shape/contracts/v1.py:84-111` | Contract rule values are not type-checked: `"allow_extra_columns": "false"` and `"unique": "false"` pass silently; `row_count.min: "10"` and `max_null_rate: "0.1"` raise a raw `TypeError`; `max_null_rate: -1` accepted. | #245 | open (outside paths) |
| 2 | medium | `contracts/v1.py:53`, `profile/reference/sources.py:43`, `query/core.py:11`, `spec/model.py:73`, `quality/`, `security`, `types.py:27-32` | `ContractError`, `SourceError`, `ShapeQueryError`, `ModelError`, `GateSchemaError`, `VerifyConfigError`, `SecurityError` and `LogicalType`'s `ValueError` are not `ShapeError`; wrong argument types leak `AttributeError` (`generate(42)`, `plan(42)`, `check(dict, ...)`, `certify(profile, profile)`, `query(p, 123)`, `from_arrow_type("int64")`, `schema_from_arrow(table)`); `query`/`view` on a profile give "a v1 shape must be an object, got Profile". | #249 | open (behaviour) |
| 3 | medium | `src/shape/api.py:28` | `shape.generate` silently ignores `n`/`relationships` (domain, schema), `mode` (schema, profile, evidence) and `scale` (evidence); the exported `shape.Shape` model is accepted by none of `generate`, `plan`, `query`. | #251 | open (behaviour); docstring now states it |
| 4 | medium | `src/shape/api.py`, `src/shape/types.py`, `docs/` | `timeline`, `view`, `query`, `certify`, `plan`, `schema_from_arrow`, `LogicalType`, `FieldType` had no docstring; `generate`'s said the legacy form takes "a Shape model" (it takes an evidence mapping and returns a `(columns, GenerationReport)` tuple) and omitted profile input; return types all `Any`; no API reference page. | #264 | **fixed** 1st commit tests, 2nd fix (below) |
| 5 | medium | `pyproject.toml` `[[tool.mypy.overrides]]` | Stable/public modules are on the `ignore_errors` ratchet: `shape.types`, `shape.artifact.canonical/migrate/secure`, `shape.spec.capabilities/io`, `shape.quality.core/policy`, `shape.drift.policy`, `shape.validation*`. | #257 | `shape.types` made strict-clean (fixed with #264); ratchet line is for the lead (diff below) |
| 6 | low | `src/shape/__init__.py:94`, stable packages | Accidental exports: `shape.Any`, `shape.TYPE_CHECKING`, `shape.importlib`, `shape.annotations` (listed by `dir()`), `shape.api.Any`, `shape.contracts.v1.{dataclass,field,Path,Any}`; `shape.spec`, `shape.contracts(.v1)`, `shape.query`, `shape.registry`, `shape.validation`, `shape.types`, `shape.api` have no `__all__`. | #253 | open (behaviour) |
| 7 | low | `src/shape/_callable.py` | `inspect.signature(shape.diff)` (and `shape.profile`/`shape.query` once the subpackage is imported) is `(*args, **kwargs)` and `help()` shows the package docstring, hiding the §12.2 keyword-only arguments at run time. | #256 | open (outside paths) |
| 8 | low | model, security, artifact, spec, contracts, query, diff, quality, registry, validation | 30+ exported names of stable modules have no docstring (`Evidence`, `Provenance`, `Shape`, `Sensitivity`, `quality.evaluate`, `registry.LocalRegistry`, ...). | #258 | open (outside paths) |
| 9 | low | `src/shape/types.py:14-32` | `LogicalType` accepts any `kind` and `bit_width` (`LogicalType("bogus")`), and raises `ValueError`, not `ShapeTypeError`. | #260 | open (behaviour); docstring states it |

Checked and fine: §12.2 keyword-only `profile(source, *, name=None)` and
`diff(baseline, current, *, thresholds=None)`; `check` violations `{column, rule, expected,
observed}` and `diff` changes `{column, kind, baseline, current, severity}` (+ `score`); `py.typed`
and `_kernel.pyi` in `src/shape` (the maturin wheel and `scripts/build_pure_wheel.py` copy every
file); `__version__` equals the distribution version; `load` reports a corrupt file as
`ArtifactFormatError` (a `ShapeError`); the `TYPE_CHECKING` block names exactly `__all__`;
`from_arrow_type` refuses `null`, `float16`, dictionary and union types with `ShapeTypeError`.

## Fixes

- `AUD-api: regression tests for #264 and #257` (62195dd): `tests/api/test_public_api.py`, 50
  tests; 30 failed before the fix (8 missing docstrings, 19 signatures absent from the then
  missing `docs/API.md`, the reference listing, the return annotations, and `mypy --strict` on
  `types.py`: `import-untyped` on the pyarrow import).
- `AUD-api: fix #264 ...`: docstrings and return annotations in `api.py`, docstrings and the
  strict-ready import in `types.py`, `docs/API.md`. All 50 pass.
- `__init__.py` docstring points at `docs/API.md`; `docs/API_STABILITY.md` links it; CHANGELOG
  (Fixed).

## For the lead

`pyproject.toml` is outside this lane. With `shape.types` strict-clean, this removes it from the
T-12 ratchet (verified: `mypy --config-file <copy without the line>` → "Success: no issues found
in 436 source files"; on 5c91ea5 the same config reports the pyarrow `import-untyped` error):

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ [[tool.mypy.overrides]] (T-12 ratchet)
     "shape.streaming.online",
-    "shape.types",
     "shape.validation",
```

## Commands and results

See the end of this file (updated at finish).
