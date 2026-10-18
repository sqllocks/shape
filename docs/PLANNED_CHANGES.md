# Planned changes: `shape-changes.yml`

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" PLANNED_CHANGES
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for PLANNED_CHANGES
    ```


A release adds a column, a migration changes a type, a source switches vendor. Until now the only
ways to keep `shape diff --fail-on-drift` and the gates quiet were `--ignore`, a looser threshold
or an `observe` gate, and all of those also hide changes nobody planned. A planned-change file
lists the changes you expect, with a window and a reason, kept in git next to `shape.yml`. A
planned change inside its window is reported as planned and does not fail; the same change the day
after the window ends fails again; an unplanned change next to it still fails.

## The file

```yaml
format: shape-planned-changes
version: 1
changes:
  - id: orders-loyalty-tier          # unique: [a-z0-9][a-z0-9._-]*
    source: orders                   # a source of shape.yml (optional: all sources)
    column: loyalty_tier             # a name, table.column, a glob, or * (table-level kinds)
    kinds: [column_added]            # drift kinds (docs/DRIFT.md) or contract rules
    from: 2026-11-01                 # ISO date, UTC, inclusive (optional)
    until: 2026-11-30                # ISO date, UTC, inclusive (required)
    action: expect                   # expect (default) | suppress | severity
    class: additive                  # optional: breaking | additive | cosmetic
    reason: Release 2026-11-02 adds the loyalty tier column
    owner: orders-team@example.com   # optional
    ticket: SHAPE-123                # optional
```

| Key | Meaning |
|---|---|
| `format`, `version` | `shape-planned-changes` and the integer `1`. A newer version than this Shape reads is refused with a message that says to upgrade Shape. |
| `id` | Unique, `[a-z0-9][a-z0-9._-]*`. |
| `source` | Optional. A source name from `shape.yml`. With a name, the entry matches only when that source is in use (`--source`, or the only source); without one it matches every source. |
| `column` | A column name, `table.column`, a glob (`note*`, `orders.*`), or `*`. Table-level changes (`table_added`, `row_count_change`, ...) have no column: they match `*`, the table's name or a glob of it. |
| `kinds` | One or more drift kinds from `docs/DRIFT.md` (`column_added`, `dtype_change`, `null_rate_change`, `row_count_change`, ...) or contract rules (`unique`, `not_null`, `dtype`, ...). An unknown kind is a validation error. `not_null` also matches the `nullable` rule. |
| `from`, `until` | ISO 8601 dates, UTC, both inclusive. `until` is required and must not be before `from`. Without `from` the entry is active from the beginning. |
| `action` | `expect` (default): reported as planned, not failing. `suppress`: not reported at all. `severity`: reported with the `severity` given (`low`, `medium` or `high`, required with this action and only valid with it). |
| `class` | Optional: `breaking`, `additive` or `cosmetic`. The change class (`docs/DRIFT.md`, "Change classes") of the changes the entry matches, instead of the class of their kind. It decides which count of `semver` a planned change is in, and what a `severity` entry that counts is counted as. A file without it reads as before. |
| `reason` | Required. |
| `owner`, `ticket` | Optional. |
| `acknowledged_by`, `acknowledged_at` | Set by `shape changes ack`. |

The JSON Schema is `src/shape/schemas/shape-planned-changes-v1.schema.json`. JSON works as well as
YAML (name the file `*.json`). `shape changes validate` checks what a schema cannot: id syntax,
real calendar dates, kinds, `until` against `from`, unique ids.

## Where it is found

`shape.yml` takes an optional key `changes: PATH` (relative to the folder holding `shape.yml`).
Without it, `shape-changes.yml` next to `shape.yml` is used when that file exists. Every command
that reads the project file reads it. `--changes FILE` overrides it, `--no-changes` ignores it.
Without a project file, `--changes FILE` is the only way to name one. A named file that is missing
or not valid is an input error (exit 2).

## Matching

A change matches an entry when the entry's source applies, the change's kind is in `kinds`, its
column matches `column`, and the **day** is inside the window. The day is the current UTC date, or
`--on YYYY-MM-DD` on `shape diff`, `shape check` and `shape verify` (for tests, and to re-run an old
CI job). The first matching entry in file order wins.

- **Inside the window** the change is planned. With `expect` it carries
  `planned: {"id": ID, "action": "expect"}` and does not count: `--fail-on-drift` and
  `min_severity` count only unplanned changes. With `severity` it carries the entry's severity (the
  engine's own is kept as `severity_was`) and counts when the new severity reaches `min_severity`.
  With `suppress` it is not reported.
- **After `until`** the entry never matches. When it would have matched, `shape: warning: planned
  change ID expired on DATE` is printed and the entry is listed under `expired`.
- **Before `from`** the entry is not active yet and is not reported.

## `shape diff`

[Run this example](#local-example-1).


The `--json` result adds three lists next to `drifted` and `changes`:

- `planned`: the entries that matched, each with `matches`.
- `planned_not_observed`: active `expect` entries that matched no change. Informational, never
  failing: the release may not have happened yet.
- `expired`: entries past their `until` that would have matched.

The result's `semver` counts unplanned changes only; the planned ones are counted under
`semver.planned`. `--fail-on CLASS` never fails on an `expect` entry, and fails again one day after
its `until`:

[Run this example](#local-example-2).


A planned change is marked `(planned: ID)` in the lines `shape diff` prints on stderr.
With a rolling-window baseline (`docs/PROJECT.md`) the lists describe the first run of the window,
kept to the entries that still match a change after the window's intersection.

In Python: `shape.diff(before, after, planned=PATH_OR_ENTRIES, on="2026-11-15", source="orders")`.
`planned` is a path, a list of entry mappings or a `shape.project.changes.PlannedChanges`;
`DiffResult.planned`, `.planned_not_observed` and `.expired` hold the lists (they are `None` when
`planned` was not given).

## `shape check` and `shape verify`

`shape check` applies the entries to contract rule failures: `kinds` names the rule (`unique`,
`nullable` / `not_null`, `dtype`, ...) and `column` the column. A planned violation carries
`planned` and does not fail; `suppress` removes it; `severity` adds the severity and fails only at
`high`. The result adds `planned` and `expired`.

`shape verify` applies them to the schema drift gate (the verify configuration's `baseline`, and
the gate modes of `shape.yml`): a planned new or removed table or column, or a type change, is a
warning marked `(planned: ID)` instead of an error, so an enforced gate does not fail on it;
`suppress` hides it; `severity: high` (or a class at or above the gate's `fail_on`) keeps it an
error. Kinds: `table_added`, `table_removed`,
`column_added`, `column_removed`, `dtype_change`. A report written with `-o x.json` carries
`planned` and `expired`.

## Examples

A planned new column, active for the release month:

```yaml
- id: orders-loyalty-tier
  column: loyalty_tier
  kinds: [column_added]
  from: 2026-11-01
  until: 2026-11-30
  reason: Release 2026-11-02 adds the loyalty tier column
