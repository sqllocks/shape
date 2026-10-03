# What Shape will not build

These are decisions the project has made. Each entry says what is not built, why, what to use
instead, and where the decision is recorded. If a request matches an entry, the answer is the
entry, until the decision is changed where it is recorded. To propose a new entry, open an issue
with the request, the reason and the alternative; an entry is added when the decision is made, with a
link to it (`CONTRIBUTING.md` links here).

Not on this list: things that are planned, and things that are not built yet. The plan is
[plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md).

## A process sandbox for plugins

**Why.** Plugins are trusted, in-process Python code, the same decision as installing any other
package. An earlier subprocess "capability sandbox" was cosmetic and was deleted, with its claims;
Shape does not claim to confine a plugin.
**Instead.** Install plugins only from sources you trust and pin their versions. Where you need
confinement, apply it to the whole process: containers, OS accounts, egress rules and secret
scoping.
**Recorded in.** [plugins/trust-model.md](plugins/trust-model.md); decision D-09 in
[plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md).

## A hosted hub or web application

**Why.** The hub and the web app had no dependents and carried security defects; they were deleted
with 25 other unused modules.
**Instead.** `shape bridge`, the JSON request and response protocol on standard input and output,
for anything that needs to drive Shape from another program.
**Recorded in.** Decision D-08 in [plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md); the
protocol is [BRIDGE.md](BRIDGE.md).

## An MCP server in this repository

**Why.** The MCP server is not shipped from this repository: the unpublished skeleton was removed,
and no MCP server is re-added here.
**Instead.** `shape bridge`, which is the protocol an MCP server would be built on.
**Recorded in.** The decision-log row of 2026-10-02 (P6-11, T-08, D-08) in
[plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md); [BRIDGE.md](BRIDGE.md).

## A native CLI binary

**Why.** The CLI stays Python (argparse and a plugin command registry), with heavy modules and
plugins loaded lazily to keep start-up within budget; a second implementation would have to
reproduce every command and the plugin registry.
**Instead.** `pip install sqllocks-shape` (or `pipx`), or the container image.
**Recorded in.** Decision T-18 in [plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md);
[CONTAINER.md](CONTAINER.md).

## Writing the version 1 model or `.shape` format

**Why.** Shape is pre-1.0 and the version 1 formats were never published. There is one model and
one `.shape` format (version 2), with a read-only migrator from version 1.
**Instead.** Read an old artifact and write it again: `shape inspect` reads both, and writing
produces the current format.
**Recorded in.** Decision T-11 in [plans/COMPLETION_PLAN.md](plans/COMPLETION_PLAN.md);
[specs/SHAPE_2.md](specs/SHAPE_2.md).

## A guarantee that a profile or a synthetic dataset is anonymous

**Why.** A Shape is not automatically anonymous: aggregates, rare categories, small cohorts and
differencing can disclose facts, and synthetic does not mean declassified. A statement of privacy
that no test enforces is not made.
**Instead.** Apply cohort thresholds and rare-value suppression, use the share-safe form of a
profile (`shape profile safe`), run `shape verify` with the memorization gate against the source,
and decide sharing by the handling rules.
**Recorded in.** [PRIVACY_MODEL.md](PRIVACY_MODEL.md); rule SHAPE-SEC-014 in
[SECURITY_SPECIFICATION.md](SECURITY_SPECIFICATION.md).

## A replacement for your warehouse, orchestrator, message broker, dbt, BI tool or ML platform

**Why.** Shape describes how data behaves and generates data that behaves the same; it is not a
backup or source-record recovery system, a warehouse, a catalog, an orchestrator, Kafka, dbt, a BI
tool or a general ML platform.
**Instead.** Run Shape inside the tools you have: write to their sinks, call it from their jobs and
pipelines, and gate their data with `shape check` and `shape verify`.
**Recorded in.** Non-goals in [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md).

## A replacement for accredited cross-domain solutions

**Why.** Moving data between security domains is the job of approved transfer mechanisms.
**Instead.** Use the approved mechanism, and let Shape integrate with it.
**Recorded in.** [SECURITY_SPECIFICATION.md](SECURITY_SPECIFICATION.md).

## A statistical engine that depends on AI

**Why.** AI may explain and author intent, but profiling, diff, gates and generation must give
the same answer without a model, offline and byte for byte.
**Instead.** Use an AI tool to write a contract, a spec or a decision file, and let Shape run it.
**Recorded in.** Principle 13 of [SHAPE_MANIFESTO.md](SHAPE_MANIFESTO.md).

## A mandatory network dependency in the core

**Why.** Profiling, diff, gates and generation work offline and in air-gapped environments; a
network service in the core path would make them fail closed for the wrong reason.
**Instead.** Network access belongs in optional plugins and extras (sources, sinks, Fabric, Kafka,
Event Hubs), which are installed and called on purpose.
**Recorded in.** [CONTRIBUTING.md](../CONTRIBUTING.md); enforced by the `zero_network` tests.
