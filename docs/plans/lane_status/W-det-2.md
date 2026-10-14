# W-det-2 status

Status: completed documentation edits; verification recorded below.

Base: `origin/main` at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.

## Changes and findings

- Cut the scan-cost sentence from `docs/DETERMINISM.md`. It claimed a scan of `0..i` the first time a plan answers row `i`. In `GenerationPlan._first_row_of`, the scan starts at the cached `done` offset, which can already exceed `i` after a higher row was requested. A test of a cold plan alone would not prove the sentence as written. Cutting this implementation-cost claim preserves the proven order-independent row contract without changing runtime behavior.
- Cut the external-side-effect sentence. Core cannot prove that an arbitrary external idempotent or transactional sink obtains unspecified stronger delivery guarantees. Sink behavior and transaction boundaries belong to the sink contract, so an exact test of the general sentence is unavailable.
- Moved the remaining streaming paragraph sentence unchanged into `docs/EMIT.md`, under delivery, backpressure and checkpoints. That section already explains at-least-once delivery and checkpoint replay limits.
- Left the generation-engine link and experimental status unchanged. Added no claim table, promotion, changelog entry, behavior change, test skip, or test xfail.

## Red evidence

Not applicable: this work takes the explicitly permitted cut option for both sentences and moves existing prose. No behavior or test changes were made. Existing neighboring tests provide verification; no fabricated failing test is reported.

## Setup and command results

Commands ran in the isolated checkout `/workspace/prompt-runs/checkouts/W-det-2` unless noted. No changes were made to the original checkout or other lanes.

| Command / operation | Exit | Result |
|---|---:|---|
| `cd /workspace/shape && git remote get-url origin` | 0 | `https://github.com/sqllocks/shape.git`; repository confirmed before setup. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-det-2`; restore origin and copy user identity | 0 | Isolated clone and original GitHub origin. |
| `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-det-2 origin/main` | 0 | Requested base and lane. |
| Read `CLAUDE.md`, `CONTRIBUTING.md`, both affected pages, plan (including Decisions and 4.4), generation implementation/tests and docs style | 0 | No `AGENTS.md` present; no plan file committed. |
| `python3.12 -m venv .venv`; symlink third-party packages excluding core/plugin editable sources | 0 | Python 3.11 absent; supported Python 3.12 used. |
| `PIP_NO_DEPS=1 make bootstrap` | 0 | Installed this clone's core and domains plugin. |
| `pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 1 | Concurrent shared Cargo staging race: maturin could not copy `target/maturin/lib_kernel.so` after the build. Core had already installed successfully through bootstrap. |
| Create local venv command wrappers and copy Ruff executable | 0 | Reused third-party package symlinks do not supply venv console scripts. An initial Ruff wrapper resolved the interpreter outside the venv; corrected before definitive checks. |
| Initial `.venv/bin/python -m ruff check ...` / `ruff format --check ...` attempts | 1 / 1 | Wrapper environment problem (`No module named ruff`); subsequent required checks pass. |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed. |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | All files formatted. |
| `.venv/bin/python -m mypy` | 0 | Success: no issues found in 701 source files. |
| `python -m pytest -q tests/generation/test_first_per_parent_g2.py tests/generation/test_random_access.py tests/scenario/test_reproducibility.py tests/streaming/test_failure_replay.py tests/streaming/test_checkpoint_store.py tests/streaming/emit/test_runtime.py -m 'not emulator and not live and not heavy'` | 0 | 75 passed in 31.21s. |
| `python scripts/check_doc_links.py` | 0 | Zero broken source links. |
| `python scripts/check_user_facing.py` | 0 | Clean. |
| Initial `git diff --check` | 2 | Extra blank line at end of DETERMINISM; removed. |
| Final `git diff --check` | 0 | Clean. |

Rust uses `CARGO_HOME=/workspace/.onboarding/cargo`, `RUSTUP_HOME=/workspace/.onboarding/rustup`, and the lead-provided shared cached `CARGO_TARGET_DIR`. Reused third-party packages were not modified. Inspection commands (`rg`, `cat`, `sed`, `tail`, `ls`, `ps`, `df`, `git status`, `git diff`) were read-only; `ls` for absent console scripts and an early `tail` before the install log existed returned nonzero, prompting the wrapper setup above.

## Full check result and setup repair

`timeout 300 make check` ran once, exit **2**. Ruff, formatting, mypy, compileall, Vulture, import contracts and every repository-script gate through `check_v1_done.py` passed. Core pytest stopped during collection in `tests/builtins/test_shape_sm_script.py` and `tests/scale/test_kql_sink_uri.py`, both with `ModuleNotFoundError: shape_fabric` (95.84s). The core test process had started before the plugin-install repair completed. This is an environment/setup timing failure, not a changed-code failure; it does not establish the coverage gate or full-suite pass. No gates were changed.

`pip install --no-deps -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` retry: **exit 0**. Import-path verification: **exit 0**, with core and all five requested plugin modules resolving inside this isolated clone. Core came from the successful bootstrap; docs third-party dependencies were inherited from the source venv. No second core rebuild was needed.

After repair, `python -m pytest -q tests/builtins/test_shape_sm_script.py tests/scale/test_kql_sink_uri.py`: **exit 0**, 7 passed in 1.91s. As requested, `make check` was not repeated.

## Documentation build

- `timeout 90 mkdocs build --strict`: **exit 0**, documentation built in 63.05 seconds.
- `python scripts/check_doc_links.py --site site`: **exit 0**, zero broken source/site links.
- `python scripts/check_user_facing.py --site site`: **exit 0**, clean.
- Final `git diff --check`: **exit 0**.
- `git add docs/DETERMINISM.md docs/EMIT.md docs/plans/lane_status/W-det-2.md` and signed-off `git commit -s -m "W-det-2: narrow determinism claims and move replay guidance"`: successful.
- `git push -u origin lane/W-det-2`: successful; lane-only push, no PR, merge, force push or tag. Remote head verified after push.

## Open questions

None. Both cuts are expressly allowed by W-det-2.