```

A planned type change, with the mean allowed to move while the migration runs:

```yaml
- id: amount-decimal
  source: orders
  column: orders.amount
  kinds: [dtype_change, mean_shift]
  until: 2026-12-15
  reason: Migration changes amount from float to decimal
  ticket: SHAPE-140
```

## Acknowledging a reported change

[Run this example](#local-example-5).


`shape changes ack RESULT.json` turns the selected changes (`--all`: every change that is not
already planned; `--change N`, 1-based, repeatable) into `expect` entries with `acknowledged_by`
and `acknowledged_at`, and `from` set to today. Review the entries in git like any other change.

## Commands

| Command | |
|---|---|
| `shape changes validate [FILE]` | Exit 0 valid, 1 with each problem on its own line (entry id, key, message), 2 when the file is missing or unreadable. |
| `shape changes list [FILE] [--active-on DATE] [--json]` | The entries. |
| `shape changes add --id ID --column COL --kind KIND [--kind ...] --until DATE --reason TEXT [--from DATE] [--action expect\|suppress\|severity] [--severity S] [--source NAME] [--owner NAME] [--ticket ID] [--file FILE]` | Appends an entry (`from` defaults to today). A duplicate id, an unknown kind or any invalid entry exits 2 and writes nothing. |
| `shape changes ack RESULT.json --until DATE --reason TEXT --by NAME [--change N ...\|--all] [--file FILE]` | See above. |

Writes append text to the file, so comments, key order and every entry that was already there stay
as they were. The file written to is `--file`, else the project's, else `shape-changes.yml` next to
`shape.yml` (or here). A file whose `changes:` is not the last top-level key cannot be appended to
safely: `add` says so (exit 2) and leaves it alone.

## Not covered

Classifying a change as breaking, additive or cosmetic, notifications, approval by more than one
person and any server-side workflow are out of scope. Drift thresholds, kinds and severities are
unchanged. `shape verify` applies entries only to the schema drift gate: the other gates have no
drift kinds.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape diff base.shape today.shape --fail-on-drift --changes shape-changes.yml
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)
    {"changes": [], "drifted": false, "expired": [], "planned": [], "planned_not_observed": [], "semver": {"additive": 0, "breaking": 0, "bump": "none", "cosmetic": 0, "planned": {"additive": 0, "breaking": 0, "cosmetic": 0}}}
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape diff base.shape today.shape --fail-on breaking --changes shape-changes.yml
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)
    {"changes": [], "drifted": false, "expired": [], "fail_on": "breaking", "failed": false, "planned": [], "planned_not_observed": [], "semver": {"additive": 0, "breaking": 0, "bump": "none", "cosmetic": 0, "planned": {"additive": 0, "breaking": 0, "cosmetic": 0}}}
    ```

<a id="local-example-5"></a>

### Example 6

<!-- example: 5 -->

```bash {.runnable-reference}
shape diff base.shape today.shape --json result.json        # exit 1: drift
shape changes ack result.json --until 2026-12-31 --reason "reviewed with the owner" --by sam --all
shape diff base.shape today.shape --fail-on-drift             # exit 0 inside the window
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)
    shape: error: --all: there is no unplanned change in the result
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)
    {"changes": [], "drifted": false, "semver": {"additive": 0, "breaking": 0, "bump": "none", "cosmetic": 0}}
    ```
