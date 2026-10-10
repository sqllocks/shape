# Canaries

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" CANARIES
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for CANARIES
    ```


A canary is a small synthetic batch with known planted failures. You send it through a real
pipeline input on a schedule. If your checks do not flag it, your monitoring is blind: it would
not notice the real thing either.

[Run this example](#local-example-0).


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

[Run this example](#local-example-2).


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


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape canary make null-flood -o canary/            # a failure mode of the catalog
shape canary make library:duplicate_rows -o duplicate-canary/ --rows 500 --marker is_test=yes
```

??? info "Output (exit 0)"

    ```text {.expected}
    wrote canary/customer.csv
    wrote canary/canary.json
    expects: drift:null_rate_change, gate:null_check, rule:nullable
    wrote duplicate-canary/return.csv
    wrote duplicate-canary/canary.json
    expects: gate:uniqueness, rule:unique
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape diff baseline.shape today.shape --json - > diff.json
shape check today.shape contract.json --json - > check.json
shape verify out/ --schema gates.json --json > verify.json
shape canary check canary/canary.json --result diff.json --result check.json --result verify.json
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: baseline.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    bump: none (0 breaking, 0 additive, 0 cosmetic)
    shape: note: today.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    Shape 0.9.1 - Verify

    Data path:   out/
    Schema:      gates.json
    Statistical: no

    Gate                         Status   Errors Warnings
    -------------------------------------------------------
    schema_conformance           PASS          0        7
    null_constraint              PASS          0        0
    unique_constraint            PASS          0        0
    referential_integrity        PASS          0        0

    Row counts:
      customers: 20
      orders: 100

      WARN  [schema_conformance]: Table 'orders' column 'order_id': expected type compatible with 'string', got 'int64'
      WARN  [schema_conformance]: Table 'orders' column 'customer_id': expected type compatible with 'string', got 'int64'
      WARN  [schema_conformance]: Table 'orders' column 'amount': expected type compatible with 'string', got 'float64'
      WARN  [schema_conformance]: Table 'orders' column 'order_total': expected type compatible with 'string', got 'float64'
      WARN  [schema_conformance]: Table 'orders' column 'is_gift': expected type compatible with 'string', got 'bool'
      WARN  [schema_conformance]: Table 'orders' column 'churned': expected type compatible with 'string', got 'bool'
      WARN  [schema_conformance]: Table 'orders' column 'salary': expected type compatible with 'string', got 'int64'

    Result: PASS
    BLIND SPOT  drift:null_rate_change: your checks did not flag it
    BLIND SPOT  gate:null_check: your checks did not flag it
    BLIND SPOT  rule:nullable: your checks did not flag it
    your monitoring is blind
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
