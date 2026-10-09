---
status: available
post:
video:
---
# Commit a shape and catch drift

Keep a reviewed retail baseline in Git and make a real drift check fail.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

Keep a reviewed retail baseline in Git and make a real drift check fail.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate a clean batch

```bash {.runnable}
shape generate retail --scale small --seed 42 --format csv -o retail --json > generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

Generate the small retail dataset with seed 42. You use generated rows so the exercise can change them without touching a production feed. The run result goes to a JSON file; the CSV files are the inputs to profiling. A drift comparison needs two observations of the same thing. Here, both observations use the retail folder and the same profile name. You change one column later, which makes the reason for the failure easy to trace instead of introducing several unrelated differences at once.

## 2. Save the baseline

```bash {.runnable}
shape profile retail/ --dataset --name retail -o baseline.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "42e4dd7f6e98fe34fe46e06fb5ae77b1ba95d2ff50b71551a8f19af214262286", "written": "baseline.shape"}
    ```

The baseline captures the current types, nulls, counts and relationships. Treat it as a reviewed expectation rather than the newest file you happen to have. The content id lets you identify its body even if the artifact is copied or renamed. A safe capture reduces retained values; it does not remove the need to review the artifact. For this local exercise you keep the dataset profile on your machine, because the current multi-table safe validator has a documented limitation. Profiling success and approval to share are separate decisions.

## 3. Commit the baseline

```bash {.runnable}
git init -q --initial-branch=main
git config user.name 'Shape Tutorial'
git config user.email 29076762+sqllocks@users.noreply.github.com
git add baseline.shape
git -c core.hooksPath=/dev/null commit -q -s -m 'Record the retail baseline'
printf 'Committed baseline.shape\n'
```

??? info "Output (exit 0)"

    ```text {.expected}
    Committed baseline.shape
    ```

The local Git repository contains the saved profile, not the generated CSV folder. The commit gives you a reviewable point to compare against and a message explaining what you accepted. The example adds a DCO sign-off. It disables hooks only for this disposable tutorial commit, so the result does not depend on hooks installed on your machine. In your project, keep the normal contribution checks. When you update a baseline through a pull request, reviewers can examine the data change and the reason for accepting it together.

## 4. Introduce a change

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

The script replaces half the order totals with empty CSV fields. It preserves the header, row count, keys and the other columns. After CSV parsing, these fields become nulls in a numeric column. This resembles a mapping or ingestion defect: orders still arrive, but the total is absent for part of the batch. You know exactly how many rows changed because the script reports the count. Keep that fact in mind when you read the diff, rather than treating a red check as an unexplained warning.

## 5. Profile the changed batch

```bash {.runnable}
shape profile retail/ --dataset --name retail -o retail.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "a38384fc705fbd5c1732b5f1b83e3bcb4609d16b587af6349b529ff5bf6b7718", "written": "retail.shape"}
    ```

Save the changed folder to `retail.shape`; leave `baseline.shape` intact. The different content id reflects the changed profile body. If you overwrote the baseline before comparing, you would lose the observation the check needs. File names are a convenient convention, but the comparison is between the two saved profiles, not between their names. Use the same profiling options on both sides so that a change in capture settings does not distract from the change in the rows.

## 6. Read the failing diff

```bash {.runnable}
shape diff baseline.shape retail.shape --fail-on-drift --json drift.json
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: retail.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: baseline.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: order.order_total: null_rate_change [breaking]
    bump: major (1 breaking, 0 additive, 0 cosmetic)
    ```

The important line names `order.order_total`, reports `null_rate_change` and classifies it as breaking. The baseline had no null totals; the current batch has a null rate of 0.5. `--fail-on-drift` makes the process exit 1, which is the signal CI can use to stop a job. The JSON result is saved separately for investigation. The default command also reports the proposed compatibility bump; that describes the change class and does not publish a package version. Fix the missing totals if the change is accidental. Accept a new baseline only if the changed data behavior is intentional and its consumers can handle it.

## What's next

[Write a contract](04-contract.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
