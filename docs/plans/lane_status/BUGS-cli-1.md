# BUGS-cli-1: command line, registry commands, demo and scale job status

Branch `lane/BUGS-cli-1`, started from `build/main-plan` (5c91ea5; still its tip when this was
written, so the merge of `origin/build/main-plan` is a no-op). Each issue: a failing regression
test commit (failing output in its message), then the fix commit. Every issue has a comment with
its fix commit; none is closed.

| Issue | Still reproducible? | Test commit | Fix commit | Result |
|---|---|---|---|---|
| #278 crash path unredacted | yes | d6a342a | 5accf2b | fixed |
| #152 text in a numeric column | yes | 1c0965b | 9a580c9 | fixed (exit code: see below) |
| #530 BOM skips the leak scan | yes | 640170c | c590d21 | fixed, plus the same hole in `is_raw_profile` |
| #543 Fabric status `Deduped` | yes | 568a277 | 3551dd4 | fixed (`cancelled`) |
| #311 conformance warnings | yes | 8575591 | caac1b6 | warnings fixed; summary line not done |
| #528 concurrent `demo init` | yes (28 of 72 saves lost) | d434817 | f5f2d72 | fixed (`shape.demo.filelock`) |
| #126 `shape cat` textconv note | yes | 231d7b3 | 64d1795 | partly fixed; owner decision left |

## Per issue

- **#278** `_main` redacts the message of an unexpected error with `redact_text` (`src/shape/cli/main.py`).
  `--debug` still propagates the raw traceback (the user asked for it).
- **#152** `validate_rows` treats a `TypeError` from a min/max comparison as a violation
  (`src/shape/quality/policy.py`, outside this lane's paths: the cause is there). The issue expects
  exit 1; inferred rules are severity `warning`, so `shape quality` reports the row and keeps
  its exit code (0 when only warnings), exactly as for an out-of-range number. Making inferred rules
  errors would change what every `shape quality` run returns: not done, decision for the owner.
- **#530** New `shape.registry.local.json_object` (reads bytes as `json.loads` does), used by the
  CLI's `_safe_document` and by `is_raw_profile`. The issue says `is_raw_profile` already did this; on
  `build/main-plan` it did not (same `lstrip()[:1] == b"{"` test), so a raw `shape profile export`
  with a BOM was stored without `--allow-raw`. Fixed too; the edit to `src/shape/registry/local.py` is
  outside this lane's paths.
- **#543** `Deduped` maps to `cancelled`. `Deduplicating` stays `running`: an existing test pins it.
- **#311** `conformance()` ignores `ArtifactNotVerifiedWarning` inside the suite only
  (`src/shape/validation/suite.py`, outside this lane's paths); a test shows the filter does not
  leak. The issue's "32/32 passed" line changes the output format: not done, owner's call.
- **#528** `shape.demo.filelock.locked(path)`: an exclusive lock on `<path>.lock` (`fcntl.flock` on
  POSIX, `msvcrt.locking` on Windows, standard library only), 30 s timeout with a clear error,
  released on error. `ConnectionRegistry.save` and `delete` run their read-modify-write under it.
  Tests: 8 threads, 6 processes x 12 saves, delete, timeout, release after a refused profile or an
  exception. The Windows branch was not run (Linux session).
- **#126** `shape cat` takes `--verify PUBKEY` (the option its note names). With
  `shape git-setup --command 'shape cat --verify PUBKEY'` every diff is verified and prints no note;
  an unsigned file or the wrong key is refused (exit 1). A plain `shape cat` still prints the note,
  because docs/SIGNING.md promises the note on every read without a key. Silencing it for every
  textconv is an owner decision; the issue stays open for it. `docs/SIGNING.md` documents the option.

No existing test was changed, skipped or weakened. No equivalence-verifier bytes changed (no
generator, profile or artifact writer was touched), so the verifiers were not run.

## Commands and results (Linux, Python 3.11.15, fresh venv with `pip install -e ".[dev]"` and
`-e plugins/shape-domains`)

- `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_refengine`: clean (1095 files formatted).
- `mypy`: no issues in 437 source files.
- `python -m compileall`, `vulture`, `lint-imports` (1 kept, 0 broken), `check_requirements`,
  `check_secrets`, `check_user_facing`, `check_shipped_data`, `check_plugin_skeletons`,
  `check_conformance_coverage`: all pass.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content`:
  6844 passed, 19 skipped, 2 failed (below).
- `SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"` (same ignores):
  6802 passed, 19 skipped, 55 deselected, 2 failed (the same two). The first python-kernel attempt
  included the `heavy` marker and was stopped after more than 30 minutes inside
  `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` (a 24-million-row
  profile in pure Python); everything before it (62% of the run) had passed. The `heavy` tests were
  therefore run only in the Rust-kernel mode (included in its 6844 passed), as `make check` does.

## Not run, and why

- `tests/demo/fabric` (needs `nbformat` and the fabric requirements plus unixODBC) and
  `tests/demo/content` (needs `$REFENGINE_ROOT`): not installed in this session; the same two
  directories `make check` ignores. Collecting them without the extras is a collection error.
- 19 skips: `sklearn` and `shape_databases` are not installed.
- `make check` as one command: its steps were run individually above (the pytest steps with the
  coverage gate, the `heavy` marker pass, and the `cargo fmt`, `clippy` and `cargo test` steps were
  not run as such; no Rust file changed).
- Windows branch of `filelock` (see #528).

## Failures that are not this lane's

`tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema`
and `::test_all_writes_the_page_and_the_model` fail with "the semantic model needs the shape-fabric
plugin"; they fail the same way on `build/main-plan` without these commits (checked with `git stash`).
`tests/scale/test_spark.py::test_submit_spark_end_to_end_registers_a_job_without_secrets` failed
before `plugins/shape-domains` was installed (no `retail` domain) and passes after.

## Workflow files

No `.github/workflows` change is needed.
