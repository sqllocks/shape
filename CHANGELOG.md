# Changelog

Shape is in **early access**. Profiling is available now; data generation and
pipeline integration are in progress. See `docs/plans/COMPLETION_PLAN.md`.

## Unreleased

- `shape from-ddl FILE`: reads SQL `CREATE TABLE` DDL (SQL Server, PostgreSQL, MySQL, ANSI; inline,
  table-level and `ALTER TABLE` foreign keys) into a generation schema, with smart inference of
  distributions, key patterns, row ratios, seasonality, correlations and business rules
  (`--smart`, the default; `--explain` prints each decision). See `docs/GENERATION_ENGINE.md`.
- Stream profiling runtime (`shape.streaming`): tumbling, sliding, session and global windows over
  Arrow micro-batches profiled in bounded mode, with watermarks, allowed lateness and a late-data
  policy; windows can be snapshotted and restored exactly. Bounded per-key sketches (LRU, TTL, a hard
  memory cap), a vectorized windowed `Deduplicator`, and a `StreamConsumer` that commits offsets and
  window state in atomic checkpoints, resumes after a restart and reconnects without replaying.
  Fixes: `TumblingWindow` could not be restored, keyed state grew without bound, `deduplicate_ids`
  looped over every row, and a reconnect replayed the batches already delivered. See
  `docs/specs/STREAMING_SEMANTICS.md`.
- `shape verify`: validation gates (schema conformance, nulls, primary keys, foreign keys,
  ranges, temporal consistency, file format, schema drift, distributions), a quarantine for
  failed files and tables, and a Markdown or JSON report. See `docs/VERIFY.md`.
- Relicensed under the MIT license.
- Repository cleaned up for public release: removed internal milestone and
  qualification records.
- README and changelog rewritten to describe the current state.
