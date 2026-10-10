---
status: available
post:
video:
---
# Generate dev data from a profile

Fit development rows from the retail profile and inspect what was preserved.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

Fit development rows from the retail profile and inspect what was preserved.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate the reference dataset

```bash {.runnable}
shape generate retail --scale small --seed 42 --format csv -o retail --json > generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

Begin with the shipped retail domain and a fixed seed. Its schema gives you an input with multiple tables and relationships to profile. This initial generation uses the domain definition directly. The later generation uses the saved profile instead, which is a different source of information. Keep that distinction visible when you evaluate the result: a profile summarizes observed rows and does not carry every business rule from the original generation schema. You are building a development fixture from those summaries, not replaying the original run byte for byte.

## 2. Save the source profile

```bash {.runnable}
shape profile retail/ --dataset --name retail -o retail.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "42e4dd7f6e98fe34fe46e06fb5ae77b1ba95d2ff50b71551a8f19af214262286", "written": "retail.shape"}
    ```

Profile the entire folder under one dataset name. The artifact records table and column summaries plus detected relationships; the report lets you inspect that evidence. Safe capture is the default and can withhold values or extrema. A safe capture is data minimisation, not anonymisation. The choice affects what the fitting step can use. If you need to evaluate a statistic that was withheld, decide how to obtain suitable evidence rather than assuming the fitted rows restore the missing source information.

## 3. Generate from the profile

```bash {.runnable}
shape generate --from retail.shape --scale small --seed 42 --format csv -o dev/ --json > dev-generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: retail.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    profile fit: 449 approximate, 202 not modelled, 623 preserved (see `shape plan`)
    ```

`--from retail.shape` selects the fitted-profile path. `--format csv` is essential: generation from a profile writes no data files without a format. The small scale chooses a fitted schema scale; `--rows N` is for a single-table profile, so it is not used for this dataset. The terminal notes summarize approximate, unmodelled and preserved properties. These are reasons to inspect the output, not promises that every property matches. The run's JSON result is saved to `dev-generation.json`, while the actual table files go into `dev`. Keep source and generated folders separate so you can compare them.

## 4. Inspect the generated files

```bash {.runnable}
python - <<'PYDATA'
import csv, json
from pathlib import Path
run = json.load(open('dev-generation.json'))
for table, count in sorted(run['counts'].items()):
    print(f'{table}: {count} generated rows')
print('CSV files:', len(list(Path('dev').glob('*.csv'))))
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    address: 1500 generated rows
    customer: 1000 generated rows
    order: 5000 generated rows
    order_line: 12500 generated rows
    product: 500 generated rows
    product_category: 100 generated rows
    promotion: 200 generated rows
    return: 850 generated rows
    store: 150 generated rows
    CSV files: 9
    ```

Read the counts rather than assuming each table has the source count. In this run, the printed counts show that the fitted product-category table differs from the domain's original small-scale count. That is an observable limit of this fixture, not a reason to silently replace the output. Decide which counts and relationships matter to your application. If you require exact domain rules, use the domain schema; if you want a profile-shaped fixture, review the fit plan and validate its result. A fixed seed helps make an investigation repeatable, but it is not a fidelity check.

## 5. Profile the development orders

```bash {.runnable}
shape profile dev/order.csv --name order -o generated-order.shape
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "3d672882177a48e977810019c45ef9ae93bd1b37baaf855e2b53e179094d5ba5", "written": "generated-order.shape"}
    ```

You now close the loop: profile a generated table instead of stopping after the writer finishes. This gives you evidence about the actual development rows. Compare types, nulls and distributions with the source report, and use a contract for requirements your tests depend on. The single-table artifact makes it easy to inspect one result before expanding the review to the full generated dataset. Avoid interpreting a new content id as a failure; different generated values can produce a different profile body even when the requirements you care about still pass.

## 6. Review the safe-capture result

```bash {.runnable}
shape profile validate --safe generated-order.shape
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: generated-order.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    LEAK: 1 finding(s) in generated-order.shape:
      [raw-string-list] $.sampling.internal[1].columns: list under 'columns' carries 5 raw strings (more than 2): a value dump. Sample: ['customer_id', 'order_id', 'order_total']
    ```

The validator's real result is shown below, including its finding. Do not suppress a failing check just because the rows were generated. Generation status, profile fidelity and safe-capture validation answer separate questions. Keep this artifact local while you investigate the reported path. The fitting path is available and is being hardened, so use its plan and a check suited to your fixture before putting it in a shared test workflow. A useful next exercise is to state a small contract for the generated orders, then test both the source and generated observations against it.

## What's next

[Load a domain into DuckDB](06-domain-duckdb.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
