# CLAUDE.md

This repository is being rebuilt against a fixed, fully decided plan:
**`docs/plans/COMPLETION_PLAN.md`**.

Before doing anything in this repo:

1. Read §0 (Builder guide), §1 (Environment setup) and §6 (Execution rules) of the
   plan, in full.
2. Find the next work package in §11 (Status tracker). It is the first `todo` whose
   `Depends` are all `done`.
3. Build exactly that work package to its acceptance criteria in §7. Then update the
   tracker, commit with the work-package ID as the message prefix, and push.

Rules that override everything else:

- Every decision in §2 (D-xx and T-xx), every gate and every tolerance is fixed. Do
  not change one; escalate instead (§0.4).
- Never lower a gate, loosen a tolerance, skip or xfail a test, or fabricate
  benchmark results.
- Equivalence verification comes before any timing (§6.4).
- Never modify the pinned Spindle checkout (`$SPINDLE_ROOT`).
- Never report something as done without running its checks in this session.

Useful commands (once P0-06 and P0-07 are done):

```bash
make check                                   # lint, format, types, tests (T-27 scope)
pytest -m "not emulator and not live"        # local test run
python benchmarks/vs_spindle/run.py --quick  # verifiers + benchmarks vs pinned Spindle
```
