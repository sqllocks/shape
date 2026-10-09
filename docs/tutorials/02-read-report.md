---
status: available
post:
video:
---
# Read the report

Use the retail report to distinguish identity, nulls, distributions and relationships.

Status: available. Generation from a profile is being hardened.

You can run this walkthrough in an empty working directory without a cloud account. Each command below is followed by the complete output from a repository build. Run the steps in order: later commands use the files written earlier. When a command intentionally fails, the expected exit code is shown beside its output. Read the explanation after the output before moving on; the verdict and its evidence matter more than the presence of a new file.

## What you'll learn

Use the retail report to distinguish identity, nulls, distributions and relationships.

## Prerequisites

Install Shape with the domains extra; see [Install](../INSTALL.md). For the DuckDB walkthrough, install the databases and integrations plugins with their DuckDB extras. You need Python 3.11 or newer; the drift walkthrough also uses Git. Start in an empty directory and keep the generated rows local.

## Time

About 15 minutes.

## 1. Generate the source tables

```bash {.runnable}
shape generate retail --scale small --seed 42 --format csv -o retail --json > generation.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

You use the same retail domain and seed as the first tutorial. The files are generated development data, and their schema includes customer, address, product, promotion, store, order, order-line and return tables. Keep the folder in place while you review the report: a report points you toward questions, but you may still need the source rows to answer them. The redirected run result records counts and paths if you need to check which files were written. Avoid combining a stale report with a newly generated folder; produce both from one run so that the evidence you read stays connected.

## 2. Create the report

```bash {.runnable}
shape profile retail/ --dataset --name retail -o retail.shape --html report.html --json summary.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "42e4dd7f6e98fe34fe46e06fb5ae77b1ba95d2ff50b71551a8f19af214262286", "written": "retail.shape"}
    ```

The profile command scans the folder and writes `report.html` together with the saved artifact and the JSON summary. Open the HTML file in your browser. It is a local report, so opening it does not require a hosted documentation service or a database account. Begin with the table overview and check the row counts before jumping to a distribution chart. If the count is not the population you intended, statistics for that population will not answer your original question. Next, choose the order table. You will use its identity and optional promotion reference to practice reading the columns.

## 3. Read the order columns

```bash {.runnable}
python - <<'PYDATA'
import json
from pathlib import Path
s = json.load(open('summary.json'))
t = s['tables']['order']
print('Order primary key:', ', '.join(t['primary_key']))
for name in ('order_id', 'customer_id', 'promotion_id', 'order_total'):
    c = t['columns'][name]
    print(f'{name}: type={c["dtype"]}; null_rate={c["null_rate"]:.3f}; cardinality={c["cardinality"]}; foreign_key={c["is_foreign_key"]}')
print('HTML report exists:', Path('report.html').is_file())
PYDATA
```

??? info "Output (exit 0)"

    ```text {.expected}
    Order primary key: order_id
    order_id: type=integer; null_rate=0.000; cardinality=5000; foreign_key=False
    customer_id: type=integer; null_rate=0.000; cardinality=940; foreign_key=True
    promotion_id: type=integer; null_rate=0.693; cardinality=200; foreign_key=True
    order_total: type=float; null_rate=0.000; cardinality=2867; foreign_key=False
    HTML report exists: True
    ```

`order_id` is unique in this sample and has no nulls. The report identifies it as the primary key. `customer_id` is different: it repeats because customers place several orders, and it refers to the customer table. `promotion_id` may be null because an order need not use a promotion; a null is not automatically a broken record. `order_total` is numeric, so its distribution, mean and spread provide a useful baseline for later batches. The printed summary is a compact way to locate these facts, not a replacement for the HTML sections. Read the type, null rate and cardinality together. A column with many distinct values can be a key, a timestamp or a measurement; cardinality alone does not decide its role.

## 4. Follow the relationship section

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

Find each printed child-to-parent link in the report. Start with orders to customers, then follow order lines to orders and products. This shows why a folder profile is different from profiling an isolated CSV: the profiler can examine the key sets across tables. Review inferred links before treating them as a contract. A shared column name helps you investigate a relationship, but the names alone do not make it correct. Likewise, a generated dataset's valid links do not guarantee that the next incoming batch has them. Save this report as the baseline you understand, then use an explicit contract or drift check to test later data.

## What's next

[Commit a baseline and catch drift](03-drift.md). Keep the artifacts you reviewed as evidence for the next decision. If a verdict differs on your machine, check that the input, seed, profile options and target file match before changing a threshold or replacing a baseline. Do not make a failing gate pass by deleting the rule that identified the problem.

## Related

[Concepts](../CONCEPTS.md) · [Reading the HTML report](../READ_REPORT.md) · [Known limitations](../KNOWN_LIMITATIONS.md) · [Troubleshooting](../TROUBLESHOOTING.md)
