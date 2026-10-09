# Profile registry and profile files

Status: available.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


Two groups of commands manage profiles. Both read and write Shape's own formats only: a `.shape`
profile artifact, and the `shape-profile` JSON that `export` writes.

## Profile files

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`import` accepts only `shape-profile` JSON; any other JSON file is refused with exit 2.
`list` prints what it skipped (a file that is not a profile) on stderr instead of hiding it.

## The profile registry

A directory of named, tagged profiles, one table profile per file (`name.shape`, or `name.safe.json`
for the safe form below):

```
<root>/<system>/<table>/<name>.shape      identity: system/table/name
<root>/_index.json                        rebuilt from the files by `reindex`
<root>/_layout.json                       the layout version (written when a registry is opened)
```

The root is `--root DIR`, else `$SHAPE_PROFILE_REGISTRY`, else `~/.shape/profiles`. The
description and tags are kept inside each file, so the index can always be rebuilt.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`save` takes data (it is profiled) or a `.shape` profile, and stores one entry per table.
`validate` checks the files and the index; with `--data` it also compares the data's profile with
the stored one (columns added or removed, type changes, null-rate drift) and exits 1 on a
difference.

Safeguards: every identity part is a plain name (letters, digits, `.`, `_`, `-`), so nothing can
leave the root; files and the index are written atomically; `save` refuses to replace an existing
profile without `--overwrite`; `reindex` lists every file it could not read.

### Real values, and the safe form

By default a stored profile is a safe capture, as written by `shape profile -o`
(`docs/PRIVACY_MODEL.md`): a sensitive column keeps statistics and formats only and a category is
kept only when every released category has at least `k` rows (`--k N`, `--column-k COLUMN=N`,
`--classify COLUMN=LEVEL`). `save --capture full` keeps **real values from the data** (up to 500
per column with their counts, and each column's minimum and maximum), says so in the artifact and
on stderr (`shape: warning: --capture full keeps real values in ROOT; do not commit or share it`);
such a store is a private catalog, and the default root is under your home folder. `list` shows
`safe capture` or `full` in its last column.

For the stricter share-safe profile JSON, save with `--safe`:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

A safe entry is the output of `shape profile safe` (one table, no field that can hold a raw value
or a value list), stored as `<system>/<table>/<name>.safe.json`. It is checked with the leak
scanner when it is saved and again by `registry validate`, and
`shape profile validate --safe` accepts the file. `list`, `tag`, `diff`, `validate --data`,
`reindex` and `delete` work on it; the column fields that `diff` compares are the safe profile's
(`mean`, `quantiles`, `categorical_weights`, ...). An identity has one form at a time: saving the
other form needs `--overwrite` and replaces it. Relationships between tables are not kept in a
safe entry. A description is free text and is scanned too: one that looks like personal data is
refused.

`shape registry` is a different store: the content-addressed registry of artifacts
(`docs/REGISTRY.md`), which refuses a raw profile unless told otherwise. The two stores are
separate and are never merged.
