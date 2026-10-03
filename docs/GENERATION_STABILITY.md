# Generation stability: generator versions and pinned fixtures

Teams keep generated datasets as test fixtures and expect the same spec and seed to give the same
data after they upgrade Shape. This page says when that holds, how to ask for it, and what it does
not cover.

## The promise

> The same generation spec, with the same generator versions pinned, the same seed and the same
> scale, gives the same **dataset id** (`docs/REPRODUCIBILITY.md`) in every 1.x release, on both
> kernels (`SHAPE_KERNEL=rust` and `python`) and on every supported platform.

The promise is about *content*, through the dataset id, not about bytes of files.

It is enforced: `tests/generation/pinned/` holds a pinned spec for every built-in strategy and
every distribution family, with the committed dataset id of seed 42 at 200 rows per table, and
`tests/generation/test_w1_15_pinned_fixtures.py` regenerates each and compares the ids in both
kernel modes in the regular test suite.

## Generator versions

Every strategy (`shape.strategies`) and every distribution (`shape.distributions`, and the
families behind `strategy: distribution`) has an integer **generator version**, which names the
algorithm that maps parameters, seed and row index to values. All built-ins are at version 1.

- A change that alters the output for the same inputs **must raise the version**. The previous
  version stays implemented and selectable for the whole 1.x line.
- A change that does not alter output (a speed-up, a refactor, a bug fix in an error message) does
  not.
- A new strategy starts at version 1.

A plugin strategy or distribution may declare `generator_version` as an optional attribute (a
missing one means 1). It is additive under the plugin API v1 promise: the Protocols do not require
it, and `shape.plugins.kit` only checks that a declared one is an integer of at least 1. A plugin
that declares more than one version lets the engine pick one by providing:

| Object | Selector |
|---|---|
| strategy | `generate_versioned(spec, ctx, version)` |
| distribution | `sample_versioned(params, ctx, version)` |

`generate` / `sample` then give the latest version. An object without a selector has the one
version it declares, and a pin to another is an error. A plugin that raises its version must keep
the older ones working in the same plugin release, or its users' pins stop working.

A strategy and a distribution may share a name (`uniform`, `normal`). A pin of that name applies to
both and must be valid for both; the built-ins that share a name always have the same version.

## Pinning

A spec pins versions in an optional top-level `generators` map, `{"<name>": <version>}`:

```json
{
  "schema_version": 1,
  "model": {"name": "shop", "seed": 42},
  "tables": {"...": "..."},
  "generators": {"sequence": 1, "weighted_enum": 1, "normal": 1, "distribution": 1}
}
```

`shape generate`, `shape pack run` and the Python API (`Engine`) run every pinned name at its pinned
version and every other name at its latest version. `Engine(schema, generators={...})` replaces the
spec's pins for one run. The map is part of the published JSON Schema
(`generation-spec-v1.schema.json`, additive under `docs/GENERATION_SPEC.md`) and survives every
`SpecDocument` edit (`doc.generators`, `set_generator_version`, `remove_generator_version`).

### `shape pin`

```bash
shape pin shop.json                 # write the current version of everything the spec uses
shape pin shop.json -o pinned.json  # ... to another file (SPEC is left alone)
shape pin shop.json --json          # the result as JSON
shape pin shop.json --check         # exit 1 and list the names that are not pinned
```

`shape pin` adds the names the spec uses and does not pin yet, at their current version. A pin that
is already there is never moved, because moving it would change the data. The spec is edited
through the edit API, so unknown fields, `x-*` keys and `$comment` stay, and a spec that has
nothing to add is not rewritten. `--check` writes nothing.

| Exit code | Meaning |
|---|---|
| 0 | pinned (or all names were already pinned, for `--check`) |
| 1 | `--check` only: the spec uses names it does not pin (they are listed) |
| 2 | the spec is not valid, cannot be read, or pins a version this Shape does not have |

The names are the strategy names of the generators (`sequence`, `weighted_enum`, ...) and, for a
`distribution` strategy, the family it names (`normal`; `uniform` when it names none). Other
strategies that take a `distribution` key have their own algorithm, which their own version
covers. A name with no implementation here (a plugin that is not installed) cannot be checked and
is left out.

### Errors and warnings

- A pin to a version this Shape does not have is an error, exit code 2:
  `shape: error: shop.json pins weighted_enum at generator version 3; this Shape has versions 1 to
  2 (upgrade Shape)`. The Python API raises `shape.generation.versions.GeneratorPinError` (a
  `ValueError`). A pin below the lowest version (an implementation that no longer has it) says
  "re-pin the spec" instead.
- A pin to a name the spec does not use is a **warning**, not an error: `shape: warning: shop.json
  pins zipf, which it does not use` (`GeneratorPinWarning` in Python; a `warning` issue in
  `GenSchema.validate()` and in `SpecDocument.validate()`).

## The run manifest and replay

A run manifest records, in `reproducibility.generators`, the version of every strategy and
distribution the run used (the pin, or the latest version for a name that was not pinned):

```json
"reproducibility": { "seed": 42, "...": "...", "generators": {"normal": 1, "sequence": 1} }
```

`shape pack replay` regenerates with the **recorded** versions, over the spec's pins. So a run made
from an unpinned spec on an older 1.x release replays to the same dataset id on a newer one, as
long as those versions still exist. If the manifest records a version this Shape does not have,
replay exits 2 with the message above. A manifest without `generators` (made before this field)
loads and replays with the spec's pins and the latest versions; the manifest `version` stays 1
because the field is additive.

## What is not covered

- **Unpinned specs across versions.** Without a pin, a name runs at its latest version, and a new
  release may have a new latest. Pin the spec (`shape pin`) for fixtures.
- **Plugin strategies that do not declare a version.** They are version 1 by definition, and Shape
  cannot tell if their output changed. Pin a plugin's version only if the plugin keeps its old
  versions.
- **Bytes.** CSV, Parquet and the other file formats are not byte-stable (writer versions,
  compression, metadata). The dataset id is the content address; compare it, not file hashes.
- **Reference data.** A strategy that reads a reference dataset (`reference_data`, `bootstrap`,
  `record_sample`, ...) gives the same ids only while the dataset has the same content. A dataset a
  domain or plugin ships is that package's to keep stable.
- Specs that use a feature a release no longer has after a major release (see below).

## Retiring a version

A generator version is retired only in a **new major release**, and only after a release that
announces it: the release before the one that removes a version lists it as deprecated in its
changelog, and a spec that pins it gets a warning. Until then every version that existed in 1.x
stays implemented and selectable.

## Raising a version (for maintainers)

1. Keep the old algorithm. Add `generate_versioned` (or `sample_versioned`) to the strategy and
   make the new algorithm version N+1; set `generator_version = N + 1`.
2. Run `python tests/generation/pinned_support.py --write`. It adds the id of the new version to
   `tests/generation/pinned/expected.json` and never changes an id that is there. The old id must
   still match: that is the proof the old version is still implemented.
3. Note the new version in `CHANGELOG.md`.

If you change a strategy's output and do not raise its version,
`test_the_dataset_id_of_every_pinned_spec_is_unchanged` fails. If you raise the version and do not
add the new id, `test_every_current_version_has_an_expected_id` fails.

`expected.json` is a persisted format: `format: "generator-pinned-ids"`, `version: 1`
(`test_expected_is_a_versioned_format`).
