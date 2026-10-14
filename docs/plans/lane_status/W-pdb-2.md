# W-pdb-2: profile-db JSON compatibility keys

Scope: tests only, 0.9.4. Read the Owner Decisions and section 4.11 from
`origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md`; the plan was
neither merged nor committed. Started at `origin/lane/W-pdb-1`,
`bf87f5648d320b3d29ccffb1baba92061f42613b`.

## Change and findings

`test_profile_db_json_key_sets` pins exact key sets for the stdout receipt
shown in `docs/plugins/sqlserver.md` and the nested file written by `--json`.
It checks dataset, table, column and relationship dictionaries. The in-memory
SQL Server scenario exercises catalog-only and sampled runs, with and without
an artifact output. Real profiling and JSON serialization run; no live service
or output fixture is needed. Adding or removing a key fails an equality assertion.

The documented stdout receipt is flat (`written`, `shape_content_id`, `tables`,
`relationships`). With summary-only output, the receipt has only the two counts.
The JSON file instead contains `name`, `row_count`, `tables`, `relationships`,
with nested statistics. The public page does not show a nested JSON literal;
the test additionally pins those actual nested summary key sets. Dataset table
names and column names are data rather than schema keys. The fixture selects
two known tables and requires nonempty columns and one relationship so the
nested assertions cannot pass vacuously.

No behavior changed; no promotion, public documentation edit, or CHANGELOG line
is included. Section 4.11 assigns the behavior-change changelog to W-pdb-1 and
asks W-pdb-2 to pin keys; a promotion release note belongs to its promotion work.
Open questions: none.

## Tests-first and red evidence

This lane adds compatibility tests for existing behavior, not a behavior change.
All four cases passed immediately against the unchanged implementation. No
artificial failing behavior requirement was introduced. To verify the pin rejects
schema drift, a temporary pytest plugin wraps `Profile.summary` in a separate
process, adding `unexpected_key` to the root or removing `mean` from every column.
No production file is edited. Each mutation fails all four cases (exit 1):

```text
E       AssertionError: assert {'name', 'rel...expected_key'} == {'name', 'rel...nt', 'tables'}
E
E         Extra items in the left set:
E         'unexpected_key'
E         Use -v to get more diff

plugins/shape-sqlserver/tests/test_json_keys.py:52: AssertionError
=========================== short test summary info ============================
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[catalog-only-summary-only]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[catalog-only-artifact-and-summary]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[sampled-summary-only]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[sampled-artifact-and-summary]
4 failed in 1.57s
```

```text
E               AssertionError: assert {'cardinality...ary_key', ...} == {'cardinality...ary_key', ...}
E
E                 Extra items in the right set:
E                 'mean'
E                 Use -v to get more diff

plugins/shape-sqlserver/tests/test_json_keys.py:58: AssertionError
=========================== short test summary info ============================
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[catalog-only-summary-only]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[catalog-only-artifact-and-summary]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[sampled-summary-only]
FAILED plugins/shape-sqlserver/tests/test_json_keys.py::test_profile_db_json_key_sets[sampled-artifact-and-summary]
4 failed in 1.58s
```

## Commands and exits

All working commands use `/workspace/prompt-runs/checkouts/W-pdb-2` after the
initial origin verification. Read-only inspections (cat, rg, sed, head, git show,
git status/rev-parse, ps, du, df and log tails) exited 0, except the initial `ls
/workspace/shape/target` (2: native target is under rust/shape-kernel), a search
for nonexistent `docs/PROFILE_DB.md` (2: page is docs/plugins/sqlserver.md), and
`git diff --no-index /dev/null <new-test>` (1: expected diff).

- `cd /workspace/shape && git remote get-url origin`: 0;
  `https://github.com/sqllocks/shape.git`, verified before setup.
- Clone `git clone --no-hardlinks /workspace/shape .../W-pdb-2`, set GitHub origin,
  copy source Git identity with git config, `git fetch origin`,
  `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`,
  `git checkout -B lane/W-pdb-2 origin/lane/W-pdb-1`: 0.
