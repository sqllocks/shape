# Proposal: an Eventhouse source for profiling

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`. Lower priority: a short proposal.

## 1. The problem

Event and log data often lives in an Eventhouse (a Kusto database). To profile it, a user must export it first: `docs/EMIT.md:245` says "Shape does not read Eventhouse tables directly. To profile their data, export to Parquet or CSV and profile the file."

- Shape already writes to an Eventhouse (the `eventhouse://` emitter, `docs/EMIT.md:232`; `shape publish -t eventhouse`, `docs/plugins/fabric-commands.md:27`) through its own Kusto client, `KustoClient` (`plugins/shape-fabric/src/shape_fabric/kusto.py:214`), with no extra dependency. Its `query` (`kusto.py:300`) returns rows of the first result table and is used only for a row count and a connectivity probe.
- The `shape.sources` protocol is a URI to a record batch iterator with `schema` and `read` (`src/shape/plugins/api/v1.py:106` to `116`). `shape-fabric` registers `onelake` and `semantic-model` sources (`plugins/shape-fabric/pyproject.toml:69` to `70`), but no Kusto one.

## 2. Proposed design

A source `eventhouse` in `shape-fabric`: `eventhouse://<cluster>/<database>/<table>`, with options `query` (a KQL query instead of a table), `where`, `since` and `until` (on a time column), `sample` (rows, with `take` or `sample`), and `limit`.

- `schema` reads the table schema (`.show table T cslschema`) and maps Kusto types to Arrow (`datetime` to `timestamp[us, UTC]`, `timespan` to `duration[us]`, `dynamic` to text JSON, `decimal` to `decimal128`).
- `read` pages the result (by time ranges of the time column, or by `row_number` for a query) so memory is bounded, and yields record batches.
- Authentication is the plugin's existing Fabric authentication (`docs/plugins/fabric-auth.md`); nothing new is stored.
- `shape profile eventhouse://...` then works like any other source, and the EMIT.md sentence is replaced by the source's documentation.

## 3. Compatibility

A new source; nothing existing changes. It is read-only, sends only queries the user names or the schema and paging queries above, and records the query text and row count in the profile's source metadata.

## 4. Wanted (ready to paste into the issue)

1. A `shape.sources` plugin `eventhouse` in `shape-fabric` with the URI and options of section 2, using `KustoClient`.
2. The Kusto to Arrow type mapping, with an error naming the column for an unsupported type.
3. Paged reads with bounded memory, and `sample` and `limit`.
4. Tests against a recorded response fixture (no network), and a live test in the Fabric lane.
5. Documentation in `docs/SOURCES.md` and `docs/plugins/fabric-commands.md`; `docs/EMIT.md:245` updated.

Acceptance beyond section 13's common items: a profile of a table read through the source equals the profile of the same rows exported to Parquet (the same statistics within float tolerance); every Kusto scalar type maps as documented; a query error is reported with the server's message and exit 2.

## 5. Open questions for the lead
1. Whether the source should also read the Kusto emulator for local tests, as the Kafka emulator is used for streaming.
2. Whether sampling should be server side only (`sample`) or also offer a deterministic hash sample on a key, so two profiles of one table use the same rows.
