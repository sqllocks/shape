# W-sign-1 — signing page scope

Base: `origin/main`; origin verified as `https://github.com/sqllocks/shape.git` before clone.
Work is confined to an independent `git clone --no-hardlinks` at
`/workspace/prompt-runs/checkouts/W-sign-1`, on `lane/W-sign-1`.

## Changes

- Move signed-artifact migration prose and original example 4 to experimental `docs/MIGRATION.md`.
  Preserve the command and complete expected transcript, move its inventory entry, and add navigation.
  Explain that this current-format fixture exercises a no-op, which writes no artifact or receipt.
- Move signed-vault prose to experimental `docs/VAULT.md` and link both experimental pages from SIGNING.
- Remove `query` from the verification-reader list and example comment. Describe `kv://` as a secret
  store reached through a resolver registered by the host, without a cloud execution claim.
- Move old-source/signing-key fixture preparation to MIGRATION (CLI retains its fixtures).
- Add documentation-scope regression checks and an Unreleased documentation-change note.
- No claim table, Python API stance change, promotion, or release edit.

## Setup and command record

All paths below are relative to this lane clone unless absolute. Reads of the prompt, CLAUDE.md,
CONTRIBUTING.md, docs style and plan Decisions/4.5 returned 0. AGENTS.md does not exist (ls: 2).
Source/inventory/Makefile/config inspections returned 0; an exploratory rg for a nonexistent
`tests/docs/test_reference_manifest.py` returned 2. No such file was assumed or changed.

- Original origin/identity read: 0. Clone, configure origin and original user identity, fetch origin,
  fetch plan ref, checkout lane from origin/main: 0. Plan was read via git show, never copied to lane.
- `python3.12 -m venv .venv`: 0. Python 3.11 is absent; 3.12 is supported by CONTRIBUTING.
- Reuse original venv third-party packages through own symlinks, excluding all Shape distributions,
  editable hooks, .pth files and installer packages: 0. Copy third-party entry points with own Python
  shebang; binary ruff links original executable: 0. Shape imports resolve this checkout.
- `PIP_NO_DEPS=1 make bootstrap`: 0 with onboarding Rust environment and initially shared target.
- Copy completed original Rust target with `cp -a --reflink=auto` to this clone: 0. Subsequent builds
  use this independent target to avoid concurrent maturin wheel-staging races.

## Red evidence

`python -m pytest -q tests/docs/test_signing_page_scope.py`: exit 1 before edits.

```text
FF                                                                       [100%]
=================================== FAILURES ===================================
_____________ test_migration_example_moves_with_its_manifest_entry _____________

    def test_migration_example_moves_with_its_manifest_entry() -> None:
        signing = (ROOT / "docs/SIGNING.md").read_text()
        migration = ROOT / "docs/MIGRATION.md"
>       assert "## Signed artifacts and migration" not in signing
E       AssertionError: assert '## Signed a...nd migration' not in '# Signing `...}\n    ```\n'
E         
E         '## Signed artifacts and migration' is contained here:
E           in read.
E           
E           ## Signed artifacts and migration
E           
E           A migrated artifact is a new file with a new manifest, so the old signature cannot be copied onto...
E         
E         ...Full output truncated (132 lines hidden), use '-vv' to show

tests/docs/test_signing_page_scope.py:12: AssertionError
___________ test_signing_scope_excludes_experimental_vault_and_query ___________

    def test_signing_scope_excludes_experimental_vault_and_query() -> None:
        signing = (ROOT / "docs/SIGNING.md").read_text()
        vault = (ROOT / "docs/VAULT.md").read_text()
>       assert "A profile that has a value vault" not in signing
E       assert 'A profile t... value vault' not in '# Signing `...}\n    ```\n'
E         
E         'A profile that has a value vault' is contained here:
E           ng else.
E           
E           A profile that has a value vault (`docs/VAULT.md`) records `vault: {"vault_id", "sha256"}` in its
E         ? ++++++++++++++++++++++++++++++++
E           manifest, so the signature covers the vault's hash: replace or alter the vault and `shape vault...
E         
E         ...Full output truncated (232 lines hidden), use '-vv' to show

