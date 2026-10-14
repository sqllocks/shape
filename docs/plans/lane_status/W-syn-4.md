# W-syn-4 lane status

Status: completed under the Owner's explicit option-classification decision. Scoped checks passed; the full check was time-bounded and remains incomplete.

Initial blocker base: `origin/main` at the commit recorded below. The earlier blocker report is preserved as historical evidence; the resumed changes add a contract test, documentation for existing options, and an Unreleased CHANGELOG line. At the initial stop, no production code, tests, reference corpus, public documentation, release files, plan file, or labels were changed. No live dispatch, promotion, PR, or merge was performed.

## Initial finding and question (resolved below)

The Synapse option table in `docs/plugins/fabric-writers.md`, in the `synapse://` section, documents 13 keys:

`chunk_rows`, `columns`, `connection`, `connection_string`, `copy_identity`, `credential`, `distribution`, `index`, `primary_key`, `schema`, `schema_name`, `staging_path`, `write_mode`.

`SynapseSink._write` in `plugins/shape-fabric/src/shape_fabric/sinks.py` additionally consumes `filesystem` and `commit_rows`. `SynapseSink.write` calls `_prefixed_table`, which additionally consumes `table_prefix`. Thus the code accepts 16 keys and the documented table lists 13. The table contains no unsupported keys. `filesystem` is a dependency injection seam, while `commit_rows` is validated as a positive integer and `table_prefix` is the shared physical table prefix.

**Exact question:** Should W-syn-4 pin all 16 accepted Synapse sink options, with the documentation owner adding `commit_rows`, `filesystem`, and `table_prefix` to the option table, or should the test explicitly exclude any of those accepted options from the documented `--sink-config` surface? If exclusions are intended, which keys and what rule should detect future undocumented accepted keys? The lane is tests-only, so it cannot resolve the missing documentation itself.

## Red evidence

A Python 3.12 diagnostic parses the option table's first column, collects literal `opts.pop(...)` keys and the `_take(...)` tuple in `SynapseSink`, and includes `_prefixed_table`'s consumed key. Its equality assertion failed (exit 1):

```text
Documented: chunk_rows, columns, connection, connection_string, copy_identity, credential, distribution, index, primary_key, schema, schema_name, staging_path, write_mode
Accepted but undocumented: commit_rows, filesystem, table_prefix
Documented but not accepted:
AssertionError: Synapse documented keys differ from accepted keys
```

This is diagnostic evidence, not a committed failing test. The prompt requires stopping instead of guessing when the plan contradicts the code or is ambiguous.

## Commands and exits

- Read `/workspace/prompt-runs/W-syn-4.md` and inspected original remote: 0.
- Same-shell `cd /workspace/shape && git remote get-url origin`: 0; confirmed `https://github.com/sqllocks/shape.git` before clone/setup work.
- Read original `git config user.name` and `git config user.email`: 0; copied the configured identity to the independent clone.
- `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-syn-4`: 0.
- Set cloned origin to the original GitHub URL and copied Git identity: 0.
- `git fetch origin`: 0.
- `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`: 0.
- `git checkout -B lane/W-syn-4 origin/main`: 0.
- `cat AGENTS.md CLAUDE.md CONTRIBUTING.md`: 1 because AGENTS.md is absent; CLAUDE.md and CONTRIBUTING.md were read. The surrounding inspection shell finished 0.
- `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` to an untracked temporary file outside the clone: 0; read Decisions and section 4.7. The plan was not merged or committed.
- Repository/plan searches with `rg` and targeted `sed`/`head` reads: 0, except `rg -n 'table_prefix' docs/plugins/fabric-writers.md`: 1 (no matches).
- Search for AGENTS.md across workspace: 0; none applies to this clone.
- Inspected original virtual environment and native build directory: 0. No dependencies, shared build targets, or other lane files were modified.
- Python 3.12 AST/Markdown equality diagnostic: 1, as shown above.
- Status-file creation: 0.

At the initial stop, bootstrap, Ruff, mypy, neighboring tests, and `make check` were not run: execution stopped on the explicit contradiction rule before changing tests or setting up dependencies. Python 3.11 is unavailable in the supplied environment; Python 3.12 would be used if work resumes. No CHANGELOG change was guessed before resolving the stop question.

