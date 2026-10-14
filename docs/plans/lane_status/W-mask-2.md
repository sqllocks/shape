# W-mask-2 lane status

Base: `origin/lane/W-mask-1` (`0629a0a`). Scope: documentation and regression coverage; no promotion or parser/behavior change.

## Finding and decision

The initial parent source-only inspection missed the generic flags. Actual command evidence on
this exact base proves that `--dry-run` already exists. `src/shape/cli/machine.py:246` registers
`mask` in `SPECS`; `machine.install` adds the standard flag to registered leaves without a native
flag, and `src/shape/cli/main.py:2227` calls it after parser registration. `machine.run` dispatches
this flag to the generic path planner. There is no missing parser wiring to fix and no reason
to introduce a mask-specific dry run. The requested regression tests were written before the
documentation changes and verify the existing behavior.

The path planner checks input existence and output parents. It does not run masking or inspect
keys, column selection or other mask-specific checks. The page now states that limitation.

## Exact-base command evidence

# W-mask-2 preflight on origin/lane/W-mask-1 (0629a0a)

Initial parent inspection of mask.py missed generic machine.install flags. Actual parser and invocations below prove --dry-run already exists.

$ shape mask --help
usage: shape mask [-h] -o DIR [--format {csv,parquet}] [--seed SEED]
                  [--key-file FILE | --key-env VAR] [--exclude COLUMN]
                  [--pii COLUMN=TYPE] [--json] [--dry-run]
                  PATH

(experimental) Find columns that hold personal data (e-mail, phone, names,
addresses, SSN, card numbers, IP addresses, IBANs, dates of birth, ...) from
their names and their values, and replace them with synthetic values in the
same format. Null positions and every other column are kept exactly; the same
original value gets the same replacement everywhere, so keys and the columns
that refer to them still match.

positional arguments:
  PATH                  a data file, or a directory of data files

options:
  -h, --help            show this help message and exit
  -o DIR, --output DIR  a new or empty directory for the masked files
  --format {csv,parquet}
                        the file format of a directory's files and of the
                        output (default: from the input file's extension; csv
                        for a directory)
  --seed SEED           random seed (default 42); not allowed with --key-file
                        or --key-env, whose key decides the masks
  --key-file FILE       keyed masking: read the secret key from FILE (not
                        readable by others, and outside the output directory);
                        the same value and key always give the same mask, in
                        any run
  --key-env VAR         keyed masking: read the secret key from this
                        environment variable
  --exclude COLUMN      leave this column as is
  --pii COLUMN=TYPE     mask this column as TYPE even if nothing marks it as
                        personal data
  --json                print the summary as JSON
  --dry-run             read and check the inputs and print what the command
                        would write or send, then exit 0 (2 for invalid
                        input); nothing is written and no connection is opened

Exit codes:
  0   ok
  2   bad input: a missing or unreadable file, the wrong kind of file,
      or a bad argument


exit=0; out_exists=False

$ shape mask mask-fixture.csv -o out --dry-run
would create out


exit=0; out_exists=False

$ shape mask mask-fixture.csv -o out --dry-run --json
{"actions": [{"action": "create", "target": "out"}], "command": "mask", "format": "shape-dry-run", "version": 1}


exit=0; out_exists=False

## Changes

- `docs/MASK.md`: standard dry-run flag, document format/action and validation scope; exact
  column-name scope across input tables, exclusion precedence, unknown-column errors,
  compatible forced types, and free-text limits. Existing runnable examples are preserved.
- `docs/THREAT_MODEL.md`: masking key leakage/output-directory exclusion, POSIX permissions,
  minimum key length/missing variable controls, named proving tests and residual risks.
- `tests/masking/test_mask_cli_guards.py`: four parametrizations exercise text/JSON previews
  with new/empty existing output directories; verify zero exit status, unchanged input bytes,
  no output writes, and one standard output action.

## Setup and command record

