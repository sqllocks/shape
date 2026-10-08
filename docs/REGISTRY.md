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

A raw profile (`shape profile -o X.shape --capture full`, or `shape profile export` of one)
holds up to 500 real values per column and each column's minimum and maximum. The default
`shape profile -o X.shape` is a safe capture (`docs/PRIVACY_MODEL.md`), which is not raw: it is
scanned by the leak scanner and committed as it is (`profile_form` `safe`). `shape registry ...
commit` refuses a raw one:

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
for a raw profile, and for a `.shape` container whose manifest no Shape reader accepts (for
example one above the manifest size limit: `not a Shape container: manifest too large`), which is
refused without being inflated.

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
shape registry ROOT prune --before DATE [--name NAME ...] [--keep-last N] [--dry-run] [--json]
```

A `REF` is `latest`, a tag, a promoted ref or a content id recorded for that name. `prune` removes
old log entries and the objects nothing points at any more (see [Pruning](#pruning)).

- **Metadata.** `--meta` (repeatable) and `--business-date` are recorded in the log entry, so a
  history of daily shapes can be read by date: `--business-date 2026-06-02` is
  `--meta business_date=2026-06-02` with the date checked.
- **Times.** Each log entry has `created_at` (epoch seconds) and `created` (UTC, ISO 8601).
- **Checkout** writes the stored bytes to `-o OUT`. With no `-o` it writes to standard output
  only when that is redirected or piped; on a terminal it refuses, because the bytes may be binary.
- **Diff** compares two versions, by what they are:
  - two **raw profiles** give the drift of `shape diff`: `drift` with `drifted` and `changes`
    (`kind`, `severity`, `score`, `baseline`, `current`);
  - two **share-safe profiles** (what a registry stores by default) give the same `drift`, with
    `not_measured` besides it (below), and `changed`, the paths that differ;
  - two other JSON documents list the changed paths (objects are walked, lists compared whole), and
    anything else says whether the content ids differ. A raw and a safe version of one name are not
    compared as profiles: `changed` is `null`.
- An unknown name or ref is a one-line error (exit 2), including `log` of a name that has no
  commits.

## Pruning

A registry only grows: every commit appends to `logs/<name>.jsonl` and adds an object. A team that
commits on every pipeline run keeps it to a bounded size with `prune`:

```bash
shape registry reg prune --before 2026-06-01 --dry-run          # what would go; nothing changes
shape registry reg prune --before 2026-06-01                    # every name
shape registry reg prune --before 2026-06-01T00:00:00Z --name orders --keep-last 7 --json
```

`--before` is a date (`YYYY-MM-DD`, midnight UTC) or an ISO 8601 timestamp with `Z` or an offset
(`2026-06-01T02:00:00+02:00`); a timestamp without a zone is refused, because it names no instant.
A log entry is removed when its `created_at` is **strictly before** the cutoff (an entry committed
at exactly the cutoff stays), unless one of these keeps it:

| kept because | what it means |
|---|---|
| `ref` | a ref of the name (`refs/<name>/*`: `latest`, a promoted ref such as `production`) points at its content id |
| `tag` | a tag of the name (`tags/<name>/*`) points at its content id |
| `keep_last` | it is one of the newest `--keep-last N` entries of the name (default 1, at least 1) |

Every entry whose content id a ref or tag points at is kept, also when the same bytes were
committed more than once. A name whose old entries are all tagged loses nothing. `--name`
(repeatable) prunes only those logs; without it every name is pruned. A log line that this
release cannot read, or an entry without `created_at`, is kept as it is and listed under `skipped`.

After the logs, the objects: an object is deleted only when **no remaining log entry of any name,
no ref and no tag** points at its content id (objects are addressed by content, so two names can
share one). An object nothing points at is deleted even when it was orphaned before this prune (by
an interrupted one, for example). A file under `objects/` whose name is not a content id (64
lowercase hexadecimal characters), and any directory there, is left alone and listed under
`skipped`.

The command prints the entries removed and kept for each name, the number of objects removed and
the bytes freed. `LocalRegistry.prune(before, *, names=None, keep_last=1, dry_run=False)` returns
the report; `--json` prints it as the `payload` of the `shape-result` document every command prints
under `--json` (`format`, `version`, `command` `registry prune`, `exit_code`; see
[EXIT_CODES.md](EXIT_CODES.md)). An error under `--json` is the same document with `exit_code` 2
and `error`. The report:

```json
{"format": "shape-registry-prune", "version": 1, "cutoff": "2026-06-01T00:00:00Z",
 "dry_run": false,
 "names": {"orders": {"entries_removed": 12, "entries_kept": 3,
                      "kept_because": {"ref": ["<content id>"], "tag": ["<content id>"],
                                       "keep_last": ["<content id>"]}}},
 "objects_removed": ["<content id>", "..."], "bytes_freed": 48211,
 "skipped": [{"path": "objects/NOTES.md", "reason": "not a content id ..."}]}
```

`kept_because` lists the content ids of the entries **older than the cutoff** that were kept, under
every reason that applies (one entry can be in several lists); newer entries are kept because they
are new and are not listed. `objects_removed` is sorted. The report declares `format` and an
integer `version`; within version 1 keys may be added, never removed or changed. `--dry-run`
(`dry_run=True`) computes the same report and changes nothing, not even a lock file.

**Pruning cannot be undone.** The removed versions and their bytes are gone; back the registry up
first (it is a plain directory) if you may need them. After a prune, `log`, `list`, `show`, `tag`,
`checkout`, `diff`, `shape bisect` and `shape timelapse` work on the remaining entries; a removed
content id is no longer recorded (exit 2), and no ref or tag ever points at a removed version.

**Crash safety.** Each log is written to a temporary file in `logs/` and replaces the old one
atomically; objects are deleted only after every log has been replaced. A prune that is
interrupted leaves a registry where every remaining entry, ref and tag still resolves (at worst
some objects nothing points at are left over); running the same prune again finishes the job.

**The lock.** A prune holds `ROOT/prune.lock` while it runs. A commit, `tag` or `promote` refuses to
run while it is held (exit 2, naming the file), and a second prune refuses as well. Each commit,
`tag` and `promote` announces itself with a short-lived `ROOT/.commit-<id>.lock` file; a prune waits
up to 30 seconds for those to finish before it changes anything. A process that was killed can
leave either file behind: the error names it, and you delete it when no commit or prune is running.

Exit codes: 0 pruned (or dry run); 2 bad input (a bad date, a date without a zone, an unknown or
invalid name, a `--keep-last` below 1 or not a number, a held lock).

## Drift between two safe forms

`shape registry ROOT diff NAME REF1 REF2` (and `shape.registry.drift.diff_versions` and
`diff_safe`) compares two share-safe profiles on **every metric both forms hold**, with the rules and
thresholds of `shape diff` (`docs/DRIFT.md`): a changed null rate is `null_rate_change`, severity
`medium`, whether the two versions are stored raw or safe, and the record has the same `kind`,
`severity`, `score`, `baseline` and `current` fields. The two scores can differ in the last digits,
because a safe form rounds the rates it keeps.

A safe form withholds some of what a raw profile holds, so some changes cannot be seen. Those are
listed under `not_measured`, never skipped without a word:

```json
"not_measured": [
  {"table": null, "column": "amount", "metric": "range",
   "reason": "a safe form keeps bounds, not the minimum and maximum"},
  {"table": null, "column": "amount", "metric": "outlier_rate",
   "reason": "a safe form does not hold the outlier rate"}
]
```

| metric | why it can be in `not_measured` |
|---|---|
| `range` | a safe form keeps winsorized bounds from the quantiles, never the minimum and maximum of a numeric column; the change of the range is not measured (a shift of the whole distribution still shows as `mean_shift`, `spread_change` and `distribution_shift`) |
| `outlier_rate` | a safe form does not hold the outlier rate of a numeric column |
| `mean`, `spread`, `distribution_shift` | the column's mean, standard deviation or quantiles were withheld in one of the two forms |
| `hour_of_day`, `day_of_week` | a date column's hour histogram was withheld, or (always, for the day of week) the form holds no first and last date to size the span |
| `length` | the string length of a column was withheld |
| `categories` | one form withholds the category weights (a category below the minimum cohort, or a personal-data column) |
| `null_rate`, `cardinality` | one form does not hold it |

Only columns both versions hold are listed (a column added or removed is `column_added` or
`column_removed`), and a column the drift policy ignores is left out of both lists. The bridge
command `registry_diff` (`docs/BRIDGE.md`) returns the same result; for two raw versions it withholds
the values of classified columns as `diff` does, and it returns no raw value for two safe ones.

## Which store holds real values

| Store | Holds | Meant for |
|---|---|---|
| `.shape` files, `--json` summaries, HTML reports | safe capture by default; real values with `--capture full` | a safe capture: anywhere; a full one: as private as the data |
| `shape registry` | safe forms by default; raw only with `--allow-raw` | sharing, backup, git |
| `shape profile registry` | safe captures by default; real values with `save --capture full`; the safe profile JSON with `save --safe` | a local catalog; a shared one holds safe captures |
| `shape profile safe` output (`*.safe.json`) | no raw values | anywhere |

## Searching the history

`shape bisect` finds the first committed version of a name that changed, `shape bisect layers`
the layer of a pipeline where a change appears, and `shape timelapse` follows one column across
the versions; see `docs/HISTORY.md`. They need raw profiles (`--allow-raw`).
