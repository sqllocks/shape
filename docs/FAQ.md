# FAQ

Answer the questions that arise in the first local workflow.

Status: available.

## Do I need a cloud account?

No account is needed for the six local starter tutorials. You create a CSV and a DuckDB file.
Cloud adapters use the endpoint and credentials you configure; they are separate workflows.

## Does a shape contain my rows?

A profile holds measured evidence, not a row export. A safe capture reduces retained values,
but categories and aggregates can still disclose facts. A full capture retains frequent values
and extremes. A safe capture is data minimisation, not anonymisation.

## Can I query a profile?

The model query interface rejects profiles. A saved profile has inspection and report interfaces.
The `.shape` extension can contain either kind, so check the payload before choosing a command.

## Why did generate create no files?

The CLI defaults to a summary. For profile generation, select a format explicitly.
The Python API returns tables; you write those tables yourself, as the tutorial does.

## What does a seed promise?

It controls the run's deterministic choices. Reproducibility also depends on versions,
generation parameters and reference data. Do not assume a seed recreates source rows or that
all measured properties survive profile fitting.

## Does Shape read Snowflake and Databricks?

Not yet. The databases plugin writes to them. PostgreSQL, MySQL and DuckDB reads come from
the integrations plugin. See [Databases](databases/index.md).

## How do I ask for a platform or domain?

Use the repository's request templates. Include read/write direction, expected behavior and
small fixtures or schemas. Do not include credentials or production rows.

## Related

[Start here](LEARNING_PATHS.md) · [Troubleshooting](TROUBLESHOOTING.md)
