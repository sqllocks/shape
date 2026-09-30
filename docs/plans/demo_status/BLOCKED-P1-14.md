# BLOCKED: P1-14 (Spindle removed from the user-facing surface)

Status: partly built; the documentation edits were refused by the permission guard
("Modify Shared Resources"), so `scripts/check_user_facing.py` still exits 1.

## Built and verified locally
- `--spindle-compat`, `shape profile capture` and `shape profile diff` removed, with their tests.
- Identifiers renamed (`infer_spindle_type` -> `infer_column_type`); comments and docstrings in
  `src/` and `rust/` rewritten.
- `scripts/check_user_facing.py` added; wired into `make check` and the CI `test` and build jobs.
- Internal harness: `benchmarks/vs_spindle/profile_1to1/adapter.py` maps a `.shape` profile onto
  the baseline JSON; `bench_cli.py` and `verify_cli.py` use it; capture/diff checks removed.

## Not done (refused edits)
`check_user_facing.py` reports these remaining matches:
- `README.md` lines 38, 44, 45, 49 (the `--spindle-compat` / `profile capture|diff` example
  lines, their paragraph, and "as Spindle's profiler does").
- `docs/PRODUCT_ARCHITECTURE.md` section 21 heading and SHAPE-COMPAT-001 text.
- `docs/specs/requirements.yaml` SHAPE-COMPAT-001 `title` and `text` (a normative requirement;
  rewording it needs an owner decision, since the ID and text are referenced by the
  requirements registry).

## Needed
Owner approval (or the edits made by the owner) for those three files. Once they are clean,
`python scripts/check_user_facing.py` exits 0 and P1-14 can be closed and G1 finished.
G1 is blocked on P1-14 (Depends).
