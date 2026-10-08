# AUD-cli — audit of the command line — status

Branch `lane/AUD-cli`, started from `int/INT-15` (f99563e). Area: every `shape` command and flag
(help text, exit codes, error messages, `--json` output) and the docs pages that document commands.
Paths: `src/shape/cli/`, `src/shape/__main__.py`, their tests, docs pages about CLI commands.
No D-xx/T-xx decision, gate or tolerance touched; §11 and §2.3 unedited; `$REFENGINE_ROOT` untouched.

## Phase 1 — findings

Reproduced from an empty folder, Python 3.11.15, editable install with the compiled kernel,
`.[dev,streaming,advanced]` plus `plugins/shape-domains` (the CI install).

1. **high** — `shape diff` on capture/evidence JSON ignores `--fail-on-drift`, `--json` and every
   threshold flag (`main.py`, the legacy `diff` branch of `_dispatch`). Repro: `shape capture
   data.csv -o base.json; shape capture later.csv -o later.json` (later has new extremes);
   `shape diff base.json later.json --fail-on-drift --json r.json --ignore amount` prints the
   changes, exits 0 and writes no `r.json`. Expected: exit 1 with drift under `--fail-on-drift`,
   `--json` written, flags that only apply to profiles refused. A CI gate built on it never fails.
2. **medium** — `shape check EVIDENCE.json CONTRACT.json --json OUT` does not write `OUT`
   (`_cmd_evidence`). Expected: the result written, as for a profile.
3. **medium** — "artifact not verified" notices print as raw Python warnings
   (`/…/src/shape/cli/profiles.py:227: ArtifactNotVerifiedWarning: … \n  return shape.load(…)`)
   instead of the one `shape: note: …` line `docs/SIGNING.md` promises, for `shape profile
   export|import|list|validate|safe|registry …`, `compatibility`, `certify-shapes`,
   `registry diff` (which also names its temporary files) and `conformance` (its self-test's
   temporary files). Only the commands dispatched through `main._run` route them.
4. **medium** — `shape fidelity A B --format text` (or any unknown format) fails after the whole
   comparison with `shape: error: missing key "no plugin 'text' in group shape.reports" in the
   input`: the format is not checked up front, and `errors.describe` turns every `KeyError` into
   "missing key … in the input".
5. **medium** — `shape fidelity A B --tier N -o R.md -o R.html` writes JSON into `R.md` and ignores
   `R.html` (`tiers.run_fidelity`); the help says `-o` picks the format by extension and repeats.
6. **medium** — `shape generate|describe|presets|emit|stream|continue|time-travel|chaos|
   generate-drift SCHEMA.yaml` fail with `not valid JSON: Expecting value…`, while `shape validate
   SCHEMA.yaml` reports the same file valid and `shape doctor` lists PyYAML as needed for "YAML
   generation schemas" (`generation.load_target` reads JSON only).
7. **medium** — `shape learn` writes non-standard JSON (`"std_dev": Infinity`) when a column's
   spread overflows (`x` = 1e308, -1e308); `shape generate` then refuses the schema. Expected: the
   CLI never writes a `.json` file that is not JSON. (Root cause in `shape.generation.learn`,
   outside this area: filed.)
8. **medium** — `shape quality` exits **2** when the data violates the rules; `docs/CLI.md` says 2 is
   bad input and 1 a failed check.
9. **low** — `docs/CLI.md` says each command's `--help` names its verdict exit codes; `check`
   (4 for an evidence contract) and `compatibility` (5) do not, and `conformance`, `version`,
   `quality`, `key`, `fd`, `privacy-k`, `query`, `certify-shapes` have no help text at all (absent
   from the command list of `shape -h`, positional arguments unexplained).
10. **low** — errors on a file that is not JSON or not text do not name the file:
    `generate|describe|presets|emit|validate X.json` → `not valid JSON: Expecting …`;
    `validate|from-ddl|describe BIN` → `'utf-8' codec can't decode byte …`;
    `continue --transitions BAD.json` the same.
11. **low** — `shape generate DOMAIN --scale-mode local_single` with no `-o`/`--sink` prints
    `generating into memory (nothing is kept)` twice (`scale.build_request` and `run_scale`).
12. **low** — `shape emit|stream --poison-fraction -1` is accepted silently (the other fractions are
    range-checked); `--retries -1` and `--checkpoint-every 0` are accepted too.
13. **low** — `shape emit … --live-target T --live-report R.txt` refuses the report format only
    after the whole stream has been delivered (exit 2 after the work is done).
14. **low** — output closed early (`shape plugins list | head`) ends with `shape: error: Broken
    pipe` and exit 2.
15. **low** — `docs/DEMO.md` shows `shape demo notebook retail --mode seeding -o retail.ipynb`;
    `-o` is not accepted (`--output` only); `demo report` the same.
