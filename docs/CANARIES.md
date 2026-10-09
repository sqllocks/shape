# Canaries

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A canary is a small synthetic batch with known planted failures. You send it through a real
pipeline input on a schedule. If your checks do not flag it, your monitoring is blind: it would
not notice the real thing either.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`make` writes one file per table the failure touches (`--format csv|parquet|jsonl`, default CSV)
and `canary.json`. Every row of every file carries a marker column (default `shape_canary=1`, or
`--marker COLUMN=VALUE`), so downstream jobs can filter the canary out of real reports. `--rows N`
sets the rows per table (default 1000), `--seed N` the seed. `--dry-run` prints what would be
written and writes nothing. A canary is written to a local folder only; a remote target is refused.

## What a canary expects

```json
{"format": "shape-canary", "version": 1, "id": "canary-null_flood-42",
 "scenario": "library:null_flood", "seed": 42,
 "marker": {"column": "shape_canary", "value": "1"},
 "expected": ["drift:null_rate_change", "gate:null_check", "rule:nullable"]}
```

`expected` is the checks that must fire: the `detected_by` of the catalog entry
(`docs/FAILURE_MODES.md`), or, for a scenario with no catalog entry, its answer key. A check is
`drift:KIND` (a change kind of `shape diff`), `rule:RULE` (a rule of `shape check`) or `gate:NAME`
(a gate of `shape verify`); alternatives joined by `|` are satisfied by any one. `make` runs the
batch against a clean one first and refuses (exit 2) when an expected check does not fire at this
size, so a canary never expects what it cannot show; use more `--rows`.

A canary comes from a scenario that plants defects in one batch. A failure that is a change over
time (a column added, a category shift) cannot be a canary, and neither can a failure mode that no
check of Shape detects (the catalog says which): both are refused with exit 2.

## Checking your pipeline

Run your own checks on the batch the pipeline received, each with `--json`, and give the results to
`check`:

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`check` reads the `shape-result` documents (`docs/CI.md`): a `diff` lists its changes, a `check` its
violations and a `verify` its failed gates. A gate named in the catalog by its scenario name
(`null_check`, `uniqueness`) counts when `shape verify` reports its own name for it
(`null_constraint`, `unique_constraint`).

It exits 0 when every expected detection is present, 1 when one is missing (each missing check is
printed as a `BLIND SPOT`), and 2 for a canary or a result that is malformed, a result of a command
that says nothing about checks (such as `profile`), or no `--result` at all. `--json` prints the
report as one document.

## Using it

Run `make` once and keep the folder, or make a new canary per run with a changing `--seed`. Send the
files through the same input as real data, run your usual checks and `canary check` the results from
the same job, and alert on exit 1. Filter on the marker column everywhere real numbers are
produced.