tests/docs/test_signing_page_scope.py:27: AssertionError
=========================== short test summary info ============================
FAILED tests/docs/test_signing_page_scope.py::test_migration_example_moves_with_its_manifest_entry
FAILED tests/docs/test_signing_page_scope.py::test_signing_scope_excludes_experimental_vault_and_query
2 failed in 0.18s
```

## Validation

- `pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver
  -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka`: 0.
  Core and every requested plugin's `__file__` were asserted to be inside this clone: 0.
- Initial `python -m ruff check ...` and format attempt: 1 because copied Python package did not
  provide its executable in the new venv. After own entry-point setup, `ruff check src tests plugins
  benchmarks/vs_refengine scripts`: 0. Initial format check: 1 on the new test quote style;
  `ruff format tests/docs/test_signing_page_scope.py`: 0; final full format check: 0.
- `python -m mypy`: 0, no issues in 701 source files.
- Scope/inventory green check (`python -m pytest -q tests/docs/test_signing_page_scope.py
  tests/docs/test_reference_examples.py::test_restored_example_inventory`): 0, 3 passed.
- `SHAPE_KERNEL=python pytest -q tests/docs/test_signing_page_scope.py
  tests/docs/test_reference_examples.py::test_restored_example_inventory
  tests/docs/test_reference_examples.py::test_reference_page[SIGNING]
  tests/docs/test_reference_examples.py::test_reference_page[MIGRATION]
  tests/artifact/test_signing.py tests/artifact/test_signing_keys.py
  tests/cli/test_bugs_cli_cat_verify.py`: 0, 59 passed (3 expected warning reports).
- `SHAPE_KERNEL=python pytest -q tests/artifact tests/vault
  tests/docs/test_reference_examples.py::test_reference_page[PRIVACY_MODEL]`: 0, 256 passed.
- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: 0.
- `python scripts/check_doc_links.py --site site`: 0, no broken links.
- `python scripts/check_user_facing.py --site site`: 0, clean.
- `git diff --check`: 0 after removing the signing page's extra EOF blank line.

The moved migration command and expected output are unchanged. The inventory still has all
206 examples. SIGNING and the new MIGRATION reference runs exercise every runnable block on
those pages. Artifact and vault neighbors retain the signing/migration semantics.

## Additional setup findings

The first `timeout 300 make check` returned 2: its core-stage collection began while requested
plugin installation was still running and could not import `shape_fabric` in two untouched tests.
All static gates passed. This incomplete-setup run is not treated as a product failure; a second
bounded run after verifying every own plugin import records the usable full-check result below.

An additional `SHAPE_KERNEL=python pytest -q
 tests/docs/test_reference_examples.py::test_reference_page[CLI]` returned 1 in unchanged
CLI example 4 because the reused environment had numpy 2.5.4, pyarrow 26.0.0 and no tzdata,
while the recorded doctor output requires requirements-docs.txt pins (2.5.3, 25.0.1, 2026.3).
`pip install --no-deps --target /tmp/W-sign-1-doc-pins numpy==2.5.3 pyarrow==25.0.1
 tzdata==2026.3`: 0. Copy those packages into this lane venv after unlinking only its own
symlinks and superseded metadata: 0. No shared dependency files were changed. Re-run the
CLI/SIGNING/MIGRATION page examples with these documented versions.

Pinned-reference rerun: `SHAPE_KERNEL=python pytest -q
 tests/docs/test_reference_examples.py::test_reference_page[CLI]
 tests/docs/test_reference_examples.py::test_reference_page[SIGNING]
 tests/docs/test_reference_examples.py::test_reference_page[MIGRATION]`: 0, 3 passed.

## Recovery and full-check limitation

The completed-setup `timeout 300 make check` passed every static/repository gate, then
was terminated during core-stage tests at about 14%. Its log contains one `F`, but no
failure traceback or final test summary was emitted before termination. The wrapper's
numerical exit code was not preserved; this is an incomplete timeout result, not a
passing full suite. Log: `/workspace/prompt-runs/W-sign-1-make-check-complete-setup.log`.
No live lane pytest/make/timeout processes remained during recovery; no process was killed.

Recovery `ruff check src tests plugins benchmarks/vs_refengine scripts`,
`ruff format --check src tests plugins benchmarks/vs_refengine scripts`, and
`git diff --check`: each 0. An initial repeat of the 59-test focused command returned 1
(44 failed, 10 passed, 5 errors) after `/tmp` filled: tracebacks show
`OSError: [Errno 28] No space left on device`. `/workspace` still had 15 GB available.
A proposed deletion of the lane's temporary dependency staging directory was rejected
by the command tool; no files were deleted. The rerun instead sets `TMPDIR` to this
checkout's `.recovery-tmp`, leaving shared temporary files untouched.

That rerun returned 1: 58 passed, SIGNING example 0 failed during editable-install
setup because the recovery shell did not include the existing onboarding Rust path.
Maturin attempted its fallback import of missing `puccinialin`. The final repeat sources
`/workspace/.onboarding/env.sh`, activates this clone's venv, and retains the workspace
`TMPDIR`; no product code or shared packages were changed to address either setup failure.

Final focused repeat: exit 0, **59 passed**, 3 expected warning reports, 46.32 seconds.
Log: `/workspace/prompt-runs/W-sign-1-recovery-rust-focused.log`.
Origin/main and pre-commit HEAD both remain `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.
`git ls-remote --heads origin lane/W-sign-1`: 0, branch did not yet exist.
Final `git diff --check`: 0. Commit uses the required `W-sign-1:` prefix and DCO sign-off;
push is restricted to `lane/W-sign-1`. No open product questions; full-suite completion
remains the lead/CI validation responsibility described in the prompt.
