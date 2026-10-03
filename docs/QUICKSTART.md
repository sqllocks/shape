# Five-minute Shape quickstart

Capture a table (CSV, Parquet, Delta, JSONL) into a portable Shape:

```bash
shape capture customers.csv -o customers.shape
```

Inspect evidence without reopening the source rows:

```bash
shape show customers.shape
shape query customers.shape 'column("age").mean'
```

Compare behavior over time:

```bash
shape capture customers_today.csv -o customers-today.shape
shape compatibility customers.shape customers-today.shape --mode backward
```

Check a Parquet (or Delta, JSONL, `abfss://`) feed for schema changes the same way; a renamed,
dropped or retyped column is reported and the command exits 5:

```bash
shape capture orders.parquet -o orders-base.shape
shape capture orders-today.parquet -o orders-today.shape
shape compatibility orders-base.shape orders-today.shape
```

Use `shape plan customers.shape` to see reconstruction support and fidelity expectations. Use the Python generation/domain APIs for high-volume synthetic reconstruction, relational keys, addresses/locations, temporal behavior and scenarios.
