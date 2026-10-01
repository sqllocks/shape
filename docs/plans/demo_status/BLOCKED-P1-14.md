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

## Scope added by the Lead after this file was first written (plan commit ad61680)
`demo/` and `docs/talks/` are now in P1-14's scope, and `scripts/check_user_facing.py` checks them
(`demo/build_benchmark_sheet.py`, `demo/BENCHMARKS.md`, `demo/TALK.md`, `demo/make_data.py`;
the talk is being reworked by a separate session). Renaming the baseline in the demo kit changes
the demo CLI flag `--spindle-root` and its tests under `tests/demo/content`, which the lane rules
(section 12.5) reserve; I did not touch them. Until these and the three docs files above are
clean, `check_user_facing.py` (and therefore `make check` and the CI `test` job) exits 1 by
design; the gate was not relaxed.

## Unblocked (lead, 2026-09-30 7:20 PM EDT)
- The lead made the refused edits (cd15b21: README, PRODUCT_ARCHITECTURE section 21,
  SHAPE-COMPAT-001 reworded with its ID kept) and merged main (5520b8a), which brings the
  Spindle-free demo generator (no `--spindle-root`, no SPINDLE_ROOT) and the reworked talk kit.
- `python scripts/check_user_facing.py` prints "check_user_facing: clean" at 5520b8a; the suite
  passes (1025). Close P1-14 against its §7 acceptance (verify.py --impl shape on the default
  datasets, both kernel modes) and carry on to G1.

## Closed (builder, 2026-09-30)
All P1-14 acceptance checks pass; tracker row 21a is done. See the decision log (§2.3).
