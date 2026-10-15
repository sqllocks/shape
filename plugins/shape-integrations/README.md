# sqllocks-shape-integrations

Thin, optional adapters between Shape and OpenLineage, MLflow, Presidio, SDMetrics, Anonymeter
and Ibis/DuckDB. One distribution, with extras for the available integrations:

```
pip install 'sqllocks-shape-integrations[openlineage]'
```

See [docs/plugins/integrations.md](../../docs/plugins/integrations.md) for each integration:
install, command or API, what is sent or written, and what is never sent.

Anonymeter adapter: **withdrawn in 0.9.4: Anonymeter 1.x requires NumPy < 1.27; Shape requires NumPy >= 2**. The adapter code stays for independently installed Anonymeter in a separate environment. The Anonymeter report becomes available again when Anonymeter supports NumPy 2. Anonymeter measures re-identification risk in a dataset; it does not change the data.