16. **low** — `shape profile registry delete MISSING` exits 1 with `shape: profile '…' not found.`
    while `tag`/`diff` of a missing profile exit 2 with `shape: error: profile not found: …`.
17. **low** — `--log-level bogus` is accepted silently.
18. **low** — `--metrics DIR/MISSING/m.json`: the command runs to the end, then exits 2 with
    `file not found: …`, hiding the command's own result.
19. **low** — `shape generate --chunk-rows 0` is silently ignored (the default is used); a negative
    value is refused.
20. **low** — missing-path wording still varies: `learn`/`mask` `path not found: X`, `profile
    registry save` `not found: X` (the documented form is `file not found: X`; `verify`/`drift`/
    `fidelity` `Path not found:` is pinned by `tests/quality/test_verify.py`, see ISS-cli).
21. **low** — `shape cat` as a git textconv prints a `shape: note: /tmp/git-blob-…/x.shape is not
    signed … (check it with --verify PUBKEY)` line on every `git diff`; `cat` has no `--verify`.
22. **low (outside the area)** — `shape demo run S --input-file MISSING` exits 1 (run failed), not
    2 (bad input) as `docs/DEMO.md`'s exit codes put it; the demo API records it as a failed run.

Coverage of `shape.cli` before any change: see "Commands and results".

## Phase 2 — issues

