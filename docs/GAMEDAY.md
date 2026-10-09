# Game days

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A game day rehearses failures before they happen. You give Shape a plan: a folder of your own local
data and rounds. For each round Shape copies some of your tables, plants a failure from the
[catalog](FAILURE_MODES.md) in the copies, runs the checks you list and records which expectations
your checks detected, which they missed and how long the round took. Your data is never modified.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Exit 0 when every expectation was detected, 1 when one was missed, 2 for a plan that is malformed
or uses something it may not.

## A plan

```json
{
  "format": "shape-gameday",
  "version": 1,
  "data": "data",
  "rounds": [
    {
      "name": "nulls in names",
      "inject": "null-flood",
      "tables": ["orders"],
      "checks": [
        ["profile", "{data}", "--dataset", "-o", "{round}/base.shape", "--capture", "full"],
        ["profile", "{round}", "--dataset", "-o", "{round}/today.shape", "--capture", "full"],
        ["diff", "{round}/base.shape", "{round}/today.shape"]
      ],
      "expect": ["drift:null_rate_change"]
    }
  ]
}
```

- `data` is a local folder (relative to the plan file, or absolute). A path that is not a local
  folder, such as a URL or `abfss://...`, is refused: only local files are used.
- `inject` is `library:NAME` (a scenario) or a failure mode id. It must plant defects in one batch;
  a change over time cannot be injected.
- `tables` lists the files of the data folder to copy, with or without the extension (`.csv`,
  `.parquet`, `.jsonl`). A scenario that touches several tables takes the tables in the order you
  list them; the round must name at least as many as the scenario touches.
- `checks` are Shape subcommands as lists of words. Only `profile`, `diff`, `check`, `verify` and
  `fidelity` are allowed; there are no shell commands. In a word, `{data}` is the source folder,
  `{round}` the folder of this round (`DIR/round-N`) and `{plan}` the folder of the plan file. A
  check may write only below `{round}`.
- `expect` lists the checks that must fire (`drift:KIND`, `rule:RULE` or `gate:NAME`, as in
  [canaries](CANARIES.md); `|` joins alternatives). A check is detected when the `--json` result of
  any check of the round shows it: Shape adds `--json` to your `diff`, `check` and `verify`.

The injection takes the first column of the right kind in each table (the first timestamp column
for a time shift, the first text column for placeholders, and so on), plants the scenario's
defects there and records which column it used, so you can see what was hit.

## What a run writes

```
gameday/
  round-1/            the copied tables, with the failure planted, and whatever the checks wrote
  round-2/
  gameday_report.json
  gameday_report.md
```

`gameday_report.json` is `{"format": "shape-gameday-report", "version": 1, ...}` with `plan`, `data`,
`seed`, `detected`, `seconds` and one entry per round: `name`, `inject`, `scenario`, `tables`,
`injected` (kind, table, column, rows), `checks` (command, exit code, seconds), `fired` (every check
that showed up), `expectations` (each with `detected`), `detected` and `seconds`. The Markdown report
shows the same.

## What is checked before the first round

The whole plan is read first, and nothing is copied or run until it passes: the format and version
(a newer version is refused with an upgrade message), the data folder, every injection, every table
(each must exist and be readable), every command (only the five allowed words, writing only below
`{round}`) and every expectation (a check Shape has). The output folder must be new or empty and must
not overlap the data folder. The source folder is hashed before and after in the tests: a run never
changes a byte of it.
