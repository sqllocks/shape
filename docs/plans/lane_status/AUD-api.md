# AUD-api — audit of the public Python API surface — status

Branch `lane/AUD-api`, from `build/main-plan` at 5c91ea5 (INT-15 merged). Area: everything
`import shape` exports (`shape.__all__`) and the modules `docs/specs/SHAPE_2.md` lists as stable
(`docs/API_STABILITY.md` defers to it): docstrings, type hints under `mypy --strict`, error types,
keyword-only arguments, accidental exports, `py.typed`, API reference docs. Edit paths:
docstrings and annotations in `src/shape/api.py`, `src/shape/__init__.py`, `src/shape/types.py`,
`docs/` API reference pages, and `tests/api/`. Behaviour changes are filed, never made.

No D-xx/T-xx decision, gate or tolerance touched; §11 and §2.3 unedited; nothing escalated;
`$REFENGINE_ROOT` untouched; no workflow edited; no verifier-compared output changed (docstrings,
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
- `AUD-api: fix #264 public API docstrings, return types and docs/API.md` (ff89a44): docstrings and return annotations in `api.py`, docstrings and the
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

- `AUD-api: tests for every dispatch path of shape.api` (3ff61e5): schema dict, `GenSchema`,
  profile documents, unknown domain, query/view on a model and on a profile; `shape.api` line
  coverage from `tests/api` alone is 100%.

## Left open

Every behaviour defect (#245, #249, #251, #253, #256, #258, #260) is outside this lane's paths or
is a behaviour change the lane may not make; #257 needs the one-line `pyproject.toml` change above.
No existing test pins a defect; no test was skipped, xfailed or changed in expectation.

## Commands and results (HEAD 3ff61e5 plus this file; Python 3.11.15)

Environment: `pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -r
tests/demo/fabric/requirements.txt` (as CI). `$REFENGINE_ROOT` is not present in this session, so
`tests/demo/content` and the verifiers were not run; none of this lane's changes touch output
bytes (docstrings, annotations, docs, tests).

- `ruff check src tests plugins benchmarks/vs_refengine`: all checks passed.
- `ruff format --check src tests plugins benchmarks/vs_refengine`: 1088 files already formatted.
- `mypy`: Success, no issues found in 436 source files (also with `shape.types` off the ratchet).
- `python scripts/check_user_facing.py`: clean.
- `pytest tests/api`: 55 passed (both kernels).
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric
  --ignore=tests/demo/content` (heavy included): 7 failed, 6878 passed, 2 skipped.
- `SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"` (same ignores):
  6 failed, 6837 passed, 2 skipped. The first python-kernel run with `heavy` was stopped after
  30 minutes inside `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  (20M rows profiled by the pure-Python kernel; CI runs `heavy` with the default Rust kernel, where
  it passed above).

The failures are not this lane's:
- 5 fail identically on the unmodified base 5c91ea5 (checked in a clean worktree):
  `tests/demo_cmd/test_notebook_and_outputs.py` (2), `tests/iss_gaps/test_landing_and_batches.py::
  test_file_sinks_take_path_template_and_batch_date`, `tests/kernel/test_hashing.py` float16 and
  1/1.0 (rust; 1 of them in python). They follow from the session's pyarrow 19.0.1, which the Fabric
  demo requirements install (float16 and partition read-back; compare #76).
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`:
  order-dependent (#77; the Fabric demo packages are installed), passes alone on HEAD.
- `tests/validation/test_fuzz_smoke.py::test_smoke_run_has_no_findings`: a 5 s per-case timeout
  (`pack-yaml` iteration 12) under full-suite load; passes alone on HEAD in both kernels.
