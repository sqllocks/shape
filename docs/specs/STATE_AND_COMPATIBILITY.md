# State and compatibility policy

Shape writes files that outlive the release that wrote them: `.shape` artifacts, safe profiles,
models, generation schemas and specs, scenario packs, registries, run manifests, contracts and
signatures. This is the one policy for all of them. It is enforced by tests (see
[the checklist](#11-changing-a-persisted-format-the-checklist)); where a sentence here has no test
behind it, it says so.

## 1. What every persisted file declares

Every persisted file declares four things, under the same key names whatever the kind:

| Key | Meaning |
|---|---|
| `format` | the kind of file, a fixed string such as `shape-safe-profile` |
| `version` | the format version, an integer of at least 1 |
| `shape_version` | the Shape release that wrote the file |
| `min_shape_version` | the first Shape release that reads this format version |

Older releases named the version differently (`format_version`, `schema_version`,
`pack_version`) or did not declare one. Readers keep accepting those names, and in the 1.x series
writers also write the old name beside `version` with the same value, so a release from before
the unified keys still reads the file. The keys must agree: a file whose `version` and old key
differ is refused, as is a version that is not an integer, or is below 1.

A file that declares no version has the **implicit version** of its kind (always 1: those kinds
never had a second version before they were declared).

| Kind | File | `format` | Version | Old key name(s) still read | Notes |
|---|---|---|---|---|---|
| `artifact` | `.shape` archive, `manifest.json` | `shape` | 2 | `format_version` | the manifest declares; version 1 holds a v1 capture and is migrated on read |
| `profile-artifact` | `.shape` archive with `kind: profile` | `shape` | 2 | `format_version` | version 2 records `capture` (`safe` or `full`, and `k`) and, for a safe capture, the `redaction_manifest`; a version 1 file has no `capture` and reads as `full` |
| `model` | `shape.json` in an artifact; a standalone model JSON | `shape-model` | 2 | `schema_version` | see below |
| `safe-profile` | `*.safe.json` | `shape-safe-profile` | 1 | `schema_version` | |
| `generation-schema` | generation schema JSON | `shape-generation-schema` | 1 | `schema_version` | see below |
| `generation-spec` | GSL spec YAML | `shape-generation-spec` | 1 | none: it always used `version` | authored by users |
| `scenario-pack` | pack YAML | `shape-scenario-pack` | 1 | `pack_version` | authored by users |
| `registry-layout` | `layout.json` at the root of a registry | `shape-registry` | 1 | none: no marker before | opening a registry writes it |
| `profile-registry-layout` | `_layout.json` at the root of a profile registry | `shape-profile-registry` | 1 | none: no marker before | |
| `run-manifest` | run manifest JSON | `shape-run-manifest` | 1 | none | carries `reproducibility` and `dataset_id`; a manifest written before they existed loads with both empty; a newer version raises `ManifestVersionError` (also an `UnsupportedVersionError`) |
| `contract` | contract JSON read by `shape check` | `shape-contract` | 1 | none | authored by users |
| `contract-model` | Shape-as-Code contract file | `shape-contract-model` | 1 | none: `version` was always the format version | |
| `signature` | `manifest.sig` in a signed artifact | `shape-signature` | 1 | none | also names its `algorithm` |
| `migration-receipt` | `DST.receipt.json` written by `shape migrate` | `shape-migration-receipt` | 1 | none | new |
| `gate-schema` | gate schema JSON | `shape-gates` | 1 | none: it already used `version` | |
| `verify-config` | verify configuration JSON | `shape-verify-config` | 1 | none: it already used `version` | authored by users |
| `profile-export` | `shape profile export` JSON | `shape-profile` | 1 | `format_version` | |
| `vault` | `*.shapevault`, the encrypted value vault | `shape-vault` | 1 | none | new; the profile artifact's manifest gains the additive `vault` field (`vault_id`, `sha256`), see `docs/VAULT.md` |
| `vault-policy` | vault policy JSON | `shape-vault-policy` | 1 | none | authored by users |

Three kinds do not follow the table to the letter, for reasons that are part of the policy:

- **`model`.** The model inside an artifact is content-addressed: its content id is the SHA-256 of
  its bytes. Adding `format` and `version` to it would change every content id, which is a format
  version bump, so the model keeps declaring its version as `schema_version` and the unified keys
  are in the artifact's manifest instead. A standalone model JSON declares the same way.
- **`generation-schema`.** The reader accepts the unified keys and `x_` fields. The writer
  (`shape from-ddl`, the schema dumps) still writes the file without them, because the parity
  harnesses under `benchmarks/` pin that file equal to the baseline's, and an owner
  decision is needed before that changes. Until then its version is `schema_version`.
- **`contract-model`.** `ShapeContract.version` is the format version of the file (it was only
  ever checked to be at least 1). A contract's own revision belongs in `metadata`.

The table is written from `shape.compat.KINDS`; `shape.compat.stamp` writes the declaration and
`shape.compat.check_readable` is the one place a reader decides.

## 2. The read-old promise

**Every 1.x and later release reads every format version ever released.** A format version is
dropped only in a new major release, announced a release cycle ahead (see
[Deprecation](#9-deprecation)), and only if an offline `shape-migrate` path still reads it.

The promise is checked, not hoped for: the [time-capsule corpus](#10-the-time-capsule-corpus)
holds a file written by the release that introduced each format version, and a test loads every
one and compares what the reader returns with what it returned when the file was written. A
release that cannot read a file in the corpus does not pass CI.

## 3. A file from a newer release

A file whose version is higher than this release reads fails with an `UnsupportedVersionError`
(a `ValueError`, and also the error type of the reader that raised it) whose message names the
first release that reads it:

```
unsupported safe profile version 2: this Shape reads up to version 1; it needs Shape 1.6.0 or newer (upgrade Shape)
```

The release comes from the file's `min_shape_version`, or else its `shape_version`; a file that
carries neither says "it was written by a newer Shape release". Only a plain release number is
ever echoed, so a hostile file cannot put text in the message. When the version keys of a file
disagree and the highest is newer than this release reads, the file is refused as newer: it is
never read as the older version.

Scenario packs and generation specs are authored, and their validators report the same message
(`Unsupported pack_version 2 ...`, `Unsupported spec version 2 ...`) so that every problem of a
file is listed together.

## 4. Migrations

`shape migrate SRC DST` (also installed as `shape-migrate`) writes a migrated copy of a `.shape`
artifact or of a JSON file of any kind above, offline.

- **Never in place.** The result is a new file. The destination must not exist, and is never
  overwritten, not through a link either. The original is kept byte for byte.
- **Recorded.** The result carries `migrated_from` (the source's version) and
  `source_content_id` (an artifact's manifest, a document's top level; a model, whose body cannot
  carry them, has them in the receipt). `DST.receipt.json` records the steps, both files by
  SHA-256, both versions and content ids and the signature of the source.
- **Dry run.** `--dry-run` prints the plan (kind, versions, steps, the content ids) and writes
  nothing.
- **Downgrades are refused.** `--to` below the file's version fails, and so does a version this
  release does not write.
- **Round trip.** Before anything is published the result is read back: its content id must be
  the one the plan predicted, and migrating it again must be a no-op. A failed check leaves no
  file. A file that is already current migrates to nothing and no file is written.
- **Renaming keys changes no content.** For a file that only needs the unified keys the content
  id is unchanged: it is computed without the declaration, the old key names and the migration
  record.

Exit code 0 is a migration, a no-op or a dry run; 2 is a refusal or bad input.

## 5. Unknown fields

A newer release may add an optional field without a new version. A reader of this release:

- **ignores** a field it does not know, and never fails on it;
- **keeps** it when it rewrites the file: artifact manifests (also through `shape migrate`), safe
  profiles at the top, table and column levels, run manifests, contract models, generation
  schemas (`x_` fields) and the layout markers keep what they did not understand;
- in **strict mode** ([Deprecation](#9-deprecation)) refuses it, naming the field.

A kind that users author by hand keeps its typo protection: a contract, a verify configuration
and a generation schema refuse an unknown field unless its name starts with `x_`, which is the
extension prefix. Fields starting with `x_` are never reported. A scenario pack or spec lists the
keys it did not use in `extra_keys` and the validator warns.

Keeping a field never launders a value: the leak scanner for safe profiles reads every field of
the file, including the ones this release only carries.

## 6. Signed artifacts and migrations

A signature covers the exact bytes of `manifest.json` (see [Signing](../SIGNING.md)). A migrated
artifact has a different manifest, so the old signature cannot be carried over and is not faked.

- **The original signed file is kept** as evidence: `shape migrate` never touches it, and it
  verifies under its original key for as long as that key is trusted.
- **A signed migration receipt.** A signed source needs `--sign-key`: the receipt is signed with
  that key (Ed25519, over a domain prefix and the canonical receipt without its signature) and so
  is the new artifact. The receipt records the source's signature (`algorithm`, `key_id`, and
  whether it was `verified`: `--verify PUBKEY` checks the source first and stops if it fails).
  `--unsigned-receipt` accepts an unsigned receipt, said out loud; an unsigned source needs
  neither. `shape.migrate.verify_receipt(path, public_key, source=, result=)` checks a receipt and
  the two files it names.
- **The algorithm id is in every signature.** `manifest.sig` and a receipt signature are objects
  with `algorithm` (today `Ed25519`), `key_id` and `signature`; a reader refuses an algorithm it
  does not know, and a new algorithm is a new value there, not a new file layout.
- **Key rotation and retired keys.** A key is rotated by generating a new pair and signing from
  then on with it. Keep the public half of a retired key in your trust list for as long as the
  artifacts it signed matter: they stay valid under it, and verify with it, because the
  signature names its `key_id` and nothing about a signature expires. A retired key is never
  asked to sign again. Verifying with the wrong key fails with "signed by a different key" and
  names neither key's secret. [Signing](../SIGNING.md) has the commands.

## 7. Canonical forms and content ids

Two forms are canonical, and both are specified byte for byte so that another language can write
and check them. [`vectors/state_vectors.json`](vectors/state_vectors.json) holds the inputs and the
exact outputs; an implementation is conformant when it reproduces every one and fails every
error vector. The vectors are checked against a second implementation written only from this
section, with the standard library, in `tests/state/test_vectors.py`.

**Artifact manifest** (`manifest.json`, and what the signature covers): a JSON object written with
the keys sorted by Unicode code point (not UTF-16 order), no whitespace, UTF-8 without escaping
non-ASCII text, no `NaN` or infinity.

**Artifact body and documents** (the codec): the same writer, plus tagged forms for what JSON
cannot hold:

- a non-finite float is `{"$float": "nan" | "inf" | "-inf"}`;
- a tuple is `{"$tuple": [...]}`;
- a mapping with a key that starts with `$` is `{"$dict": [[key, value], ...]}`, in insertion
  order;
- a tag key may only appear alone in its object; bare `NaN` and `Infinity` are refused.

Text is escaped with `\"`, `\\`, `\n`, `\r`, `\t`, `\b`, `\f`; any other control character below
U+0020 as `\u00xx` in lower case; everything else raw. Integers are written exactly, whatever
their size. A float is written as the shortest decimal that reads back to the same IEEE 754
binary64 value, in Python's `repr` form: a lower-case `e` with a signed exponent of at least two
digits (`1e-07`, `1e+22`), and a `.0` on integral values (`100.0`, `-0.0`). A reader written in
another language must parse such a number, and a writer must produce this spelling.

**Content ids.** The content id of an artifact is the SHA-256, in lower-case hex, of the bytes of
its body as written (`shape_content_id`; a model body is written with sorted keys, a profile body
in insertion order, because the order of its enumerated values means something). The content id
of a JSON document is the SHA-256 of its codec form with sorted keys, **without** its declaration
(`format`, `version`, `shape_version`, `min_shape_version`), its old version key names and its
migration record: renaming the version keys does not change what the document says.

Changing any of this is a format change: it needs a new format version, a migration, a new
generation in the time-capsule corpus and new vectors.

## 8. Dates, decimals, locale

- **Dates and times** are UTC ISO 8601 with a `Z`: `2026-10-03T04:05:06Z`, with up to six digits of
  fraction when there are any. `shape.compat.utc_iso` writes it and refuses a naive datetime, which
  names no instant. A reader (`parse_utc_iso`) takes `Z` or an offset and converts to UTC; local
  forms, a space instead of `T` and locale forms are refused.
- **Decimals** (money, exact quantities) are strings, never binary floats. The artifact codec
  refuses a `Decimal` outright; the JSON writers that may meet one use `shape.compat.json_default`,
  which writes it as a string, a datetime as above, a path in its POSIX form and a numpy scalar
  as its Python value, and refuses anything else instead of `str()`-ing it.
- **Nothing is locale dependent.** No module reads or sets the locale, and a test fails if one
  does; the canonical forms are the same in every locale.

The rules are tested for the files in the table: the run manifest, the registry log (`created`
beside the epoch `created_at`), the receipt and the codec. Older files with other date spellings
still read.

## 9. Deprecation

No format version is deprecated today. When one is, the process is:

1. **Announce one release ahead.** The release that deprecates a version says so in the
   [changelog](../../CHANGELOG.md) under "Deprecated", naming the kind, the version and the major
   release that stops reading it directly. The support window below shows it. A test fails if a
   deprecation is declared (`Kind.deprecated`) and not in the changelog.
2. **Reader warnings.** Reading a deprecated version raises a `FormatDeprecationWarning`
   (a `DeprecationWarning`) that names the release that removes it and the command that converts
   it (`shape migrate`).
3. **Strict mode.** `shape.compat.strict_formats()`, or `SHAPE_STRICT_FORMATS=1`, makes a reader
   fail instead on a deprecated version, on an old key name where `version` is expected, and on an
   unknown field. CI jobs that want to find every file that needs attention use it.
4. **Removal.** Only in the next major release (2.0 for the 1.x series), and only if an offline `shape-migrate` path still
   reads the version: the reader moves to the migrator, which stays.

**Support window.** Every format version below is supported by every release until a major
release removes it by the process above; none is scheduled for removal. This table is generated
by `shape.compat.render_support_table()` and a test keeps it equal to the code.

<!-- support-table:start -->
| Kind | Format | Version | First release that reads it | Status |
|---|---|---|---|---|
| `artifact` | `shape` | 1 | 0.9.0 | supported |
| `artifact` | `shape` | 2 | 0.9.0 | supported |
| `profile-artifact` | `shape` | 1 | 0.9.0 | supported |
| `profile-artifact` | `shape` | 2 | 0.9.0 | supported |
| `model` | `shape-model` | 1 | 0.9.0 | supported |
| `model` | `shape-model` | 2 | 0.9.0 | supported |
| `safe-profile` | `shape-safe-profile` | 1 | 0.9.0 | supported |
| `generation-schema` | `shape-generation-schema` | 1 | 0.9.0 | supported |
| `generation-spec` | `shape-generation-spec` | 1 | 0.9.0 | supported |
| `scenario-pack` | `shape-scenario-pack` | 1 | 0.9.0 | supported |
| `registry-layout` | `shape-registry` | 1 | 0.9.0 | supported |
| `profile-registry-layout` | `shape-profile-registry` | 1 | 0.9.0 | supported |
| `run-manifest` | `shape-run-manifest` | 1 | 0.9.0 | supported |
| `contract` | `shape-contract` | 1 | 0.9.0 | supported |
| `contract-model` | `shape-contract-model` | 1 | 0.9.0 | supported |
| `signature` | `shape-signature` | 1 | 0.9.0 | supported |
| `migration-receipt` | `shape-migration-receipt` | 1 | 0.9.0 | supported |
| `gate-schema` | `shape-gates` | 1 | 0.9.0 | supported |
| `verify-config` | `shape-verify-config` | 1 | 0.9.0 | supported |
| `vault` | `shape-vault` | 1 | 0.9.0 | supported |
| `vault-policy` | `shape-vault-policy` | 1 | 0.9.0 | supported |
| `profile-export` | `shape-profile` | 1 | 0.9.0 | supported |
<!-- support-table:end -->

## 10. The time-capsule corpus

`tests/timecapsule/corpus/<generation>/` holds a file for every kind and format version, written
by the release that introduced it, through its own writer. A generation is frozen: `index.json`
lists each file with its SHA-256, and `expected/<generation>/<id>.json` holds the canonical form
the reader returned when the generation was written. `test_timecapsule.py` runs in CI and, for
every file of every generation, checks that nothing was edited, that the installed release loads
it, and that the canonical form is the recorded one: every recorded value unchanged, and any field
a later release added holding only its empty default (a file that lacks an optional field reads
with its default; a value there would be a change of meaning). It also fails if a kind or version of
`shape.compat.KINDS` has no file.

- `base` is what the code wrote before the unified keys (old key names only).
- `unified-keys` is what the release that introduced them wrote.

Where a writer no longer exists (a version 1 artifact), the file is built from the documented
layout and migrated by the existing registry, and `index.json` says so (`produced_by`). A file
that users author and no command writes is marked `authored`.

To add a generation (a release that changes a format): run
`python tests/timecapsule/capsule_generate.py tests/timecapsule/corpus/<name>` with that release, then
`python tests/timecapsule/capsule_bless.py tests/timecapsule/corpus/<name>`, and commit the result. Never
edit an existing generation or its expected files: a reader that no longer matches is the
defect.

## 11. Changing a persisted format: the checklist

1. Does the change need a new version? Anything an older reader would misread does; a new
   optional field does not. A change to a canonical form or a content id always does.
2. Add the version to `shape.compat.KINDS` (`current`, `first_release`) and a step to the
   migration registry (`shape.artifact.migrate.MIGRATIONS` for artifacts, `shape.migrate` for
   documents). The writer uses `compat.stamp`; the reader uses `compat.check_readable`.
3. Add a generation to the time-capsule corpus and regenerate the vectors if a canonical form
   changed.
4. Keep every old key name readable; never remove a reader.
5. Update this document and the changelog; run `pytest tests/state tests/timecapsule`.
