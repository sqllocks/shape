# Data detective packs

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A detective pack is a case: a generated batch of data with problems planted in it, a brief, hints
and a way to check your answer. You look at the batch with Shape's own commands (`shape profile`,
`shape diff`, `shape check`), say which table and column hold which failure mode, and Shape tells
you what you found, what you missed and what you got wrong. It is the quickest way to learn what
each check can and cannot see.

The data is never shipped. A pack names a [library scenario](SCENARIO_LIBRARY.md) that plants the
problems and a seed, so `start` generates the same case every time. The failure modes are those of
the [failure mode catalog](FAILURE_MODES.md).

## How to play

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`case/` holds `data/` (one Parquet file per table), `baseline.shape` (the profile of the clean batch
this one should look like) and the case's brief, a Markdown file (brief.md). Then look:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

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

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

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
