# W-verify-1

Status: scoped documentation work complete; focused checks passed. Full `make check` incomplete under its 300-second bound; see limitations below.

## Scope and findings

Based on `origin/main` at `a8e476ca910c40e3d8e7dd0dace9c80699237e82` and section 4.6 plus Decisions of the promotion plan read from `origin/plan/promotion-093`. The plan was read through `git show` and is not included in this branch.

- Moved “Scores by dimension and failing rows” from `docs/VERIFY.md` into experimental `docs/SCORECARD.md`. Removed its now-redundant self-link and adjusted its opening reference and wrapping.
- Reworded the `privacy` verify-config row to describe only its verified behavior: the configuration is accepted and validated but does not enable a verification gate. Removed the experimental command reference and membership-inference description from this row. Validation is implemented in `_check_privacy` in `src/shape/quality/verifyconfig.py`.
- Both pages retain their existing status. No runnable examples, manifest entries, runtime implementation, recorded outputs, regression data, or promotion labels changed. W-verify-2 claim mapping and W-verify-3 Python API markers/release note remain separate work packages.

## Red evidence

Not applicable: this task moves documentation and clarifies existing behavior without adding or changing behavior. No implementation-mirroring tests were added. Existing runnable references for both affected pages, the shared inventory, and verify-config tests prove the retained behavior.

## Environment deviations

Python 3.11 is unavailable; used supported Python 3.12. Created an independent clone and venv. Reused third-party packages through filtered symlinks, excluding `shape*`, `sqllocks_shape*`, and editable source mappings. Installed core and requested plugins from this clone with `PIP_NO_DEPS=1`. Generated venv console entry-point wrappers for reused dependencies and linked the Ruff executable. Verified `shape.__file__` resolves to this clone's `src/shape/__init__.py`.

Used Rust from `/workspace/.onboarding/cargo` and `/workspace/.onboarding/rustup`. Bootstrap initially used the lead-provided shared Cargo target; copied the completed original target cache to this clone's own `rust/shape-kernel/target` for subsequent docs/plugin packaging, to avoid shared packaging staging races.

## Commands and exit codes

Commands ran in this independent checkout unless explicitly stated. Logs are kept outside tracked files under `/tmp/W-verify-1-*.log`.

| Command | Exit | Result |
|---|---|---|
| `git -C /workspace/shape remote get-url origin` | 0 | Correct `https://github.com/sqllocks/shape.git`. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-verify-1` | 0 | Independent checkout. |
| Initial setup chain after clone in its parent directory | 128 | Wrong working directory for `git remote`; corrected immediately, before edits. |
| Set checkout origin, copy original git user identity, `git fetch origin`, fetch plan ref, `git checkout -B lane/W-verify-1 origin/main` | 0 | Correct base and restricted lane. |
| Read `CLAUDE.md`, `CONTRIBUTING.md`; `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` | 0 | No applicable AGENTS.md found. |
| `python3.12 -m venv .venv`; filtered package symlink setup | 0 | Own venv and own source mapping. |
| `PIP_NO_DEPS=1 make bootstrap` | 0 | Core and domains built locally. |
| Initial `ruff check` / `ruff format --check` | 127 | Reused dependency binaries needed venv entry points. |
| Initial `mypy` wrapper | 1 | Entry-point selection wrapper fixed; no repository failure. |
| Generate corrected dependency entry points and link Ruff binary | 0 | Local tooling repair only. |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | Clean. |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2150 files already formatted. |
| `mypy` | 0 | Clean. |
| `pytest -q 'tests/docs/test_reference_examples.py::test_reference_page[VERIFY]' 'tests/docs/test_reference_examples.py::test_reference_page[SCORECARD]' tests/docs/test_reference_examples.py::test_restored_example_inventory tests/quality/test_verify_config.py` | 0 | 21 passed in 39.90s. |
| `python scripts/check_doc_links.py` | 0 | No broken source links. |
| `python scripts/check_user_facing.py` | 0 | Clean. |
| `NO_MKDOCS_2_WARNING=1 mkdocs build --strict` | 0 | Built in 40.66s. |
| `python scripts/check_doc_links.py --site site` | 0 | No broken source or site links. |
| `python scripts/check_user_facing.py --site site` | 0 | Clean. |
| `git diff --check` | 0 | Clean. |

| Copy original completed Rust build cache to own target | 0 | Independent packaging target. |
| `PIP_NO_DEPS=1 pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | All requested local packages installed. |
| Import core and all five requested plugins, print source paths | 0 | All resolve into this independent checkout. |
| First `timeout 300 make check` | 2 | Static/repository checks passed. Core tests collected before Fabric editable install completed: two `ModuleNotFoundError: shape_fabric` errors. This was a setup sequencing error, resolved by finishing the install; full check repeated after verification of source paths. |

| Affected collection tests rerun with pytest after install (exact invocation not retained in the recovered log) | 0 | 7 passed in 2.37s; initial collection errors were in `tests/builtins/test_shape_sm_script.py` and `tests/scale/test_kql_sink_uri.py`; see `/tmp/W-verify-1-collection-recheck.log`. |
| Second `timeout 300 make check` after complete environment setup | 124 (timeout) | Static and repository checks passed. Core tests reached approximately 14% before termination. One failure marker appeared near 11%; no traceback or final pytest summary was produced before timeout, so its cause is unresolved. Heavy, pure-Python and Rust stages were not reached. |
| Recovery: inspect git status/diff, prior logs, plan section 4.6, contributor instructions and process list | 0 | Preserved existing checkout and edits. No running lane test/build processes remained; no processes were killed. |

## Validation limitations

The full suite did not complete. Its timeout is not a passing result, and the single failure marker cannot be attributed to a known environment artifact without a traceback. Both modified-page reference tests, their shared inventory, and verify-config tests passed in the focused 21-test run. No additional edits were made after those checks except this internal report. The lead must rely on complete CI for full-suite confirmation.

## Publication

Signed-off commit with prefix `W-verify-1:` and ordinary push restricted to `lane/W-verify-1`. Publication SHA and remote verification are reported to the lead after push.

## Open questions

None.
