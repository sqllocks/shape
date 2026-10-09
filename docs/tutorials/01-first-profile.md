---
status: available
post:
video:
---
# Your first profile

Generate a retail dataset and save a profile of its tables and relationships.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

Generate a retail dataset and save a profile of its tables and relationships.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate retail data

```bash {.runnable}
shape generate retail --scale small --seed 42 --format csv -o retail --json > generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

You start with a shipped domain rather than rows you type by hand. The seed fixes the random choices for this run. The small scale produces enough customers, orders and order lines to make relationships visible, while keeping the work local. `--format csv` writes table files into `retail`; `--json` sends the run result to `generation.json`. Redirecting that result leaves no terminal output, which is why the block says no output. It does not mean no data was written. Keep the input folder separate from reports and profiles so your next folder scan reads only table data.

## 2. Inspect the generated tables

```bash {.runnable}
python - <<'PYDATA'
import json
from shape.generation.domains import load_domain
run = json.load(open('generation.json'))
for table, count in sorted(run['counts'].items()):
    print(f'{table}: {count} rows')
month = load_domain('retail').schema.tables['order'].columns['order_date'].generator['profiles']['month']
print(f'Month weights: Jan={month["Jan"]}, Nov={month["Nov"]}, Dec={month["Dec"]}')
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    address: 1500 rows
    customer: 1000 rows
    order: 5000 rows
    order_line: 12500 rows
    product: 500 rows
    product_category: 50 rows
    promotion: 200 rows
    return: 850 rows
    store: 150 rows
    Month weights: Jan=0.071, Nov=0.096, Dec=0.106
    ```

Notice that orders and order lines have different row counts. A line is a child of an order; one order can have several lines. The month weights come directly from the shipped retail schema, not from a claim about a particular business. November and December have larger weights than January. They are generator settings, so a finite seeded sample need not reproduce the weights exactly. This is a useful distinction when you read a profile: the schema describes the inputs to generation, while a profile describes the rows actually produced. You inspect both before deciding what a development fixture should represent.

## 3. Profile the folder

```bash {.runnable}
shape profile retail/ --dataset --name retail -o retail.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "42e4dd7f6e98fe34fe46e06fb5ae77b1ba95d2ff50b71551a8f19af214262286", "written": "retail.shape"}
    ```

`--dataset` gives each file its own table in one profile. `--name retail` gives that profile a stable name. The command writes an artifact, an HTML report and a JSON summary; these are three views of the same profiling run. The long content id identifies the captured profile body. It is not a row count or an indication that generation succeeded. The command uses a safe capture by default. A safe capture is data minimisation, not anonymisation. Review what is retained before you share an artifact from your own data, even when the command itself succeeds.

## 4. Read the relationships

```bash {.runnable}
python - <<'PYDATA'
import json
s = json.load(open('summary.json'))
print(f'Tables: {len(s["tables"])}; total rows: {s["row_count"]}')
for r in s['relationships']:
    print(f'{r["child"]}.{",".join(r["child_columns"])} -> {r["parent"]}.{",".join(r["parent_columns"])}')
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Tables: 9; total rows: 21750
    address.customer_id -> customer.customer_id
    order.customer_id -> customer.customer_id
    order.store_id -> store.store_id
    order.promotion_id -> promotion.promotion_id
    order_line.order_id -> order.order_id
    order_line.product_id -> product.product_id
    order_line.promotion_id -> promotion.promotion_id
    return.order_id -> order.order_id
    ```

The folder profile finds customer references from addresses and orders, order references from order lines and returns, and other links between the generated tables. These links describe observed keys and values. They do not establish that an upstream database declared a foreign-key constraint. Follow one link: an order's `customer_id` refers to the customer table, while `order_id` identifies the order itself. That distinction matters when you write a contract or generate another set of related tables. A missing table, a renamed key or values outside the parent key set can change what a later profile finds.

## 5. Check the capture before sharing

```bash {.runnable}
shape profile validate --safe retail.shape
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: retail.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    LEAK: 3 finding(s) in retail.shape:
      [raw-string-list] $.tables.address.sampling.internal[0].columns: list under 'columns' carries 5 raw strings (more than 2): a value dump. Sample: ['address_type', 'city', 'state']
      [raw-string-list] $.tables.order.sampling.internal[1].columns: list under 'columns' carries 5 raw strings (more than 2): a value dump. Sample: ['customer_id', 'order_id', 'order_total']
      [raw-string-list] $.tables.order_line.sampling.internal[0].columns: list under 'columns' carries 8 raw strings (more than 2): a value dump. Sample: ['discount_percent', 'line_total', 'order_id']
    ```

This final command intentionally demonstrates a current limitation. The multi-table dataset validator reports findings and exits 1, even though the capture was written in safe mode. Do not read that failure as proof that the data is ready to share, and do not silently bypass it in CI. The single-table path is shown in the DuckDB tutorial. Keep this dataset local while you inspect its report. You now have a repeatable generation input, a saved profile and an explicit validation result; each answers a different question about the same workflow.

## What's next

[Read the report](02-read-report.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
