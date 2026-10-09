# What leaves my machine

Review capture contents, network paths and installed plugin trust.

Status: available.

A safe capture is data minimisation, not anonymisation. This page describes shipped behavior,
not an approval to share data. Each claim below names its implementation source.

## What you save

| Behavior | Code evidence |
|---|---|
| A profile records counts, types, null rates, distinctness, distributions, keys and optional joint analysis. | `src/shape/profile/reference/profile.py`, `_COLUMN_FIELDS`, serialization methods |
| The default save uses safe capture; full capture retains values and extremes. | `src/shape/profile/reference/profile.py`, `save`; `src/shape/privacy/safe_profile.py` |
| Sensitive columns omit raw value evidence; categories need the release threshold. | `src/shape/privacy/safe_profile.py` |
| The HTML report is inline CSS and SVG with no script or external report assets. | `src/shape/report/html.py`, `render_html` |
| The safe validator is a scan, not a sharing approval; dataset validation has a known failure. | `src/shape/privacy/safe_validator.py`; local starter validation |

A profile can still expose aggregate facts and released categories. Your own profiles stay
yours. Review the capture policy, classification and access controls for every artifact,
HTML report and JSON summary. A full capture needs the handling rules of the source data.

## Network calls

Local CSV and Arrow profiling does not require a cloud service. Selecting a remote source,
sink or command can send data or metadata to its configured endpoint. This is not a claim
that every installed plugin is offline. Package installation also downloads dependencies.

| Selected surface | What it contacts or transfers | Code evidence |
|---|---|---|
| ADLS / OneLake / remote Delta | Reads files using the configured storage and identity clients. | `src/shape/builtins/sources/azure.py`, `src/shape/profile/reference/delta_fallback.py`, `plugins/shape-fabric/src/shape_fabric/` |
| Delta fallback | DuckDB can download its extension, then read the selected Delta files. | `src/shape/profile/reference/delta_fallback.py` |
| Database sinks | Sends generated table definitions and rows to the URI target. | `plugins/shape-databases/src/shape_databases/` |
| Snowflake | Stages local Parquet with PUT, loads with COPY INTO, removes staged files. | `plugins/shape-databases/src/shape_databases/snowflake.py` |
| Databricks | Sends bound INSERT statements; optional client-secret sign-in exchanges an OAuth token. | `plugins/shape-databases/src/shape_databases/databricks.py` |
| Kafka / Event Hubs | Reads or emits messages through configured broker clients. | `plugins/shape-kafka/src/`, `plugins/shape-eventhubs/src/` |
| Fabric commands | Manages configured workspace items and writes to configured services. | `plugins/shape-fabric/src/shape_fabric/` |
| CI comments and notifications | Sends the selected result/comment or notification payload to the configured API or webhook. | `src/shape/cli/ci.py`, `src/shape/cli/notify.py` |

The documentation site adds no analytics or feedback widget. Its problem link opens a GitHub
issue form carrying the page URL; submitting that form shares what you enter with the public
repository. The VS Code extension starts local Shape and Git processes; its declared behavior
is covered in [the extension page](VSCODE.md).

## Plugin trust

Plugins load through Python entry points and run in the Shape process with its permissions.
A plugin can read files, make network calls or start programs. An allow-list checks which
packages load; it does not confine their code. Evidence: `src/shape/plugins/host.py`,
`src/shape/plugins/trust.py` and [the plugin trust model](plugins/trust-model.md).
Install only packages you trust, pin versions and restrict the whole process where required.
Credentials resolved by adapters remain subject to the adapter and driver's behavior.
[Owner: security reviewer — review deployment-specific endpoints, identities and egress policy.]

## Related

[Known limitations](KNOWN_LIMITATIONS.md) · [Licensing](LICENSING.md) · [Security policy](SECURITY.md)