No duplicates found (searched open issues; #5, #27 and #4 are related but different).

| Finding | Issue |
|---|---|
| 1 | #107 |
| 2 | #108 |
| 3 | #109 |
| 4 | #110 |
| 5 | #111 |
| 6 | #112 |
| 7 | #113 |
| 8 | #114 |
| 9 | #115 |
| 10 | #116 |
| 11 | #117 |
| 12 | #118 |
| 13 | #119 |
| 14 | #120 |
| 15 | #121 |
| 16 | #122 |
| 17 | #123 |
| 18 | #123 |
| 19 | #124 |
| 20 | #125 |
| 21 | #126 |
| 22 | #127 |


## Phase 3 — fixes

Every fix has a regression test in `tests/regressions/test_aud_cli.py` that was committed failing
first (the failing output is in the test commits: 9399734 for #107, c3e968a for #108-#114,
133bca2 for #115-#125). The "not verified" tests (#109) and the closed-pipe test (#120) run
`python -m shape` as a subprocess, because pytest records warnings instead of printing them.

| Issue | Finding | Fix commit | What changed |
|---|---|---|---|
| #107 | 1 | b5e68f6 | `diff` of captures: `--fail-on-drift` exits 1 on any change, `--json` written, profile-only flags refused (docs/CLI.md) |
| #108 | 2 | d74ed7c | `check` of an evidence document writes `--json` |
| #109 | 3 | df988eb, ff85ce5 | notices routed for the whole dispatch; `registry diff` and `conformance` read with notices off (`errors.quiet_notices`) |
| #110 | 4 | 539fbc5 | `fidelity --format` checked against the installed report formats first; `errors.describe` shows a `KeyError` that carries a sentence as it is |
| #111 | 5 | fb694b9 | a `--tier` report goes to every `-o`; a non-`.json` name is refused before the run (help says so) |
| #112 | 6 | 4f1a9b6 | `load_target` reads `.yaml`/`.yml` with the safe loader `validate` uses (`validate.load_document`; `_load` kept for the bridge) |
| #113 | 7 | efb9957 | `learn` refuses to write a non-finite number, naming the path and column; the overflow itself is in `shape.generation.learn` (outside the area): **#113 stays open for it** |
| #114 | 8 | 76b7741 | `quality` exits 1 for a failed check |
| #115 | 9 | 1b0044c | help for `conformance`, `version`, `quality`, `key`, `fd`, `privacy-k`, `query`, `certify-shapes`; `check` and `compatibility` name exit 4 and 5 |
| #116 | 10 | 2a6beda | JSON and text decode errors name the file; they keep their exception types, so the bridge's error codes are unchanged |
| #117 | 11 | e1856fa | the into-memory note printed once |
| #118 | 12 | 164f1cf | `--duplicate-fraction`/`--poison-fraction` 0-1, `--retries` >= 0, `--checkpoint-every` >= 1, checked before any work |
| #119 | 13 | 164f1cf | `--live-report` extension checked before streaming |
| #120 | 14 | 276f2d4, f429fb4 | a closed standard output: exit 141, nothing printed (docs/CLI.md exit table) |
| #121 | 15 | ca052ca | `demo notebook` and `demo report` take `-o` |
| #122 | 16 | e774e35 (test), aaa1a67 (fix) | `profile registry delete` of a missing profile: exit 2, `shape: error: profile not found: NAME`, as `tag`/`diff`; per the lead's decision (2026-10-03) the one assertion in `tests/cli/test_profile_registry_cli.py::test_registry_delete` changed from `== 1` to `== 2` |
| #123 | 17, 18 | a8b4986 | an unknown `--log-level` (with `--log-json`) and a `--metrics` path in a missing folder are refused before the command |
| #124 | 19 | c1c8274 | `--chunk-rows 0` reaches the engine, which refuses it |
| #125 | 20 | 0aee269 | `learn`, `mask`, `profile registry save`: `file not found: PATH` |

CHANGELOG.md: "Fixed (command line)" (586175f).

## Left open, and why

- **#126** (finding 21): `shape cat` as a git textconv prints a "not signed" note on every
  `git diff`. docs/SIGNING.md says every CLI read prints the note, so silencing it for `cat` is the
  owner's call.
- **#127** (finding 22, `shape.demo.api`) and **#152** (`shape quality` crashes with `TypeError` on a
  text value in a numeric column, `shape.quality.policy`): outside this area, filed only.
- **#113**: the CLI side is fixed. The overflowing `std_dev` in `shape.generation.learn` is not.
- `verify`/`drift`/`fidelity` still say `Path not found:` (pinned by `tests/quality/test_verify.py`,
  as ISS-cli recorded).

## Pre-existing failures (not this lane's)

On unmodified INT-15 (f99563e), in this container: `tests/demo/fabric/test_udf.py` and
`test_generate_udf.py` cannot import (`libodbc.so.2` missing: no unixODBC here);
`tests/demo_cmd/test_notebook_and_outputs.py::test_the_semantic_model_is_a_bim_of_the_learned_schema`
and `::test_all_writes_the_page_and_the_model` need the `shape-fabric` plugin, which the main CI
job does not install. With the fabric test requirements installed, pip pulled pyarrow down to
19.0.1, which failed three more tests (float16 hashing, landing, ...); with pyarrow 25.0.1 (the
version the plan pins) they pass.

## Commands and results (this session, after merging origin/build/main-plan 5c91ea5)

- `ruff check src tests plugins benchmarks/vs_refengine`: all checks passed. `ruff format --check`
  (same scope): 1088 files already formatted. `mypy`: no issues in 436 files.
  `python scripts/check_user_facing.py`: clean.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live" -n 4 --cov=shape.cli`: 7078 passed,
  2 skipped, 3 failed, 8 errors. `SHAPE_KERNEL=python` (same): 7078 passed, 2 skipped, 3 failed,
  8 errors. In both modes all of them are pre-existing on INT-15 (see above): the 8 errors are
  `tests/demo/fabric/*udf*` (no `libodbc.so.2`), two failures are `tests/demo_cmd` semantic-model
  tests (no `shape-fabric` plugin), and
  `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`
  depends on test order under xdist: it also failed on the baseline run, and the file passes alone
  in both modes (41 passed).
- Coverage of `shape.cli`: 83% on INT-15 before this lane, 90% after (3954 statements, 415
  missed).
- No benchmark or equivalence verifier output was touched (CLI messages, exit codes and option
  checks only).

## Commands and results (this session, #122; origin/build/main-plan still at 5c91ea5, no merge)

Fresh container, venvs built per plan section 1: `~/.venvs/shape` (`.[dev,streaming,advanced]`,
`plugins/shape-domains|eventhubs|sqlserver|fabric`, pyarrow 25.0.1), `~/.venvs/fabric`
(`tests/demo/fabric/requirements.txt`, pyarrow 19.0.1, unixodbc installed), RefEngine baseline from
`benchmarks/vs_refengine/setup_refengine.sh` (read only).

- `pytest tests/regressions/test_aud_cli.py -k registry_delete`: failed first (`assert 1 == 2`),
  in e774e35; both files `tests/regressions/test_aud_cli.py` and
  `tests/cli/test_profile_registry_cli.py`: 61 passed after the fix.
- `make check`: exit 0 (ruff, format, mypy, vulture, lint-imports, the check scripts, coverage
  gate run 6827 passed 2 skipped, the heavy Rust run, `SHAPE_KERNEL=python tests/kernel`,
  cargo fmt, clippy, test).
- `python scripts/check_user_facing.py`: clean.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric`: 6907
  passed, 2 skipped, 13 deselected (by the marker expression), exit 0.
- `tests/demo/fabric` in the pyarrow 19 venv: 216 passed. `tests/demo/content`: 38 passed.
- `SHAPE_KERNEL=python pytest -m "not emulator and not live" --ignore=tests/demo/fabric`: 6907
  passed, 2 skipped, 13 deselected, exit 0 (1 h 28 min: the 24M/48M-row bounded-profile memory test
  runs the pure-Python kernel). Run serially, so the order-dependent credential-reference test
  (#77) passed. No failures in either kernel mode; `tests/demo_cmd` semantic-model tests ran with
  `shape-fabric` and `shape-eventhubs` installed from their in-repo paths and passed. The fabric
  tests were run in their own venv (above) because the main venv lacks their requirements.
- No failure remains; nothing is recorded as pre-existing. No `.github/workflows/*` diff needed.
