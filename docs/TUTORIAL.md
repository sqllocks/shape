# Starter tutorials

Learn the profile → check → diff → generate workflow with local data.

Status: available.

Complete these tutorials in order. Each page also runs from an empty working directory, so you
can repeat one without depending on files from another. The runnable blocks and their complete
outputs are checked in CI. Installation is a prerequisite; see [Install](INSTALL.md).

1. [Your first profile](tutorials/01-first-profile.md).
2. [Read the report](tutorials/02-read-report.md).
3. [Commit a shape and catch drift](tutorials/03-drift.md).
4. [Write a contract and check it](tutorials/04-contract.md).
5. [Generate dev data from a profile](tutorials/05-generate-profile.md).
6. [Generate a domain and load it into DuckDB](tutorials/06-domain-duckdb.md).

After the first profile, check its requirements with a contract. Keep the reviewed profile as a
baseline. Compare a later extract before replacing it. Then generate development rows from the
profile with an explicit output format. Generation without a format writes nothing.

The capture, show, query and compatibility commands operate on models. They are a separate
reference surface described in [Models](MODELS.md), not prerequisites for these tutorials.

## What's next

Choose [your role's learning path](LEARNING_PATHS.md).

## Related

[Quickstart](QUICKSTART.md) · [Concepts](CONCEPTS.md) · [Troubleshooting](TROUBLESHOOTING.md)
