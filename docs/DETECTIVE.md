# Data detective packs

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" DETECTIVE
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for DETECTIVE
    ```


A detective pack is a case: a generated batch of data with problems planted in it, a brief, hints
and a way to check your answer. You look at the batch with Shape's own commands (`shape profile`,
`shape diff`, `shape check`), say which table and column hold which failure mode, and Shape tells
you what you found, what you missed and what you got wrong. It is the quickest way to learn what
each check can and cannot see.

The data is never shipped. A pack names a [library scenario](SCENARIO_LIBRARY.md) that plants the
problems and a seed, so `start` generates the same case every time. The failure modes are those of
the [failure mode catalog](FAILURE_MODES.md).

## How to play

[Run this example](#local-example-0).


`case/` holds `data/` (one Parquet file per table), `baseline.shape` (the profile of the clean batch
this one should look like) and the case's brief, a Markdown file (brief.md). Then look:

[Run this example](#local-example-1).


The diff lists what changed against the baseline. A change that a diff cannot show (an earliest
date that moved, a value that is no longer allowed) shows up in a contract written from what the
baseline says is normal: `shape check today.shape contract.json`. Write what you found in an answer
file:

```json
{"format": "shape-detective-answer", "version": 1,
 "findings": [{"table": "customer", "column": "last_name", "mode": "null-flood"}]}
```

`mode` is a failure mode id (`shape failure-modes list`). For a column that was renamed, name the old
column. Then:

[Run this example](#local-example-3).


`check` prints each finding as `found` (named and planted), `missed` (planted and not named) or
`wrong` (named and not planted), and exits 0 when every planted finding is named and none is wrong,
1 otherwise, and 2 for a malformed answer, an unknown failure mode or an unknown pack. `--json`
prints the same as one document.

## The packs

| Pack | Level | Problems |
|---|---|---|
| `first-case` | beginner | one column of the customer table stopped being filled |
| `empty-forms` | beginner | a stand-in value fills a column without a single null |
| `twice-the-price` | intermediate | part of a numeric column is recorded in another unit |
| `text-trouble` | intermediate | two text columns are wrong in different ways |
| `renovations` | advanced | four schema changes, one in each of four tables |
| `clocks-and-keys` | advanced | dates, keys and a relationship between columns |

Every pack is solved by an automated solver in the test suite that uses only `shape profile`,
`shape diff` and `shape check`, so a pack is never unsolvable with the commands above.

## Writing a pack

A pack is `src/shape/scenario/library/detective/NAME.json`:

```json
{
  "format": "shape-detective-pack",
  "version": 1,
  "name": "first-case",
  "level": "beginner",
  "brief": "What the client says is wrong, in a few sentences.",
  "hints": ["Where to look.", "What to look for.", "Nearly the answer."],
  "scenario": "library:null_flood",
  "seed": 7101,
  "findings": [{"table": "customer", "column": "last_name", "mode": "null-flood"}]
}
```

- `name` is a slug and the file's name; `level` is `beginner`, `intermediate` or `advanced`.
- `scenario` is a library scenario that plants defects in one batch (a scenario that is a change
  over time has no single batch). A pack that needs several problems uses a scenario with several
  defects; add one to the library (`docs/SCENARIO_LIBRARY.md`) when none fits.
- `findings` lists each planted problem: the table, the column (`null` for a whole table) and the
  failure mode id. Give each pack its own `seed`.
- Make the problems findable: run the case through the solver in `tests/scenario/test_detective.py`
  (every pack in the folder is solved there) and write hints that lead from where to look to what
  to look for.

Shape refuses a pack of another format or a newer version, with unknown or missing keys, a level
that is not one of the three, a scenario that is not in the library, a failure mode that is not in
the catalog and a finding that repeats.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape detective list                                  # the packs, their level, how many problems
shape detective start first-case -o case/             # writes the case; never the answer
```

??? info "Output (exit 0)"

    ```text {.expected}
    clocks-and-keys    advanced      4 problems
        The nightly load passes its own checks, yet downstream dashboards show orders from the past, dates that are off, return ids that repeat and customers in the wrong places.
    empty-forms        beginner      1 problem
        Customer names look fine at a glance, yet the welcome emails greet some people with an odd word.
    first-case         beginner      1 problem
        The customer team says some people are missing from the mailing list.
    renovations        advanced      4 problems
        A release went out on Friday and touched four tables.
    text-trouble       intermediate  2 problems
        Two columns of the customer table hold text, and both are wrong, in different ways.
    twice-the-price    intermediate  1 problem
        Finance reports that revenue doubled overnight, but the number of orders is the same.
    case first-case: 9 tables written to case/data
    read case/brief.md, then look at the data with shape profile, diff and check
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape profile case/data --dataset --joint -o today.shape --capture full
shape diff case/baseline.shape today.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: warning: --capture full keeps real values in today.shape; do not commit or share it
    {"shape_content_id": "abcae705b3f94a095413f66c481b8e9e69009d9c24ee0c240ffb9823a031dcd6", "written": "today.shape"}
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: case/baseline.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: customer.last_name: null_rate_change [breaking]
    bump: major (1 breaking, 0 additive, 0 cosmetic)
    {"changes": [{"baseline": 0.0, "class": "breaking", "class_reason": "a column that never had nulls has them now (readers may not expect)", "column": "customer.last_name", "current": 0.6, "kind": "null_rate_change", "score": 0.6, "severity": "medium"}], "drifted": true, "semver": {"additive": 0, "breaking": 1, "bump": "major", "cosmetic": 0}}
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
shape detective hint first-case 1                     # a hint, counting from 1
shape detective check first-case --answer answer.json
```

??? info "Output (exit 1)"

    ```text {.expected}
    Compare the baseline with today's batch table by table; most tables will look the same.
    missed  customer.last_name: null-flood
    not solved
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
