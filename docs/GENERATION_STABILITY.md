# Generation stability: generator versions and pinned fixtures

Teams keep generated datasets as test fixtures and expect the same spec and seed to give the same
data after they upgrade Shape. This page says when that holds, how to ask for it, and what it does
not cover.

## The promise

> The same generation spec, with the same generator versions pinned, the same seed and the same
> scale, gives the same **dataset id** (`docs/REPRODUCIBILITY.md`) in every 1.x release, on both
> kernels (`SHAPE_KERNEL=rust` and `python`) and on every supported platform.

The promise is about *content*, through the dataset id. Within one Shape version the text writers
also promise *bytes*: see "Byte-identical files".

It is enforced: `tests/generation/pinned/` holds a pinned spec for every built-in strategy and
every distribution family, with the committed dataset id of seed 42 at 200 rows per table, and
`tests/generation/test_w1_15_pinned_fixtures.py` regenerates each and compares the ids in both
kernel modes in the regular test suite.

## Generator versions

Every strategy (`shape.strategies`) and every distribution (`shape.distributions`, and the
families behind `strategy: distribution`) has an integer **generator version**, which names the
algorithm that maps parameters, seed and row index to values. All built-ins are at version 1,
except `faker` at version 2 (W8-06: the `faker` package's e-mail, URL, host and phone providers are
reserved by default; version 1 returns the package's values as before).

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
- **Bytes across versions.** File bytes are promised only within one Shape version, and only for
  the CSV, TSV, JSON Lines and SQL writers (see "Byte-identical files" below). Parquet and the
  other binary formats are not byte-stable (writer versions, compression, metadata). Across
  versions the dataset id is the content address; compare it, not file hashes.
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

## Byte-identical files

> For the same schema (or spec), seed, scale, writer options and Shape version, the CSV (and TSV),
> JSON Lines and SQL (every dialect: `tsql`, `tsql-fabric-warehouse`, `postgres`, `mysql`) writers
> produce identical bytes on Linux, Windows and macOS (x86-64, and arm64 where wheels exist), with
> `SHAPE_KERNEL=rust` and `SHAPE_KERNEL=python`, independent of the locale, the local time zone,
> `PYTHONHASHSEED`, the thread count (`SHAPE_THREADS`, writer threads) and the order of dictionary
> or file-system iteration.

So a generated file can be published with its SHA-256, diffed byte for byte in CI, or rebuilt on
another machine with the same hash.

### The same values on every machine

Both promises need the generated *values* to be the same bits on every machine, not only the
writers. numpy's float64 `log`, `exp`, `log1p`, `power` and `cos` are not: numpy picks AVX-512
routines on some x86-64 CPUs and the C library elsewhere, and the C libraries of Linux, macOS and
Windows round differently in the last bit (#768). So every logarithm, exponential, power and
cosine a built-in strategy or distribution family uses to make a value (the `normal` draw itself,
`log_normal`, `exponential`, `gamma`, `beta`, `weibull`, `pareto`, `power_law_cutoff`, the tables
of `zipf`, `poisson` and `negative_binomial`, `geometric`, `empirical`'s interpolation, fan-out
weights, skewed foreign keys and lead times) is computed with `shape.kernel.pmath`: fdlibm's
algorithms written with IEEE basic operations only, the same in the native kernel and in its numpy
twin (`docs/GENERATION_KERNEL.md`, "Portable math"). The values follow the same distributions as
before; only the last bits of some values changed once, in W8-04b, together with the pinned ids
and the golden byte corpus.

`tests/generation/test_w8_04b_cross_cpu.py` regenerates the pinned fixtures and the golden byte
corpus with numpy's AVX-512 code switched off, and checks that generating them calls no
machine-dependent numpy or `math` function; CI runs the pinned fixtures and the corpus on Linux,
macOS (arm64) and Windows.

### What the writers do

| Property | CSV / TSV | JSON Lines | SQL |
|---|---|---|---|
| Line end | `\n` (Arrow's writer) | `\n` (`newline="\n"`) | `\n` (`newline="\n"`) |
| Encoding | UTF-8, no BOM | UTF-8, no BOM, non-ASCII kept | UTF-8, no BOM; `N'...'` literals in `tsql` |
| Float | shortest round-trip digits (Arrow: `1e-7`, `1`) | shortest round-trip digits (`repr`) | shortest round-trip digits (`repr`); NaN and infinity are `NULL` |
| Decimal | exact digits | exact digits, as a string | exact digits (`format(v, "f")`) |
| Timestamp | ISO 8601; a zoned column in its zone with the offset | ISO 8601; a zoned value with its offset | the wall-clock time; a zoned value in its zone |

"Shortest round-trip digits" is the shortest decimal that reads back as the same double: the
digits of Python's `repr`. CSV spells the exponent and whole numbers as Arrow does (`1e-7`, `1`,
`-0`), JSON Lines and SQL as `repr` does (`1e-07`, `1.0`, `-0.0`). No value is formatted through
the locale and no timestamp through the local time zone; a naive timestamp is written as it is. A
writer never iterates a set or a directory listing; the tables of a run are written to separate
files, so the order in which writer threads finish does not matter.

Each writer that makes this promise declares an integer **`format_version`** (`csv`, `tsv` and
`jsonl` are at 1; `sql` is at 2, since a PostgreSQL literal with a backslash became an `E'...'`
literal, #285). A change to a writer that changes bytes for the same input raises it and is listed
in `CHANGELOG.md`.

### The version guard

A run manifest records, in `reproducibility.writers`, the `format_version` of every writer the run
used that declares one (`{"csv": 1}`; a Parquet run records `{}`). The field is additive, so the
manifest `version` stays 1, and a manifest without it still loads.

`shape.repro.file_hashes(run_dir) -> dict` returns the SHA-256 (hex) of every file under
`run_dir`, keyed by its path relative to `run_dir` with `/`, in sorted order. The run manifest
(`<run_id>_manifest.json`) is left out, because it records when the run happened. Store the result
and compare it with a later run:

```python
from shape.repro import file_hashes

before = file_hashes("out/run1")
after = file_hashes("out/run2")
assert before == after
```

It raises `NotADirectoryError` when `run_dir` is not a directory.

### Frozen reference pools

The value lists built-in strategies draw from (`src/shape/builtins/strategies/pools/*.txt` and the
locale lists in `src/shape/builtins/strategies/locales/`) are listed in
`src/shape/builtins/strategies/pools/MANIFEST.json`:

```json
{
  "format": "shape-pool-manifest",
  "version": 1,
  "files": {
    "pools/first_names.txt": {
      "sha256": "...",
      "bytes": 1234,
      "drawn_by": {"faker": 1, "locale": 1, "native": 1}
    }
  }
}
```

A key is the file's path relative to `src/shape/builtins/strategies/`. `drawn_by` names every
strategy that reads the file and the generator version whose output the file's bytes belong to.
`tests/generation/test_w8_04_byte_identity.py` fails when a file's bytes differ from the manifest,
when a file is shipped but not listed (or listed but not shipped), and when a strategy in
`drawn_by` is at another generator version. A manifest with a higher `version` than this Shape
reads is refused; unknown fields are ignored.

**Changing a pool** changes the output of every strategy that draws from it, so in the same change:

1. raise the generator version of every strategy in the file's `drawn_by`, keeping the old version
   working on the old values (see "Raising a version" above);
2. run `python -m shape.builtins.strategies.pool_manifest --write`, which records the new digests
   and the current versions (a new file needs its `drawn_by` filled in by hand);
3. rewrite the golden byte corpus (below) and note the change in `CHANGELOG.md`.

`python -m shape.builtins.strategies.pool_manifest` with no option checks the pools and exits 1
with one line per difference. The masking transform (`shape mask`) also reads the pools; its output
follows the same manifest.

### The golden byte corpus

`tests/generation/golden_bytes/` holds the SHA-256 of every file the writers produce for a fixed set
of specs at seed 42 and 100 rows per table: the pinned spec of every built-in strategy and
distribution family (`tests/generation/pinned/`), every `native` provider (so every reference pool)
and a spec of every column type with the values a writer must escape or format
(`golden_bytes/specs/`). Each table is written as CSV, TSV, JSON Lines and SQL in every dialect (a
table with a struct or list column has no CSV or TSV form).

- `hashes.json` (`format: "shape-golden-bytes"`, integer `version`, currently 1) holds the
  `seed`, the `rows`, the `shape_version` that wrote it and the digest of each file
  (`<spec>/<table>.<csv|tsv|jsonl|<dialect>.sql>`).
- `lines.json` (`format: "shape-golden-bytes-lines"`) holds a short digest of every line, so a
  difference is reported as the first line that differs, with the line written now.

The tests regenerate the corpus in both kernel modes, and once more in a child process with a
non-UTF-8 locale (`LC_ALL=C` with UTF-8 mode off on Linux and macOS; the ANSI code page on
Windows), `TZ=Pacific/Chatham`, another `PYTHONHASHSEED` and another thread count. They run in the
regular test suite, which CI runs on Linux, Windows and macOS.

```bash
python scripts/golden_bytes.py            # compare a fresh run with the corpus (exit 1 if not)
python scripts/golden_bytes.py --update   # rewrite the corpus
```

SQL files name the Shape version in their first comment line, so a release rewrites the corpus
(`--update`) as one of its steps; the test says so when the corpus was written by another version.

### What is not covered

- **Binary formats:** Parquet, Delta, Excel, Arrow IPC, Avro and any other binary format.
- **Output across different Shape versions:** two Shape versions may write different bytes; only
  the content promise (the dataset id with pinned generator versions, above) holds across versions.
- **Plugin writers that do not opt in:** a plugin sink is covered only if it declares
  `format_version` and keeps the rules above.
- **Different dependency versions for CSV:** CSV and TSV values are formatted by Arrow's CSV writer,
  so the promise is for the pyarrow release the corpus is checked with in CI; a pyarrow release
  that changes those bytes fails the corpus test there.
- **Named time zones:** a timestamp column whose type names a zone other than UTC is written in
  that zone's local time, which depends on the IANA time zone database installed. Shape's own
  strategies write naive timestamps.
- **Reference data from other packages:** a dataset a domain or plugin ships (such as the US places
  of the `locale` strategy) is that package's to keep stable.
- The run manifest itself (it records times) and the `summary` output.
- **`formula` expressions with `np_log`, `np_exp` or a power of floats:** a formula is evaluated
  with numpy and equals numpy's own evaluation of the expression on the same machine
  (`docs/GENERATION_STRATEGIES.md`), so these functions keep numpy's last bits, which may differ
  between machines. Arithmetic, comparisons, `np_sqrt`, `np_round`, `np_floor` and the other
  functions are exact everywhere.
- **Fitting a spec to a profile** (`shape generate --from X.shape`, `shape learn`): the fitted
  parameters (correlations, calibrated copula coefficients) come from numpy's linear algebra and
  random generators and may differ in the last bits between machines. Save the fitted spec and
  generate from it to get the same bytes everywhere.