- Read CLAUDE.md, CONTRIBUTING.md and requested plan sections: 0; no AGENTS.md
  found in the checkout.
- `mkdir -p /workspace/prompt-runs/tmp/W-pdb-2`, `python3.12 -m venv .venv`: 0.
  Python 3.11 is absent; Python 3.12 is supported by CONTRIBUTING.md.
- Python dependency reuse script: 0. Linked installed third-party dependencies
  only, excluding shape*, sqllocks_shape*, __editable__*. No shared target edited.
- `cp -a --reflink=auto /workspace/shape/rust/shape-kernel/target
  rust/shape-kernel/target`: 0; independent native target, no CARGO_TARGET_DIR sharing.
- Export TMPDIR to own workspace directory, CARGO_HOME=/workspace/.onboarding/cargo,
  RUSTUP_HOME=/workspace/.onboarding/rustup and prepend its bin directory to PATH.
- `PIP_NO_DEPS=1 make bootstrap`: 0; own core/domains editable/native build.
- An early docs/plugin installation attempt was terminated (143) while bootstrap
  was still running, before installation; rerun after bootstrap completed.
- `python -m pip install --no-deps -e '.[docs]' -e plugins/shape-domains
  -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric
  -e plugins/shape-kafka` with the environment above: 0.
- `python -c 'import shape, shape_sqlserver; print(shape.__file__);
  print(shape_sqlserver.__file__)'`: 0; both paths point to this checkout.
- Initial `python -m ruff format <new-test>` and `python -m ruff format --check
  src tests plugins benchmarks/vs_refengine scripts`: 1; copied dependencies lacked
  the Ruff binary. Copied tool executables into own venv and rewrote Python
  shebangs to own interpreter (Python setup script: 0).
- `ruff format plugins/shape-sqlserver/tests/test_json_keys.py`: 0.
- `ruff check src tests plugins benchmarks/vs_refengine scripts`: 0.
- `ruff format --check src tests plugins benchmarks/vs_refengine scripts`: 0;
  2141 files formatted.
- `python -m mypy`: 0; 698 source files.
- `pytest -q plugins/shape-sqlserver/tests/test_json_keys.py`: 0; 4 passed.
- `pytest -q -m 'not emulator and not live' plugins/shape-sqlserver/tests`: 0;
  178 passed, 19 deselected, 4 unsigned-artifact warnings.
- `KEY_MUTATION=add PYTHONPATH=<own TMPDIR> pytest -q -p key_mutation
  plugins/shape-sqlserver/tests/test_json_keys.py`: 1; expected 4 failed.
- Same command with `KEY_MUTATION=remove`: 1; expected 4 failed.
- Bare `ruff --version` in an inactive shell: 127; activated own venv and reran: 0, Ruff 0.17.0.
- `git diff --check`: 0. Initial staged diff check exited 2 for trailing
  whitespace in captured log excerpts; stripped trailing spaces, then staged
  diff check passed (0).
- `python scripts/check_secrets.py` and `python scripts/check_user_facing.py`
  after writing the lane report: 0 each.
- `timeout --signal=TERM --kill-after=15s 240s make check`: 124 (time limit).
  Setup was complete before this sole run. Lint, format, mypy, compileall,
  Vulture, import-linter and every repository script gate passed. The coverage
  core stage reached 7% with only passing progress dots before termination;
  it did not complete, so no coverage result is claimed. Heavy, Python-kernel
  and Rust check stages were not reached. The full-suite result remains
  incomplete and requires the lead's GitHub CI run.
- `git add` and `git commit -s -m "W-pdb-2: pin profile-db JSON output key sets"`: 0.
- `git push -u origin lane/W-pdb-2`: result verified after commit by lead report.

Logs for setup, checks and mutation evidence are in
`/workspace/prompt-runs/tmp/W-pdb-2/` (not committed).
