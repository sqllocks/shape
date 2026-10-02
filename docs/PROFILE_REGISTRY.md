# Profile registry and profile files

Two groups of commands manage profiles. Both read and write Shape's own formats only: a `.shape`
profile artifact, and the `shape-profile` JSON that `export` writes.

## Profile files

```bash
shape profile export orders.shape -o orders.json    # portable JSON, round-trips exactly
shape profile import orders.json -o orders.shape    # --name NAME renames it
shape profile list DIR                              # the .shape profiles in a directory (--json)
shape profile validate orders.shape                 # well formed? tampered? (exit 0 / 1)
shape profile validate --safe orders.json           # the leak scanner (see PRIVACY_MODEL.md)
```

`import` accepts only `shape-profile` JSON; any other JSON file is refused with exit 2.
`list` prints what it skipped (a file that is not a profile) on stderr instead of hiding it.

## The profile registry

A directory of named, tagged profiles, one table profile per file:

```
<root>/<system>/<table>/<name>.shape      identity: system/table/name
<root>/_index.json                        rebuilt from the files by `reindex`
```

The root is `--root DIR`, else `$SHAPE_PROFILE_REGISTRY`, else `~/.shape/profiles`. The
description and tags are kept inside each file, so the index can always be rebuilt.

```bash
shape profile registry save orders.csv --system crm --name 2026Q2 --tags prod,daily
shape profile registry save orders.shape --system crm --name 2026Q2 --overwrite
shape profile registry list [--system S] [--table T] [--tag X]... [--query TEXT] [--json]
shape profile registry tag crm/orders/2026Q2 reviewed        # --remove to drop tags
shape profile registry diff crm/orders/2026Q2 crm/orders/2026Q3   # --fail-on-diff for CI
shape profile registry validate [crm/orders/2026Q2] [--data new.csv --tolerance 0.05]
shape profile registry reindex
shape profile registry delete crm/orders/2026Q2
```

`save` takes data (it is profiled) or a `.shape` profile, and stores one entry per table.
`validate` checks the files and the index; with `--data` it also compares the data's profile with
the stored one (columns added or removed, type changes, null-rate drift) and exits 1 on a
difference.

Safeguards: every identity part is a plain name (letters, digits, `.`, `_`, `-`), so nothing can
leave the root; files and the index are written atomically; `save` refuses to replace an existing
profile without `--overwrite`; `reindex` lists every file it could not read.

A stored profile is a full Shape profile, as written by `shape profile -o`: it holds value
ranges and enum values. Share the output of `shape profile safe` instead when a profile leaves
your organisation.

`shape registry` is unchanged: it is the content-addressed registry of `.shape` artifacts. The
two stores are separate and are never merged.
