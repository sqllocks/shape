---
status: available
post:
video:
---
# Write a contract and check it

State retail identity and total requirements, then observe a real violation.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

State retail identity and total requirements, then observe a real violation.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate the input

```bash {.runnable}
shape generate retail --scale small --seed 42 --format csv -o retail --json > generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

Generate the same seeded retail tables used by the other starter tutorials. A contract states what you require of those rows; it is not a list of every statistic the profiler happens to measure. Begin with a few rules whose purpose you can explain. Here the customer and order ids identify records, and order totals must exist and be nonnegative. These are requirements you choose for this exercise. Do not turn an observed business pattern into a mandatory rule without deciding whether future batches are allowed to differ.

## 2. Capture the dataset

```bash {.runnable}
shape profile retail/ --dataset --name retail -o retail.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "42e4dd7f6e98fe34fe46e06fb5ae77b1ba95d2ff50b71551a8f19af214262286", "written": "retail.shape"}
    ```

The folder profile includes several named tables. This matters for the contract shape: table-specific rules belong inside a `tables` object. A one-table contract applied to a dataset is the wrong kind of input, not a way to choose the first table. Keep the report and summary so you can inspect the baseline before writing the rules. The safe capture reduces retained values, so some value-based checks may be unavailable; a successful profile command does not guarantee that every possible rule can be evaluated from the saved artifact.

## 3. Write the rules

```bash {.runnable}
python - <<'PYDATA'
import json
contract = {'tables': {'customer': {'columns': {'customer_id': {'unique': True, 'nullable': False}}}, 'order': {'columns': {'order_id': {'unique': True, 'nullable': False}, 'order_total': {'nullable': False, 'min': 0}}}}}
json.dump(contract, open('contract.json', 'w'), indent=2)
print('Wrote contract.json: customer identity, order identity and nonnegative totals')
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote contract.json: customer identity, order identity and nonnegative totals
    ```

The file names customer and order explicitly. For each identity column, `unique` requires distinct values and `nullable: false` requires every record to have a value. For the total, `min: 0` states a lower bound rather than assuming that its mean stays constant. This keeps the contract focused on validity while drift handles changes in behavior. Review the actual JSON file if you want to extend it. A typo or unsupported rule is an input error; do not replace a malformed contract with an empty one merely to make a check pass.

## 4. Check the clean profile

```bash {.runnable}
shape check retail.shape contract.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: retail.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"passed": true, "violations": []}
    ```

The check reports `passed: true` and no violations. That result covers the rules you wrote. It does not certify every relationship, column or business rule in the dataset. For example, this contract does not state how a return relates to an order or how an order total is calculated from its lines. Add those requirements when your workflow needs them and you have the required input. The unsigned-artifact note concerns origin verification; it is separate from whether the contract's data requirements pass.

## 5. Make totals missing

```bash {.runnable}
python - <<'PYDATA'
import csv
from pathlib import Path
p = Path('retail/order.csv')
with p.open(newline='') as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)
for row in rows[::2]:
    row['order_total'] = ''
with p.open('w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
print(f'Set order_total to null in {len(rows[::2])} of {len(rows)} orders')
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Set order_total to null in 2500 of 5000 orders
    ```

Use the same controlled change as the drift tutorial: blank out every other order total. The keys remain present, so the identity rules can still pass while the total rule fails. This demonstrates why a single overall verdict needs its violation details. You are not testing whether the generator normally creates missing totals. You are testing whether your contract catches the defect you deliberately introduced into its output.

## 6. Profile the changed rows

```bash {.runnable}
shape profile retail/ --dataset --name retail -o changed.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "a38384fc705fbd5c1732b5f1b83e3bcb4609d16b587af6349b529ff5bf6b7718", "written": "changed.shape"}
    ```

The saved changed profile is a new observation. A contract check reads the profile you give it; editing the CSV does not update an earlier artifact automatically. This is an easy mistake in a local workflow and in CI: a check can pass against a stale profile while the new files are broken. Name or place the artifacts so you can tell which batch each command uses. Keep the clean artifact for comparison rather than overwriting every file with the same path.

## 7. Read the violation

```bash {.runnable}
shape check changed.shape contract.json
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: changed.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"passed": false, "violations": [{"column": "order.order_total", "expected": false, "observed": {"null_count": 2500}, "rule": "nullable"}]}
    ```

The command exits 1. The violation names `order.order_total`, the `nullable` rule and the observed count of 2,500 nulls. That gives you a concrete repair target. Restore the totals if they were lost during ingestion; change the contract only if null totals are part of the intended data behavior and consumers agree. A contract and a drift baseline serve different purposes: the contract states requirements, while the baseline lets you notice changes that may still satisfy those requirements. Use both when your pipeline needs validity checks and behavior checks.

## What's next

[Generate development data from the profile](05-generate-profile.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
