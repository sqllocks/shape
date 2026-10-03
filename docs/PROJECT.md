# The project file: `shape.yml`

A Shape setup, in git. `shape.yml` holds what is otherwise spread over command-line flags: the
named **sources**, a **baseline** for each, **thresholds** and **ignore** lists per column,
**gates** with their **mode** (observe or enforce), and column **owners** and **annotations**.
`shape init` writes a starting project; `shape project validate` checks one.

```bash
pip install "sqllocks-shape[yaml]"     # the file is YAML; PyYAML is an optional extra
shape init my-feed --source orders     # shape.yml, folders, .gitattributes, a CI workflow
cd my-feed && shape project validate
```

## An example

```yaml
format: shape-project
version: 1
name: orders-platform

sources:
  orders:
    path: data/orders                  # a file, folder, glob, Delta table or URI
    dataset: false                     # true: a folder of tables, one per file
    contract: contracts/orders.json    # what `shape check` uses
    baseline:
      kind: rolling_window             # previous_run | same_weekday | rolling_window | month_end | pinned
      window: 7
      registry: shapes/registry        # default
    thresholds:                        # drift thresholds, docs/DRIFT.md
      null_rate: 0.02
    ignore: [load_ts]
    columns:
      amount:
        thresholds: {mean_shift_std: 1.0}
        owner: finance-data@example.com
        annotations: {unit: EUR}
      batch_id:
        ignore: true

gates:
  schema_conformance: {mode: enforce}
  distribution: {mode: observe}
```

## Reference

| Key | Meaning |
|---|---|
| `format` | `shape-project`. |
| `version` | An integer, now `1`. A file with a newer version than the installed Shape reads is refused with an error that says so (upgrade Shape). |
| `name` | Optional label. |
| `changes` | Optional path of the planned-change file, relative to the folder holding `shape.yml`; default `shape-changes.yml` next to it when that file exists. See `docs/PLANNED_CHANGES.md`. |
| `sources.NAME` | Letters, digits, `.` `_` `-`, starting with a letter or digit. Also the default registry name of the source's baselines. `safe`, `validate`, `export`, `import`, `list` and `registry` are `shape profile` subcommands and cannot be source names. |
| `…path` | Required. Relative paths are relative to the folder holding `shape.yml`; a URI (`abfss://…`) or an absolute path is used as written. |
| `…dataset` | `true` when `path` is a folder of table files (one table per file), as `--dataset`. |
| `…contract` | The contract `shape check PROFILE.shape` uses when none is given. |
| `…baseline` | See below. |
| `…thresholds` | Any threshold of `docs/DRIFT.md` for the whole source. |
| `…ignore` | Columns left out of the comparison: a name, `table.column` or a glob. |
| `…columns.COLUMN` | A column name, `table.column` or glob. `thresholds`, `ignore: true`, `owner`, `annotations` (strings, numbers, booleans). |
| `gates.NAME.mode` | `observe` or `enforce`. NAME is a gate of `shape verify` (`schema_conformance`, `referential_integrity`, …). A gate that is not listed is enforced. |

The JSON Schema is `src/shape/schemas/shape-project-v1.schema.json` (also `shape.project.schema()`).
Rules a schema cannot state (name syntax, non-empty strings, the baseline keys that go with each
kind, gate names) are checked by `shape project validate`, which reports every problem at once
with its key path, for example `sources.orders.baseline.window: window is required for
rolling_window`. Two equal keys in one mapping are an error, not a silent "last one wins".

## Baselines

A baseline is resolved against the registry (`shape registry`, `docs/REGISTRY.md`): a plain
folder of committed profiles with a log. An entry's date is its `business_date` metadata
(`shape registry ROOT commit NAME FILE --business-date YYYY-MM-DD`), else the UTC date of the
commit. The reference date D is `--baseline-date` (default: today, UTC).

| `kind` | Resolves to |
|---|---|
| `previous_run` | The newest commit (before D when `--baseline-date` is given). |
| `same_weekday` | The newest entry before D that falls on D's weekday. |
| `rolling_window` | The `window` newest entries before D (fewer when fewer exist). `shape diff` reports only the changes that show up against **every** run in the window, so data inside the range of the recent runs is not drift. |
| `month_end` | The newest entry in the month before D's, up to that month's last day. |
| `pinned` | `artifact: FILE.shape` (relative to `shape.yml`), or `ref:` a registry ref, tag or content id. |

`registry:` (default `shapes/registry`) and `name:` (default: the source's name) say where. The
registry is read, never created or changed, and a missing baseline is an error that says what was
looked for. `shape diff` compares full profiles, so commit them with `--allow-raw` (the registry
refuses a raw profile otherwise: it holds real values). A share-safe baseline cannot be diffed and
is reported as such.

## How the commands use it

Commands find `shape.yml` in the working folder or the nearest folder above it, never above a
repository root (a folder with `.git`). `--project FILE` names a file, `--no-project` ignores it.
A `shape.yml` that is not valid stops the command with exit 2 and the problems listed: fix it, or
pass `--no-project`.

| Command | From the file |
|---|---|
| `shape profile NAME -o OUT.shape` | `NAME` is a source: its `path`, `dataset`, and (unless `--name`) its name. A real path called `NAME` wins. |
| `shape diff [BASE] CURRENT.shape` | The source's thresholds, per-column thresholds and ignore list; with only `CURRENT.shape`, its baseline (`--baseline-date YYYY-MM-DD`). Changes carry the column's `owner` and `annotations`. |
| `shape check PROFILE.shape [CONTRACT]` | The source's `contract` when none is given; violations carry `owner` and `annotations`. |
| `shape verify NAME-or-DATA` | `NAME` may be a source; each gate's mode (below). |
| `--json` / `-o` reports | A `project` block (`file`, `format`, `version`, `source`, `baseline`), and for `verify` the `mode` of every gate. |

The source is `--source NAME`, else the only source, else the one named like the profile. If
there are several and none is chosen, Shape says so on stderr and does not apply source settings.

**Flags override the file.** Every existing flag works as before and wins over `shape.yml`:
threshold flags (`--null-rate`, `--threshold K=V`, `--column-threshold`) override key by key,
`--ignore` and `--only` replace the file's lists, `--policy FILE` replaces the file's policy
whole, an explicit `BASE.shape` or `CONTRACT` replaces the declared baseline or contract. Without
a `shape.yml` nothing changes.

**Gate modes.** `observe` still runs the gate and reports its errors, but a failure does not
change the exit code (`Result: PASS (observed failures: …)`); `enforce` (the default) fails the
run. `--strict` counts only the warnings of enforced gates. The report keeps the truth: `passed`
is the raw result, `enforced_passed` is what decided the exit code.

## `shape init`

```
shape init [DIR] [--name NAME] [--source NAME[=PATH]]... [--force]
```

Writes `shape.yml` (valid, with the optional settings as comments), `data/`, `shapes/` and
`contracts/`, a `.gitattributes` rule (`*.shape diff=shape`, see `shape git-setup`) and
`.github/workflows/shape.yml`, an example that validates the project, profiles each source and
diffs it against its baseline. It never overwrites `shape.yml` (use `--force`), keeps an existing
workflow, and only extends `.gitattributes`. Run `shape git-setup` in the repository to
configure the `git diff` filter itself.

## Versioning

The file declares `format` and an integer `version`. A change that older Shapes could misread
raises the version; additions an older Shape would ignore do not exist, because unknown keys are
errors. `tests/fixtures/project/v1/shape.yml` is a frozen version 1 file that every later Shape
must still load (`tests/project/test_compat.py`).
