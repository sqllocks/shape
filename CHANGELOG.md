# Changelog

Shape is in **early access**. Profiling is available now; data generation and
pipeline integration are in progress. See `docs/plans/COMPLETION_PLAN.md`.

## Unreleased

- `shape verify`: validation gates (schema conformance, nulls, primary keys, foreign keys,
  ranges, temporal consistency, file format, schema drift, distributions), a quarantine for
  failed files and tables, and a Markdown or JSON report. See `docs/VERIFY.md`.
- Relicensed under the MIT license.
- Repository cleaned up for public release: removed internal milestone and
  qualification records.
- README and changelog rewritten to describe the current state.
