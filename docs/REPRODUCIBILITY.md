# Reproducibility: the run tuple, the dataset id and replay

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


Every `shape pack run` writes a run manifest (`docs/SCENARIO_PACKS.md`). Two keys of it make a
run comparable and replayable: the reproducibility tuple and the dataset id.

## The reproducibility tuple

`reproducibility` in the manifest holds the seven facts that determine the output of a run:

| Field | Meaning |
|---|---|
| `schema_version` | version of the generation schema document this Shape writes and reads |
| `profile_version` | version of the profile document this Shape writes and reads |
| `seed` | the run's seed |
| `scale` | the scale preset |
| `shape_version` | the Shape version |
| `kernel` | `rust` or `python` (`SHAPE_KERNEL`) |
| `platform` | operating system and machine, such as `linux-x86_64` |

A manifest also records `generators` in `reproducibility`: the generator version of every strategy
and distribution the run used (`{"normal": 1, "sequence": 1}`). It is not one of the seven facts
above (they describe the environment); it names the algorithms. See
[GENERATION_STABILITY.md](GENERATION_STABILITY.md) for the promise that a pinned spec gives the same
dataset id in every 1.x release.

A manifest also records `identifiers` in `reproducibility`: the run's identifier mode, `reserved`
or `realistic` (`docs/GENERATION_STRATEGIES.md`); a manifest without it was a reserved run.

`seed` and `scale` are also top-level keys of the manifest, as before. The schema and profile
versions are those of the build that made the run, so a run made after a format change is told
apart from one made before it.

## The dataset id

`dataset_id` is `sha256:` plus 64 hex digits: a content address of the run's output tables, taken
over **every generated table after chaos was applied**, not only the files that were written.
Equal ids mean equal content. Calling `shape.repro.dataset_id(tables)` gives the id of
any `{name: pyarrow.Table}`.

### The canonical form

The id does not depend on the order of tables, the order of columns, the order of rows, or how a
table is split into chunks, and it does not depend on the kernel (`rust` and `python` give the same
id) or on the platform.

1. **Cell hash.** Each column is hashed value by value with Shape's canonical value hash (the same
   for both kernels, never Python's `hash()`). That hash treats null and NaN alike, so a flag
   (valid, null, NaN) is mixed in. Lists and structs are hashed as their JSON text (sorted keys);
   dictionary columns are read as their values.
2. **Row digest.** The hash of a cell is mixed (splitmix64) with a salt taken from the column's
   name and type, and the mixed cells of a row are added modulo 2**64. Addition makes the row
   independent of column order. It is done with two hash seeds, so a row digest is 128 bits.
3. **Table digest.** The row digests are sorted and hashed with SHA-256 together with the row count.
   Sorting makes the digest independent of row order; duplicate rows count.
4. **Id.** The SHA-256 of a JSON document with sorted keys and no spaces: the id format version (1)
   and, for each table name, the row count, the sorted list of `column:type` pairs and the table
   digest. Types are Arrow type names; `large_string` reads as `string`, `large_binary` as `binary`,
   and a dictionary column as its value type.

What changes the id: a value, a null versus an empty string, a null versus NaN, a column name or
type (an `int64` column and a `float64` column of the same numbers differ), a table name, a row
added, removed or duplicated, and values swapped between rows. What does not: `0.0` versus `-0.0`
(the hash reads them as equal), and the order of anything.

## Replay

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`replay` regenerates the run from the manifest's domain, scale and seed, using the pack (or the
spec) the run used, into a scratch directory that is removed afterwards, and compares the dataset id
of the result with the recorded one. It prints both ids and the differences between the recorded
tuple and this environment (`kernel`, `platform`, `shape_version` ...). Exit codes:

* `0`: the ids match.
* `1`: they do not (read the listed tuple differences first).
* `2`: the run cannot be replayed: the manifest has no dataset id (it predates them), the target is
  another pack or domain than the run's, the run used a spec and a pack was given (or the reverse),
  or the spec file changed since the run (its SHA-256 must equal the manifest's `spec_hash`).

`replay` generates with the generator versions the manifest recorded (`reproducibility.generators`),
over the pins of the spec, so a run made from an unpinned spec on an older 1.x release replays to
the same dataset id on a newer one as long as those versions exist. A recorded version this Shape
does not have exits 2 (`... pins sequence at generator version 3; this Shape has version 1
(upgrade Shape)`). See [GENERATION_STABILITY.md](GENERATION_STABILITY.md).

`replay` generates with the recorded identifier mode (`reproducibility.identifiers`; a manifest
without it replays as `reserved`, and a value other than `reserved` or `realistic` exits 2).

A replay on another platform or kernel is a legitimate check: it matches when the generator gives
the same tables there, and a difference in the tuple does not by itself fail a match.

## File hashes

The dataset id is about content. For the bytes of the files, `shape.repro.file_hashes(run_dir)`
returns the SHA-256 of every file a run wrote under `run_dir` (the run manifest left out), keyed by
its relative path with `/`. The CSV, JSON Lines and SQL files are byte-identical for the same spec,
seed, scale, writer options and Shape version on every platform; the manifest records the writer
format versions in `reproducibility.writers`. See "Byte-identical files" in
[GENERATION_STABILITY.md](GENERATION_STABILITY.md).

## Compatibility

The manifest declares `format: "shape-run-manifest"` and `version: 1`. A manifest without them (a
run made before this change) still loads, with an empty `reproducibility` and `dataset_id`. A
manifest whose version is newer than this Shape's is refused with a message that names the Shape
release that reads it (`ManifestVersionError`, which is also `shape.compat.UnsupportedVersionError`).
The declaration follows `docs/specs/STATE_AND_COMPATIBILITY.md`: `format`, `version`,
`shape_version` and `min_shape_version`; fields this Shape does not know are kept when the manifest
is written back.
A manifest without `reproducibility.generators` (made before generator versions) loads, and replays
with the spec's pins and the latest versions; the field is additive, so `version` stays 1.

A manifest without `reproducibility.identifiers` (W8-06) loads, reads as `reserved`, is written
back without it and replays as a reserved run; the field is additive, so `version` stays 1.
