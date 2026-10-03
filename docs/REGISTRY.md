# `shape registry`: a content-addressed history of artifacts

```
shape registry ROOT ACTION ...
```

`ROOT` is a directory. Every version of a name is stored as the bytes you committed, under their
sha256 (`objects/<sha256>`); `logs/<name>.jsonl` records each commit, `refs/<name>/latest` and
`tags/<name>/<tag>` point at versions. `layout.json` at the root declares the layout version
(`format` `shape-registry`, `version` 1; opening an older registry writes it); a registry whose
layout is newer than this release reads is refused, naming the release that reads it. Each log
entry has `created_at` (epoch seconds) and `created` (UTC ISO 8601). See
[state and compatibility](specs/STATE_AND_COMPATIBILITY.md). A registry is a plain directory, so it is natural to back
up or commit to git. That is why **a registry never holds a raw profile unless you ask for it**.

## What it stores: safe forms, not real values

A raw profile (`shape profile -o X.shape`, or `shape profile export`) holds up to 500 real values
per column and each column's minimum and maximum. `shape registry ... commit` refuses one:

```
$ shape registry reg commit customers cust.shape
shape: error: cust.shape is a raw profile: it holds real values from the data ...
```

Commit the safe form instead:

```bash
shape registry reg commit customers cust.shape --safe            # converts, then commits
shape profile safe cust.shape -o cust.safe.json                  # or write it yourself ...
shape registry reg commit customers cust.safe.json               # ... and commit that
shape profile validate --safe cust.safe.json                     # exit 0: no leak found
```

A safe-profile JSON is scanned with the leak scanner before it is stored; one that fails (for
example `shape profile safe --unsafe-full-fidelity` output) is not committed (exit 1). The safe
form is deterministic, so committing the same data twice gives the same content id. `--k N` and
`--sensitive` set the minimum cohort, as in `shape profile safe`.

`--allow-raw` stores a raw profile as it is, with a warning, for a registry that stays as private
as the data. Every commit of a profile records `profile_form` (`safe` or `raw`) in its log entry.
Other files (models, contracts, anything) are stored unchanged. Only profiles are guarded: a
model holds summary statistics but is not scanned.

Python: `LocalRegistry.commit(name, data, metadata, allow_raw=False)` raises `RegistryError`
for a raw profile.

## Commands

```bash
shape registry ROOT commit NAME ARTIFACT [--meta KEY=VALUE]... [--business-date YYYY-MM-DD]
                                         [--safe [--k N] [--sensitive] | --allow-raw]
shape registry ROOT log NAME             # every commit, oldest first (JSON; `created` is UTC)
shape registry ROOT list                 # the names: commits, latest content id, tags
shape registry ROOT show NAME [REF]      # one log entry
shape registry ROOT diff NAME REF1 REF2  # what changed between two versions
shape registry ROOT checkout NAME [REF] -o OUT
shape registry ROOT tag NAME TAG [REF]
shape registry ROOT promote NAME SOURCE TARGET
```

A `REF` is `latest`, a tag, a promoted ref or a content id recorded for that name.

- **Metadata.** `--meta` (repeatable) and `--business-date` are recorded in the log entry, so a
  history of daily shapes can be read by date: `--business-date 2026-06-02` is
  `--meta business_date=2026-06-02` with the date checked.
- **Times.** Each log entry has `created_at` (epoch seconds) and `created` (UTC, ISO 8601).
- **Checkout** writes the stored bytes to `-o OUT`. With no `-o` it writes to standard output
  only when that is redirected or piped; on a terminal it refuses, because the bytes may be binary.
- **Diff** compares two versions: for two JSON documents it lists the changed paths (objects are
  walked, lists compared whole); for two raw profiles it gives the drift; otherwise it says
  whether the content ids differ.
- An unknown name or ref is a one-line error (exit 2), including `log` of a name that has no
  commits.

## Which store holds real values

| Store | Holds | Meant for |
|---|---|---|
| `.shape` files, `--json` summaries, HTML reports | real values (raw profile) | the pipeline; as private as the data |
| `shape registry` | safe forms by default; raw only with `--allow-raw` | sharing, backup, git |
| `shape profile registry` | full profiles by default; the safe form with `save --safe` | a local catalog; `--safe` for a shared one |
| `shape profile safe` output (`*.safe.json`) | no raw values | anywhere |