All work used the existing isolated checkout and its own Python 3.12 virtual environment.
Third-party dependencies were reused through read-only links excluding core/plugin distributions
and editable hooks; command wrappers point to the lane interpreter. The native target was copied
with `cp -a --reflink=auto` from the completed original target, then rebuilt in this lane.
`TMPDIR=/workspace/prompt-runs/tmp/W-mask-2`; Rust used onboarding `CARGO_HOME`/`RUSTUP_HOME`.
No shared dependency installation or target staging was modified.

- Remote identity and clean checkout inspection: exit 0 (`sqllocks/shape.git`).
- Required `CLAUDE.md`/`CONTRIBUTING.md` reads: successful; optional `AGENTS.md` absent
  (`cat` combined read exit 1 for that absent optional file).
- `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md`: exit 0;
  Decisions and section 4.3 read, never copied into tracked lane files.
- `python3.12 -m venv .venv`, third-party reuse, console wrapper setup, target copy: exit 0.
- `PIP_NO_DEPS=1 make bootstrap`: exit 0 (own native kernel built).
- An early docs/plugins install was stopped with SIGTERM (143) to avoid overlapping bootstrap;
  the sequential final `pip install --no-deps -e '.[docs]'` plus all required plugin editables
  completed with exit 0.
- Import verification of `shape` and `shape._kernel`: exit 0; both resolve within this checkout.
- Two focused-test invocations using nonexistent guessed neighbour filenames (`test_ci_flags.py`,
  `test_machine_output.py`) exited 4 without running tests; corrected to actual filenames.
- Initial focused run: exit 1, 115 passed/1 failed. The test expected one text-space before an
  existing output path, whereas standard generic formatting pads `write` to two spaces. The
  assertion now compares tokens; no implementation changed. This was a test expectation error,
  not red evidence for a behavior change. No behavior change required failing red evidence.

- Final focused suite (`pytest -q tests/masking tests/builtins/test_mask.py
  tests/cli/test_ci_flags_coverage.py tests/cli/test_json_envelope.py`): exit 0, **116 passed**.
- `pytest -q 'tests/docs/test_reference_examples.py::test_reference_page[MASK]'`: exit 0,
  **1 passed** (all retained page examples).
- `ruff check src tests plugins benchmarks/vs_refengine scripts`: exit 0.
- `ruff format --check src tests plugins benchmarks/vs_refengine scripts`: exit 0,
  2,140 files already formatted.
- `mypy`: exit 0, 698 source files.
- `python scripts/check_doc_links.py`: exit 0.
- `python scripts/check_user_facing.py`: exit 0.
- `NO_MKDOCS_2_WARNING=1 mkdocs build --strict`: exit 0.
- `python scripts/check_doc_links.py --site site`: exit 0.
- `python scripts/check_user_facing.py --site site`: exit 0.
- `git diff --check`: exit 0.
- Required `timeout 600 make check`: exit **124**, interrupted at the 600-second limit,
  about 16% into 18,438 selected core tests. Every preceding static/repository gate passed.
  The full coverage stage and subsequent heavy/Python-kernel/Rust stages are incomplete.
  One failure was printed before interruption; it was investigated rather than assumed harmless.
- `pytest --collect-only -q -m 'not emulator and not live and not heavy'
  --ignore=tests/demo/fabric --ignore=tests/demo/content`: exit 0 (18,438 selected);
  progress index 2,066 corresponds to
  `tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left`.
  The interrupted suite did not populate a failure cache (the cache was `{}`).
- Exact targeted rerun of that node: exit **1**, output mode `0600` fails `mode != 0o600`.
  `umask`: exit 0, **0077**. This lane changes neither that test nor the sink implementation.
- The same targeted node in an isolated shell with `umask 022`: exit **0**, 1 passed.
  This identifies an environment-sensitive pre-existing test assumption; no source, gate or
  tolerance was changed, and the broad check is still reported incomplete/unsuccessful.

Broad failure evidence:

```text
assert mode != 0o600
E assert 384 != 384
FAILED tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left
1 failed in 0.55s
```

Local complete logs are under `/workspace/prompt-runs/W-mask-2-*.log`, including the interrupted
broad run, exact failing rerun and isolated umask-022 rerun. No full-suite success is claimed.

## Open questions

None. Preserve the existing free-text sentence for the independent W-mask-3 pinning test.
