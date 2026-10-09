# Consumer data contracts

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A team that consumes a dataset can state the parts it depends on, as a file, so that the
producing team's CI fails **before** a change breaks them. A consumer contract is a
contract v1 body (what `shape check` runs) limited to what the consumer needs,
plus who the consumer is and whom to call.

Extra tables and extra columns of the producer are **always allowed**: a consumer contract
never forbids anything, and `allow_extra_columns: false` is rejected.

## Writing a contract (consumer team)

```json
{
  "format": "shape-consumer-contract",
  "version": 1,
  "consumer": "finance-reporting",
  "owner": "finance-data@example.com",
  "source": "orders",
  "since": "2026-10-03",
  "requires": {
    "tables": {
      "orders": {
        "required_columns": ["order_id", "amount"],
        "columns": {
          "amount": {"dtype": "float", "max_null_rate": 0.0},
          "status": {"allowed_values": ["new", "paid", "shipped"]}
        },
        "row_count": {"min": 1000}
      }
    }
  }
}
```

| Key | Meaning |
|---|---|
| `format`, `version` | always `shape-consumer-contract` and `1` |
| `consumer` | the consuming team or system (letters, digits, `.`, `_`, `-`) |
| `owner` | who to contact when this contract breaks |
| `source` | the producer's source name in its `shape.yml` |
| `since` | the date the dependency started, `YYYY-MM-DD` |
| `requires` | a contract v1 body: `tables` (table name to rules), or for a single-table producer the rules themselves. Rules per table: `row_count` (`min`, `max`), `required_columns`, `columns` (`dtype`, `nullable`, `unique`, `max_null_rate`, `pattern`, `allowed_values`, `min`, `max`, `distribution`, `min_true_rate`, `max_true_rate`, `no_placeholder`), and the joint rules `fd`, `implies`, `reference_pair`, `max_implausible_rate` |

Name only what the consumer reads. Every extra rule is a promise the producer has to keep.

`shape contracts validate FILE` checks a file. It reports **every** problem with its key path
(for example `$.requires.tables.orders.columns.amount: unknown rules ...`) and exits 2, or exits
0 when the file is valid. The JSON Schema is `src/shape/schemas/shape-consumer-contract-v1.schema.json`.

Commit the file to the **producer's** repository, under `contracts/consumers/` (next to
`shape.yml`; sub-folders are fine), by pull request to the producing team. Collecting contracts
from other repositories is not automated: consumers commit their files, or CI copies them in.

## Running them (producer CI)

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

It runs every consumer contract in `DIR` (default `contracts/consumers/` next to `shape.yml`,
found from the working folder upwards) whose `source` matches the source. The source is
`--source`, else the only source of `shape.yml`, else the one named like the profile. Without a
`shape.yml`, `--source` names it. The text report is one block per consumer:

```
FAIL  finance-reporting  owner: finance-data@example.com
      orders.amount: required_column expected 'present', observed 'missing'  producer owner: finance-data@example.com
PASS  marketing
1 of 2 consumers pass
```

`producer owner` is the column's `owner` in the producer's `shape.yml`.

| Exit | Meaning |
|---|---|
| 0 | every consumer passes (or none matches the source, unless `--require-consumers`) |
| 1 | a consumer's contract is broken |
| 2 | unusable input: an invalid consumer file (every problem of every file is listed), two files for one consumer, an unknown source, a profile that is not a `.shape` profile, a missing `--consumers` folder, or no consumer contract for the source with `--require-consumers` |

A consumer contract that cannot be checked against the producer's profile at all (it names
`tables` and the producer profile is a single table) fails that consumer with the rule
`contract_not_applicable`.

### What a change breaks

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

A rule that needs a value the profile does not hold because it was captured safe (the default
`.shape`, `docs/PRIVACY_MODEL.md`: a `min` or `max` of a column whose extremes were removed, an
`allowed_values` set on a sensitive column) is never a silent pass: it is a violation whose
`observed` is `not evaluable: COLUMN was captured safe (...)`. Profile the producer's data with
`--capture full` in CI to measure it.

With `--baseline`, each violation carries `new`: true when the rule passed on the baseline and
fails on the new profile. Each consumer gets `broken_by_change` (it has a new violation) and
`already_failing` (it fails, but none of it is new), so a pull request shows which consumers a
change breaks rather than which were already broken. The exit code still follows the plain
rule: any failing consumer exits 1.

### In CI

`shape init` creates `contracts/consumers/` and its workflow example runs, after profiling each
source, `shape contracts check-consumers shapes/current/NAME.shape --source NAME -o
shapes/current/NAME.consumers.json`. To preview a pull request's effect, add `--baseline` with
the profile of the base branch (for example the one `shape registry` holds).

## The report

`shape-consumer-check`, version 1 (JSON Schema: `shape-consumer-check-v1.schema.json`):

```json
{
  "format": "shape-consumer-check", "version": 1, "source": "orders",
  "directory": "contracts/consumers",
  "profile": {"name": "orders", "path": "new.shape", "content_id": "ab12..."},
  "baseline": {"name": "orders", "path": "base.shape", "content_id": "cd34..."},
  "consumers": [{
    "consumer": "finance-reporting", "owner": "finance-data@example.com", "source": "orders",
    "since": "2026-10-03", "file": "contracts/consumers/finance.json", "status": "fail",
    "broken_by_change": true, "already_failing": false,
    "violations": [{"table": "orders", "column": "amount", "rule": "required_column",
                    "expected": "present", "observed": "missing",
                    "producer_owner": "finance-data@example.com", "new": true}]
  }],
  "summary": {"consumers": 1, "passed": 0, "failed": 1, "broken_by_change": 1},
  "passed": false
}
```

`baseline`, `broken_by_change`, `already_failing` and `new` appear only with `--baseline`.

## Versioning

Both files declare `format` and an integer `version`. A consumer contract written for a newer
version is refused with a message, never half read. Frozen version 1 documents live in
`tests/fixtures/consumers/v1/` and must keep loading (`tests/consumers/test_compat.py`).
