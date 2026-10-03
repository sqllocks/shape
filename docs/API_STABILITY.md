# API Stability — Shape by SQLLocks

Shape 1.0 freezes the versioned specification and the public interfaces identified in `docs/specs/SHAPE_2.md`.

Stable interfaces follow semantic-versioning compatibility through the 1.x line. Additive optional behavior is allowed; silent semantic changes are not. Experimental/internal modules are not covered unless explicitly promoted to Stable.

Artifact readers fail closed on unknown mandatory capabilities and tolerate unknown optional extensions only when they can be ignored safely.

Plugin API v1 has its own promise, with the per-group rules and the deprecation process: `docs/plugins/stability.md`.

The generation spec format (its JSON Schema, the stability promise within 1.x and the edit API) is described in `docs/GENERATION_SPEC.md`.

## Stable Python modules

These modules are Stable interfaces: they follow the compatibility rules above through the
whole 1.x line. Each lists its public surface in `__all__`; a name that starts with `_`, a
module's `main` and anything not in `__all__` is internal and not covered.

| Module | Exported names |
|---|---|
| `shape.generation.spec_edit` | `Position`, `SpecDocument`, `SpecError`, `SpecProblem`, `validate_text` |
| `shape.generation.spec_schema` | `build_schema`, `published_schema`, `render`, `strategy_names` |

The generation spec format itself is described in `docs/GENERATION_SPEC.md`.

### What the promise covers

- Each function's and method's parameter names, order, kinds (positional, keyword-only, ...) and
  defaults, and its return type (including the constructor of `SpecDocument` and `SpecError`).
- The members of `SpecDocument` and `SpecProblem`, with their kinds (method, classmethod,
  property).
- Dataclass field names, order, types and frozen-ness (`SpecProblem`).
- Exception classes and their base classes (`SpecError` is a `ShapeSchemaError`).
- The documented behaviours below.

### What it does not cover

- Internal names (anything starting with `_`, and `main`).
- The text of error messages, and the exact wording of `SpecProblem.message`. Match on
  `SpecProblem.level` and `SpecProblem.pointer`, never on the message.

### Documented behaviours

| Behaviour | Test that enforces it |
|---|---|
| A spec loaded from text and not changed is written back byte for byte. | `tests/api/test_stable_api_promise.py::test_unchanged_spec_is_written_back_byte_for_byte` |
| An edit keeps unknown keys (`x-*`, `$comment`, fields a newer 1.x adds) and the existing key order. | `tests/api/test_stable_api_promise.py::test_an_edit_keeps_unknown_keys_and_key_order` |
| Every `SpecProblem` has a JSON Pointer, and a line and column for a spec loaded from text. | `tests/api/test_stable_api_promise.py::test_every_problem_has_a_pointer_and_text_problems_have_a_place` |
| `published_schema()` equals the shipped `generation-spec-v1.schema.json`. | `tests/api/test_stable_api_promise.py::test_published_schema_is_the_shipped_file` |

A test fails when a test id in this table does not exist
(`tests/api/test_stable_api_promise.py::test_listed_tests_exist`).

### What counts as a break

Additive changes pass `python scripts/stable_api_compat.py --check`: a new exported name, a
new method, a new keyword parameter with a default after the existing ones, a new optional
dataclass field with a default at the end.

These fail it: a removed or renamed name, a removed, renamed or reordered parameter, a new
required parameter, a changed default, a changed parameter kind, a changed return annotation, a
removed or retyped field, a dataclass that stops being frozen, a changed exception base class.
A break needs a new major version. After an additive change, refresh the baseline with
`python scripts/stable_api_compat.py --write` (see `docs/CONTRIBUTING.md`).

### Deprecation

The window of plugin API v1 applies (`docs/plugins/stability.md`): nothing is removed in 1.x. A
deprecated member keeps working and raises a `DeprecationWarning` until the next major version.