The initial blocker was committed and pushed only to `lane/W-syn-4`; resumed work preserves that commit and uses the same permitted lane.
a8e476ca910c40e3d8e7dd0dace9c80699237e82

## Owner resolution and implementation (2026-10-14)

The Owner authorized documentation changes for user-settable options, explicit commented
exclusions for injection-only options, and no sink behavior changes.

| Key | Classification | Evidence, type, default and action |
|---|---|---|
| `commit_rows` | User-settable | `src/shape/cli/scale.py:99` parses CLI numeric strings to integers; `plugins/shape-fabric/src/shape_fabric/sinks.py:229-239` consumes a positive integer, defaults to `None`, and selects one load or grouped loads. `_write_in_loads` at lines 251-281 rounds to batches, commits each group, appends subsequent groups and preserves prior committed rows on failure. Added the existing behavior to the Synapse option table. |
| `table_prefix` | User-settable shared plumbing | The same CLI parser accepts strings. `sinks.py:188` calls `_prefixed_table`; `sinks.py:605-610` consumes the option, defaults to the empty string, validates string type and prepends it to the physical name. Shared plumbing is still user-settable, so documented it. |
| `filesystem` | Python injection seam, excluded from CLI docs | `sinks.py:213` passes it to the writer; `plugins/shape-fabric/src/shape_fabric/_storage.py:59-60` returns the injected object and lines 71/75 call `.open()`/`.rm()`. The CLI parser produces strings or integers, which cannot implement this filesystem object interface. The test explicitly excludes it with a comment naming these source lines; the exclusion subset assertion fails if code stops reading it. |

The test extracts consumed keys from `SynapseSink` and `_prefixed_table`, including literal
`pop`, `get`, `setdefault`, subscript and membership reads, and `_take` lists. Literal
comprehension-bound keys cover the existing secret-mask read. Nonliteral unrecognized keys
fail for review. Table key parsing includes identifiers containing digits. Documented keys must equal consumed keys minus the explicit filesystem
exclusion. Mutation guards prove added reads require docs, removal of `commit_rows` requires
doc removal, and removal of `filesystem` requires exclusion removal.

No sink behavior, frozen labels, AVAILABLE table, promotion plan or live receipt changed.
The prose now qualifies one-load behavior as the default, consistently with `commit_rows`.

### Resumed red evidence

Before documentation edits, `python -m pytest -q
plugins/shape-fabric/tests/test_synapse_config_docs.py` exited **1**, one failed test:

```text
Extra items in the right set:
'commit_rows'
'table_prefix'
1 failed in 0.18s
```

The complete red log is `/workspace/prompt-runs/W-syn-4-red.log`. After adding those two
documented options, the contract passed; after expanding the mutation guards, all twelve
contract cases passed. Focused neighboring tests passed (92 tests).

### Resumed setup and verification commands

All logs below are under `/workspace/prompt-runs/`, with prefix `W-syn-4-`.
Shell exports use `TMPDIR=/workspace/prompt-runs/tmp/W-syn-4`,
`CARGO_HOME=/workspace/.onboarding/cargo`, `RUSTUP_HOME=/workspace/.onboarding/rustup`
and the corresponding cargo `bin` on PATH. The Rust target is this lane's own
`rust/shape-kernel/target`, never a shared writable target. Python 3.12.14 meets the
repository's >=3.11 requirement (3.11 is unavailable).

- Recovery reads (`pwd`, `git status`, remote/log, prompt/status/test, policy files,
  `git show` plan Decisions and 4.7, source/docs/setup inspection): 0. Initial `rg`
  mistakenly named nonexistent `synapse_writer.py`: 2; actual code is `synapse.py`.
  A truncated temporary plan dump was replaced with direct `git show` reads.
- Initial `.venv/bin/pytest` probe: 127 (entrypoint missing); `python -m pytest`
  proved the red state with exit 1 (`red.log`).
