# W-mask-1 — `shape mask` before freezing, items 1 to 3

Scope: 0.9.4, promotion-plan section 4.3 items 1 to 3. Item 4 not done; `mask --dry-run`
is left for W-mask-2. Base: f321d96aac18697969142bbedb79e338d80fb77d on `lane/W-mask-1`.
Python 3.11 venv, `make bootstrap`, four plugins installed editable.

## Lead answers
- The `test_audit_profile.py` fork-pool deadlock in `lock_data` is a sandbox issue; CI passes
  the same suite (run 38504314440). Do not wait on it; do not change, skip, deselect or xfail it.
- W-mask-1 does items 1 to 3 only.

## What changed
1. `-o/--output`: a non-empty folder, or a path that is a file, is refused with exit 2 and one
   `shape: error:` line ("... exists and is not an empty folder: use a new one"), checked before
   anything is read or written. A new or empty folder works as before.
2. `--seed` now defaults to `None` (42 is applied when no key is used). `--seed` with
   `--key-file`/`--key-env` exits 2 with one error line.
3. The profiler's `--string-columns` UserWarning is ignored inside `shape mask` (mask reads CSV
   values as text, so the hint does not apply). `docs/MASK.md`: the hint lines are gone from
   the transcripts; each example command writes to its own output folder
   (`masked/`, `masked_seed/`, `masked_parquet/`, `masked_json/`, `masked_env/`,
   `masked_file/`) because a second run into `masked/` is now refused; the `-o` and `--seed`
   rows of the options table are updated.
Plus one line under `## Unreleased` in CHANGELOG.md.

Files: `src/shape/cli/mask.py`, `tests/masking/test_mask_cli_guards.py` (new), `docs/MASK.md`,
`CHANGELOG.md`, this file.

## Red evidence (new tests, before the implementation)
`python -m pytest -q tests/masking/test_mask_cli_guards.py -p no:cov` -> 4 failed, 2 passed:
```
FAILED test_a_non_empty_output_folder_is_refused
FAILED test_seed_with_a_key_is_rejected[--key-file]
FAILED test_seed_with_a_key_is_rejected[--key-env]
FAILED test_the_csv_read_does_not_advise_a_flag_mask_lacks
```
(the two that passed already — file as output path, new/empty folder accepted — pin existing
behaviour.) After the change: all 6 pass.

## Commands and exit codes
| Command | Exit |
| --- | --- |
| `python3.11 -m venv .venv`; `make bootstrap`; `pip install -e plugins/shape-{sqlserver,eventhubs,fabric,kafka}` | 0 |
| full `timeout 2400 make check` (first run, before formatting the new line) | 2: ruff E501 in my edit, fixed |
| full `timeout 2400 make check` (second run) | 124 (timeout, not the deadlock). Stages up to `check_v1_done.py` all passed (ruff, format, mypy, compileall, vulture, import contracts, requirements, secrets, user-facing, exit codes, failure modes, shipped data, plugin skeletons, versions, release workflows, conformance, CLI surface, v1 done). Core pytest reached about 25% in 40 minutes with failures (`F`) from the sandbox (no docker, no network installs) and was killed by the timeout; the heavy, Python-kernel and Rust stages were not reached. No hang in `test_audit_profile.py` seen. |
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 |
| `mypy` | 0 (698 files) |
| `pytest -q tests/builtins tests/masking tests/cli -m "not emulator and not live and not heavy" -p no:cov` | 0: 1126 passed, 1 skipped |
| `pytest -q tests/docs -m "not emulator and not live and not heavy" -p no:cov` | 1: 145 passed, 20 failed. `test_reference_page[MASK]` passes. Failures are other pages (CONTRIBUTING, README, CLI, CONTAINER, DBT, FIDELITY_TIERS, GENERATION_ENGINE, IMPORTERS, INSTALL, NOTIFICATIONS, PROJECT, REPORT_CARD, SIMULATION_FILES_EVENTS, SINKS, authoring, behavior, healthcare-codes, simulation), `test_brand`, and tutorial `06-domain-duckdb` (docker socket missing, unset env vars, etc.). DBT, REPORT_CARD and SINKS were re-run on the unmodified tree (stash) and fail the same way; none of these pages run `shape mask`. Not fixed: not touched by this lane. |
| `pytest -q tests/profile/test_audit_profile.py -p no:cov` | 0: 163 passed |

The lead proves the full suite in CI on the pushed head.
