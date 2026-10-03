# Lane CI-FIX (round 3)

Branch `lane/CI-FIX`; merged `origin/build/main-plan` first (no rebase). §11 and §2.3 untouched.

## Fixed (root causes)

1. **Fuzz smoke `profile-json` RecursionError** (`src/shape/privacy/safe_validator.py`).
   `json.loads` accepts nesting up to ~990 levels, but the recursive `_walk` needs a few more
   frames than the parser, so input nested to depth ~993 overflowed (earlier in pytest, whose
   stack is deeper). `validate_data` now measures depth iteratively and rejects anything deeper
   than `MAX_NESTING_DEPTH = 64` with the validator's existing `malformed` finding (the same
   rule used for other malformed profile JSON). Seed derivation and the smoke assertion are
   unchanged. Regression tests in `tests/privacy/test_safe_validator.py`: depths 65/500/993/5000
   as list and dict (all `malformed`, no exception) and a depth-60 document still scanned.
   Reproduced before the fix with a depth sweep (RecursionError at depth 993).
   Note: the exact CI inputs (iterations 13 and 17) did not reproduce in this container
   (Python 3.11, shallower stack); the sweep above is the reproduction.
2. **OneLake paths on Windows** (`plugins/shape-fabric/src/shape_fabric/onelake.py`): `join` and
   `parent` used `pathlib.Path` for non-URI bases, giving backslashes on Windows. They now use
   `posixpath`, and `PureWindowsPath` only for a base that already has a drive letter or
   backslash. Remote (`abfss://`, `onelake://`) paths were already `/`-joined. No other
   `os.path`/`Path` use on remote paths in shape-fabric, shape-eventhubs, shape-sqlserver or
   shape-kafka (`_storage.py` uses `Path` for genuinely local files only). Not run on Windows
   here; the POSIX run of `test_onelake.py` passes.
3. **bench-quick**: the Shape venv step in `.github/workflows/ci.yml` now installs
   `-e plugins/shape-domains` next to `.[dev]`. That venv serves every later bench step
   (`run.py --quick`, domain_1to1, verify_1to1), so they all get the domains.
4. **Secret scan**: the scrubber tests that carry synthetic secret literals moved from
   `plugins/shape-fabric/tests/test_recorded.py` to
   `plugins/shape-fabric/tests/security/test_tape_redaction.py` (exempt by the existing
   "tests" + "security" rule). `check_secrets.py` and the literals are unchanged. The contract
   tests stay in `test_recorded.py`. The stream-plugins job runs the whole `tests` directory,
   so the moved tests still run (17 collected there).

## Checks run (this session, Python 3.11, venv `$SHAPE_VENV`)

- `ruff check` / `ruff format --check` on src tests plugins benchmarks/vs_spindle: clean.
- `mypy`: no issues in 330 files.
- `pytest -m "not emulator and not live and not heavy"` (ignoring tests/demo/fabric, content):
  4747 passed.
- Fuzz smoke (`tests/validation`): pass. `scripts/fuzz_artifacts.py`, 60 iterations, seeds
  today and today ±1, 3, 5, 7, 10, 12 days: 0 findings each.
- `python scripts/check_secrets.py`: OK.
- Plugins (kafka, eventhubs, sqlserver, fabric) `-m "not emulator and not live"`: 417 passed.
- `stream_1to1/verify.py --scale small` and `--scale medium` against the pinned Spindle
  (set up with `benchmarks/vs_spindle/setup_spindle.sh`): both exit 0, VERDICT PASS.
  My venv held all plugins, so it does not prove the CI venv alone; the CI change mirrors the
  stream-plugins job.