- `pip install --force-reinstall --no-deps pytest ruff mypy maturin mkdocs`: 0
  (`entrypoints.log`). Installed packages existed but entrypoint scripts were absent.
- Bootstrap started without the recovered Rust PATH; stopped only that duplicate
  bootstrap process tree with SIGTERM (143), retaining lane sources/cache. Its pip
  build had selected an alternate automatic toolchain. Restarted with the recovered
  Rust toolchain and own target; the two initially shared the same log, so its
  `Terminated` line belongs to the stopped duplicate process.
- Recreated 42 missing lane console entrypoints from installed distribution metadata: 0.
- `pip install --no-deps -e plugins/shape-sqlserver -e plugins/shape-eventhubs
  -e plugins/shape-fabric -e plugins/shape-kafka`: 0 (`plugin-install.log`).
- Test editing and `ruff format plugins/shape-fabric/tests/test_synapse_config_docs.py`: 0.
  The first expanded extractor rejected the existing comprehension-bound `opts.get(k)`
  read (8 failures); resolving keys from its literal tuple fixed it without production changes.
- `pytest -q plugins/shape-fabric/tests/test_synapse_config_docs.py`: 0,
  **12 contract cases**, including 11 mutation guards (the final complete run is in `neighbours.log`; `contract.log` records the preceding ten-case run).
- `pytest -q plugins/shape-fabric/tests/test_synapse_config_docs.py
  plugins/shape-fabric/tests/test_synapse.py`: 0, **92 passed** including all final mutation guards (`neighbours.log`).
- `ruff check src tests plugins benchmarks/vs_refengine scripts`: 0 (`ruff.log`).
- `ruff format --check src tests plugins benchmarks/vs_refengine scripts`: 0 (`format.log`).
- `mypy`: 0, 701 source files (`mypy.log`).
- `mkdocs build --strict`: 0 (`site.log`).
- `python scripts/check_doc_links.py --site site`: 0 (`links.log`).
- `git diff --check`: 0.
- `python scripts/check_user_facing.py --site site`: 0 (`wording.log`).
- `make bootstrap` with recovered Rust toolchain: 0 (`bootstrap.log`); native kernel
  rebuilt in the lane's own target and domains/core installed from this checkout.
- `pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver
  -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka`: 0
  (`docs-install.log`); verified both core and Fabric import from this lane.

Open questions: **none**. The Owner's classifications decision resolved the original stop.

- `timeout 600 make check`: **124** (`make-check.log`). It passed Ruff, formatting,
  mypy (701 files), compileall, Vulture, import contracts, requirements, secrets,
  user-facing wording, generated registries, shipped data, plugin skeletons,
  versions, release workflows, conformance coverage, CLI compatibility and v1
  checks. The timeout occurred in the large core coverage-stage pytest run,
  after progress reached 22%, with no displayed failures. This is an incomplete
  full-suite run, not a passing coverage result or a proven coverage deadlock.
  The heavy/Python-kernel/Rust check stages were not reached. No gate was changed.
- Final `ruff check` and `ruff format --check` on all prompt-listed paths: 0.
- Final focused Synapse contract and neighboring tests: 0, **92 passed**.
- Final `git diff --check`: 0.
- Remote preflight `git ls-remote --heads origin lane/W-syn-4`: 0; the existing
  blocker head was `f0469b92aecb94dc26b1cb3161b23dc8e899167f` and is preserved.

Finish uses a DCO-signed commit, ordinary push only to `lane/W-syn-4`, and
`git ls-remote` verification. No force push, PR, merge, release change or tag.

- `git commit -s -m 'W-syn-4: pin documented Synapse sink options'`: 0,
  `a721d7e7da6fddd6e73e8c65a996e05466855984` (DCO trailer included).
- `git push -u origin lane/W-syn-4`: 0; `git ls-remote --heads origin lane/W-syn-4`: 0,
  matching the implementation commit exactly; `git status --short`: 0, clean.
- Final log read showed the full-check progress advanced to **22%** before the
  watchdog terminated it. This status-only follow-up corrects the earlier 16%
  snapshot and records the verified implementation push. No scoped source or
  tests changed after their final passing checks.
