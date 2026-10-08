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

**48-hour Fabric demo lanes:** if your session prompt names a demo lane (L1, L2 or
L3), follow §12 of the plan instead of step 2. Edit only the paths that §12.5 gives
your lane, and record completion in `docs/plans/demo_status/<WP>.md`, never in the
plan's tables.

Rules that override everything else:

- Every decision in §2 (D-xx and T-xx), every gate and every tolerance is fixed. Do
  not change one; escalate instead (§0.4).
- Never lower a gate, loosen a tolerance, skip or xfail a test, or fabricate
  benchmark results.
- Equivalence verification comes before any timing (§6.4).
- Never modify the pinned RefEngine checkout (`$REFENGINE_ROOT`).
- Never report something as done without running its checks in this session.
- Shell state does not persist between tool calls. Start every command that uses the
  plan's environment variables with `source scripts/env.sh &&`, run from the repo
  root. P0-00 creates that file; until then, use the §1 block from the plan.
- Performance numbers count only after the equivalence verifier for that workload
  exits 0.
- `REFENGINE_NAME` is the reference engine's short name, set outside the repository
  (your shell; in CI the GitHub repository variable of the same name). `scripts/env.sh`
  does not set it, and the harness under `benchmarks/vs_refengine/` derives every name
  the engine defines from it (`_refpkg.py`). Never write its value into a repo file.

Useful commands (once P0-06 and P0-07 are done):

```bash
make check                                                           # lint, format, types, tests (T-27 scope)
pytest -m "not emulator and not live"                                # local test run
source scripts/env.sh && python benchmarks/vs_refengine/run.py --quick # verifiers + benchmarks vs pinned RefEngine
```
