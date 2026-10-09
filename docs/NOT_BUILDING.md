# Scope and limits

Understand the boundaries of the shipped package before proposing a feature.

Status: available.

## A process sandbox for plugins

**Why.** Plugins execute trusted Python code in the current process with its permissions.
Shape has no process boundary around them.
**Instead.** Install code you trust and apply operating-system permissions to the process.
**Recorded in.** [Plugin trust model](plugins/trust-model.md).

## A native CLI binary

**Why.** The shipped CLI is Python with argparse and a plugin command registry.
**Instead.** Use the Python distribution and the shipped command interface.
**Recorded in.** [CLI guide](CLI.md).

## Profile queries through the model interface

**Why.** Model queries require model evidence. A saved profile is a different payload.
**Instead.** Read the profile report or its inspection interface.
**Recorded in.** [Models](MODELS.md).

## Unreviewed sharing of profiles

**Why.** Aggregates, rare categories and repeated releases can disclose facts. A safe capture
is data minimisation, not anonymisation.
**Instead.** Review capture policy and access controls before sharing an artifact.
**Recorded in.** [Privacy model](PRIVACY_MODEL.md).

## Snowflake and Databricks profiling

**Why.** The shipped database adapters for these targets write tables; they do not read profiles.
**Instead.** Profile a supported local extract where your handling policy permits it.
**Recorded in.** [Database adapters](databases/index.md).
