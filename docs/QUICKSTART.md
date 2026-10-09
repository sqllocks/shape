# Quickstart

Follow one short route from local rows to a reviewed baseline and generated data.

Status: available.

Start with [Your first profile](tutorials/01-first-profile.md). Its tested commands create
forty local rows, profile them, save a safe `.shape` file and validate the capture. You can
open the HTML file locally; it needs no account.

Next, [write a contract](tutorials/04-contract.md). You state that the id must be unique
and non-null and that an amount cannot be negative. Run the check against the saved profile
and read the observed output. A zero exit code means those rules pass; an unavailable rule
is not a pass.

Then [commit a shape and catch drift](tutorials/03-drift.md). Keep the profile in an isolated
Git repository, change the input, profile again and compare the artifacts. The example includes
the expected nonzero drift exit code. Review a change before accepting a baseline.

Finally, [generate dev data from a profile](tutorials/05-generate-profile.md). Use an explicit
format and output directory. Generation from a profile is available and is being hardened.
Inspect the rows instead of assuming every property of the original data is reproduced.

## What's next

[Read the report](tutorials/02-read-report.md) or
[generate a domain into DuckDB](tutorials/06-domain-duckdb.md).

## Related

[Install](INSTALL.md) · [Learning paths](LEARNING_PATHS.md) · [Known limitations](KNOWN_LIMITATIONS.md)
