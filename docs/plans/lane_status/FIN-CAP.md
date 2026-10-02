# FIN-CAP — financial default window and `shape capture` on every input

Lane `lane/FIN-CAP`. Two owner decisions of 2026-10-02. Never edited: §11, §2.3, the pinned
baseline checkout. Results below are from runs in the builder session (final table at the end).

## 1. Financial simulator default window

- `FinancialStreamConfig.duration_hours` defaults to `None`: the window is first to last
  transaction time plus one settlement batch (the lag a transaction waits to settle). Without a
  time column: 24 h. `duration_hours` overrides. `stats["duration_hours"]` is the window used.
  Fraud-burst hours and settlement batches both follow it.
- Harness: allow-list entry `SIM-9` (`simulation_1to1/names.py`) with a probe
  (`case_financial._window_probe`): on a three-month input the baseline settles only the first 24 h
  and Shape settles the whole span, every month included. The parity cases pin
  `duration_hours=24.0` so every other check compares like with like (also the SIM-6 probe).
- Regression tests (`plugins/shape-simulation/tests/test_financial.py`): every month of a
  multi-month input has settlements and nothing is left unsettled; fraud covers the period;
  settlement lag distribution (in (0, batch], mean batch/2) and status shares unchanged versus an
  explicit full window; explicit window of the same length gives identical tables;
  `duration_hours` still honoured; no time column gives 24 h.
- Docs: `docs/plugins/simulation.md`, `CHANGELOG.md`.

## 2. `shape capture` on every input

- `shape.profile.reference.sources.load_table` (one shared dispatch with `shape profile`'s
  `load_columns`, via `_path_table`) returns the Arrow table of any source: CSV, Parquet, JSONL,
  glob, folder, Delta (`--version` / `--as-of`), URL/`abfss://` plugin sources, Arrow, DataFrame.
  `capture_arrow` (`shape.capture`) turns it into the capture; an empty table keeps its columns.
- `shape capture SRC [--dataset] [--version N] [--as-of T]`. `--dataset` writes a model with one
  table per file; `shape compatibility` compares multi-table models per table (paths
  `tables.<t>.columns.<c>`, a missing table is `removed`). Single-table behaviour unchanged.
- Equal models: CSV, Parquet, JSONL, folder partitions, glob and Delta of the same table give
  equal models (test). Documented format-driven difference: a date is text in CSV and a date
  type in Parquet; both captured as text with the same distinct count (test).
- e2e (`tests/cli/test_capture_sources.py`): capture -> compatibility on Parquet and Delta: a
  renamed column (`removed`), a dropped column (`removed`) and a type change (`type_changed`) are
  each reported, exit 5; unchanged feed compatible; Delta version/as-of; dataset folders.
- Docs: `docs/CLI.md`, `docs/QUICKSTART.md` (schema-change check on a Parquet feed), CLI help.
- Not tested: live `abfss://` (no cloud here); it goes through the same plugin path as `profile`.

## Checks (this session)

| Check | Result |
|---|---|
| `verify_patterns.py --quick` (all simulators, controls, probes SIM-1..9) | exit 0, 0 failures |
| ruff check / format --check (src tests plugins benchmarks/vs_spindle) | clean |
| mypy (repo), mypy --strict plugin | clean |
| vulture, lint-imports, check_user_facing (repo and plugin wheel), bandit -ll | clean |
| `shape --version` start | 42-53 ms |
| `pytest plugins/shape-simulation`, plugin kit | 156 passed; OK |
