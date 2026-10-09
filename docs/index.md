# Shape by SQLLocks

Profile your data, check a contract, compare drift and generate development rows.

Status: available.

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

```mermaid
flowchart LR
    A[Your data] --> P[Profile]
    P --> C[Check a contract]
    C --> D[Diff against a baseline]
    D --> G[Generate development data]
```

Start with [your first profile](tutorials/01-first-profile.md). You create a local CSV,
save a `.shape` file and read its report. No cloud account is needed.
Then [write a contract](tutorials/04-contract.md),
[catch drift](tutorials/03-drift.md) and
[generate from your profile](tutorials/05-generate-profile.md).
Use [learning paths](LEARNING_PATHS.md) to choose the next step for your role.

A profile describes a table's types, counts, nulls, distributions and relationships.
A contract states what your data must satisfy. A diff shows how its behavior changes.
A safe capture is data minimisation, not anonymisation. You review it before sharing.

## Where platforms plug in

```mermaid
flowchart TB
    F[Local files / DuckDB / PostgreSQL / MySQL] --> P[Profile]
    L[OneLake / ADLS / Delta files] --> P
    P --> C[Contract and drift checks]
    P --> G[Generation]
    G --> DB[DuckDB / PostgreSQL / MySQL]
    G --> W[Snowflake / Databricks write targets]
    G --> M[Fabric writers / local files]
    C --> CI[GitHub Actions / pipeline exit codes]
    M --> BI[Semantic model exports for Power BI]
```

[Database pages](databases/index.md) describe shipped adapters. Snowflake and Databricks
are write targets; Shape does not profile from them yet. Cloud examples needing an account
stay owner placeholders until their commands have been run.

## Choose your next page

[Install](INSTALL.md) · [Concepts](CONCEPTS.md) · [Read the report](READ_REPORT.md) ·
[Troubleshooting](TROUBLESHOOTING.md) · [Known limitations](KNOWN_LIMITATIONS.md) ·
[Python API](API.md) · [Changelog](CHANGELOG.md)

Industry Profile Packs are coming with Premium, healthcare first:
[shapedata.ai](https://shapedata.ai/).
