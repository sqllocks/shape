# INT-20: integration of the finished lanes on int/INT-19

Ready for sharded verification: 356196009639a94cca4defe9c53ffb53ae64fc78

(`35619600` is the merged tree of round 3, see "INT-20 r3 merges"; the commit after it changes
only this file. The previous candidate was `535e9274`.)

Branch `int/INT-20`, started from `origin/int/INT-19` (f30c1122) and merged with it again when it
moved (`origin/int/INT-19` f3ffa7c6, which carries `origin/int/INT-18` 9886a7d8; merge b2f5e99e).
Merged a third time before the final checks: `origin/int/INT-19` f5062835 (its lane status file only),
merge ce1578aa. INT-19 then moved again (d702c6f8, which takes the newer INT-18 including the W1-15
revert); after the lead's answers of 2026-10-05 it was merged (ae9105eb, decision 8), and
BUGS-721, SEC-high and W8-06 were merged on top. W1-15 and W8-04 are **held** (decision 8 (b)).
Every lane was merged with a merge commit (`INT-20: merge lane/<X>`), never rebased or squashed;
nothing was force-pushed. A lane that moved after its merge was merged again (the second row of
AUD-io, AUD-pluginfw, W8-02, W8-04). §11 and §2.3 of the plan are not edited.
`.github/workflows/*` is not edited (`git diff f5062835 HEAD -- .github`, INT-19 as last merged, is empty); the
lanes' workflow diffs are collected below. The pinned baseline checkout was only read
(`scripts/setup_refengine.sh`, HEAD 422e78d). No test was skipped, xfailed or deselected, no
tolerance or gate changed, no D-xx or T-xx decision changed. Every test this integration changed is
listed under "Fixes" with the reason; none lost an assertion about behaviour that the merged tree
still has.

## INT-20 r3 merges

Round 3 (2026-10-05), on `f687ac3b`, in worktree `land20`. Merge commits only, nothing rebased or
force-pushed; §11 and §2.3 of the plan are not edited by this branch (the plan file is
`origin/build/main-plan`'s, unchanged); `.github` is not edited; the pinned baseline checkout was
only read. No test was skipped, xfailed or deselected (other than the `emulator`/`live` markers),
no gate, tolerance, D-xx or T-xx decision changed.

| Merged | Head | Merge commit | Conflicts and resolution |
|---|---|---|---|
| origin/int/INT-19 | `3a51d44d` | `2b0f7527` | `CHANGELOG.md`: INT-19's T-07 text (the declared floors stay `numpy>=2.0,<3` and `pyarrow>=14.0.1`; `ci/constraints-min.txt` pins the tested 2.3.0/19.0.1) and this branch's HUNT2-scenario entry. `pyproject.toml` auto-merged to T-07's floors (INT-18 f5dd2f62); `io/readers.py`, `io/identifiers.py` (c1999097, the streamed-CSV memory fix) auto-merged. |
| origin/build/main-plan | `2722c381` | `0658f15c` | none. `docs/plans/COMPLETION_PLAN.md` is build/main-plan's file exactly (`git diff origin/build/main-plan -- docs/plans/COMPLETION_PLAN.md` is empty); it brings `INT-18.md`. Its code (the INT-18 landing) was already here through INT-19. |
| lane/BUGS-demo-3 | `1a5ee8c3` | `b79f9f11` | `pyproject.toml`: this branch's `advanced` floor and `integrations` extra, plus the lane's `[faker]` extra and `faker>=24` in `[all]`. `plugins/shape-domains/pyproject.toml`: this branch's licence files, classifiers and URLs + the lane's `faker>=24` dependency. `plugins/shape-fabric/pyproject.toml`: this branch's classifiers and URLs + the lane's core-only dependencies and `[eventhubs]`/`[sqlserver]` extras. `scripts/build_pure_wheel.py`: this branch's metadata (derived from pyproject.toml, every extra, so `[faker]` is provided; the lane's hand-kept `DEMO_REQUIRES` list is not needed). `shape_fabric/_tsql.py`: this branch's `Encrypt=Strict`/`APP`/`MARS_Connection` handling and timeout check with the lane's lazy driver default (`_sql().DEFAULT_DRIVER`); both docstrings. `docs/INSTALL.md`: this branch's extras list + `faker`. `CHANGELOG.md`: both. |

**Held:** W1-15 and W8-04 stay out (decision 8 (b)); none of the three merges brings either back
(`git diff f687ac3b HEAD` touches none of their files except the provenance newline below).
**T-07:** `pyproject.toml` keeps `numpy>=2.0,<3`, `pyarrow>=14.0.1`.

Fixes on the r3 tree (each failed on the merged tree; the test came first):

| Commit | Fix |
|---|---|
| `880cf49f` | `tests/demo/core/test_wheel.py` expected BF-76's floors (this branch's 8b6b0853); with INT-19's T-07 floors back in pyproject.toml it failed; it now expects `numpy>=2.0,<3` and `pyarrow>=14.0.1` (checked failing before). The build script's docstring names the same floors. |
| `35619600` | `io/provenance.py`'s sidecar write names `newline="\n"`: the hold of W8-04 (134b3c1c) had reverted it, so BUGS-portability-1's #238 scan (`tests/test_portable_text_writes.py`) failed on `f687ac3b` already. |

### Checks of round 3 (this session)

The static checks and the verifiers ran on `880cf49f`; `35619600` only adds a `newline` argument
to one write, after which `ruff check`, `ruff format --check`, `mypy` and `check_user_facing` were
run again on the whole tree (clean, 1989 files, no issues, clean).

Venv `/tmp/claude-0/kv20` (python 3.11, `pip install -e '.[dev,streaming,advanced]'`, which builds
the Rust kernel; every first-party plugin editable `--no-deps`; `tests/demo/fabric/requirements.txt`,
which resolves pyarrow 19.0.1). Pinned baseline at 422e78d with its venv `refengine-venv2`, only read.

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_refengine) | clean / 1989 files formatted |
| `mypy` | no issues in 684 source files |
| `compileall`, `bandit -q -r src -ll`, `vulture`, `lint-imports` | clean; 1 contract kept |
| `scripts/check_*.py` (8) | exit 0 each; `check_user_facing: clean` (D-13) |
| `gen_exit_codes --check`, `gen_failure_modes --check`, `cli_surface.py --check` | exit 0; 0 breaking, 0 notes |

Equivalence verifiers (no timing):

| Verifier | Result |
|---|---|
| `demo_1to1/verify.py --negative-control`, `SHAPE_KERNEL=rust` | exit 0, VERDICT PASS, negative control flagged |
| `demo_1to1/verify.py --negative-control`, `SHAPE_KERNEL=python` | exit 0, VERDICT PASS, negative control flagged |
| `bridge_1to1/verify.py --negative-control` | exit 0, 67/67 checks, 17/17 commands |
| `safe_profile_1to1/verify.py` | exit 0, PARITY OK |
| `profile_1to1/verify.py --impl shape` | exit 0, 49 datasets PASS |
| `domain_1to1/verify.py --domain retail --scale small --impl shape` | exit 0, VERDICT PASS |
| `fabric_commands_1to1/verify.py` | exit 0, VERDICT PASS |

Tests of the directories the r3 merges and fixes touch (`tests/demo tests/cli tests/plugins
tests/release tests/diff tests/demo_cmd tests/docs tests/packaging tests/regressions/test_aud_cli.py
tests/test_portable_text_writes.py plugins/shape-fabric/tests plugins/shape-domains/tests`,
`-m "not emulator and not live"`):

| Kernel | Result |
|---|---|
| `SHAPE_KERNEL=rust` (on `880cf49f`) | 2 failed, 3654 passed: the #238 scan (fixed by `35619600`; with it, the scan and `tests/io/test_provenance.py`, `tests/chaos/test_input_check.py` pass in both kernels, 38 passed) and the Lakehouse test below |
| `SHAPE_KERNEL=python` (started on `880cf49f`; `35619600` was committed while it ran and the #238 scan, near the end, passed) | 1 failed, 3655 passed: the Lakehouse test below |

`plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips` is
**environment-only, not merge-caused**: `tests/demo/fabric/requirements.txt` resolves pyarrow
19.0.1 here, and pyarrow 19 reads a Parquet `dictionary<int8>` column back with int32 indices
(checked with a two-row table), so the schema comparison fails; neither the test nor
`shape_fabric/lakehouse.py` changed in this round (`git diff f687ac3b HEAD` is empty for both).
It passes with a newer pyarrow, as in the earlier rounds' venv.

## Merged lanes

| Merged | Head merged | Merge commit |
|---|---|---|
| lane/BUGS-contracts | `4f4fe87d` | `e81dbd90` |
| lane/OWN-425 | `622b4601` | `8e831a88` |
| lane/OWN-429 | `4a1b446e` | `863af2fe` |
| lane/BUGS-plugins-2 | `d8d54a16` | `b5c7b54d` |
| lane/BUGS-stream-1 | `44cd4a2f` | `22ff7456` |
| lane/BUGS-profile-1 | `ade53a25` | `9a42c2e3` |
| lane/BUGS-demo-2 | `336d8edf` | `0f7f587c` |
| lane/BUGS-gen-1 | `a6eabc62` | `c2d9769b` |
| lane/BUGS-kernel-2 | `a8b9ec9e` | `1cd3d70d` |
| lane/BUGS-packaging-1 | `53c29599` | `0475c600` |
| lane/BUGS-portability-1 | `7860fee6` | `9a82313c` |
| lane/BUGS-builtins-1 | `f127ceb8` | `d1f0759e` |
| lane/HUNT2-cli | `5274d83f` | `724dc662` |
| lane/HUNT2-io | `6ec2bc04` | `1b98375f` |
| lane/HUNT2-kernels | `754e38b1` | `760585a0` |
| lane/HUNT2-plugins | `e27bd88c` | `e42db852` |
| lane/HUNT2-privacy | `49170977` | `eb83e40e` |
| lane/HUNT2-profile | `b716a3d4` | `f728ecf0` |
| lane/HUNT2-quality | `7410ef71` | `78de26ad` |
| lane/HUNT2-scenario | `87d844a4` | `025c04d7` |
| lane/HUNT2-generation | `660e1d85` | `38fed10f` |
| lane/AUD-stream | `19fef27f` | `d6cd9d6c` |
| lane/HUNT2-streaming | `d62c11d8` | `3094fc8b` |
| lane/AUD-chaos | `f0cc101a` | `143eea4f` |
| lane/AUD-io | `130e5df9` | `ac369726` |
| lane/AUD-pluginfw | `840c55f4` | `5c159fef` |
| lane/W7-03 | `c749b83b` | `7347bcea` |
| lane/W7-05b | `4bb987fb` | `b09e068e` |
| lane/W8-01 | `4dc5d6ba` | `ff44a3a1` |
| lane/W8-02 | `d2733b54` | `c3eb7943` |
| lane/W8-04 | `ebd1729a` | `cc4fdbf8` |
| lane/P8-03 | `210592b0` | `cd9ff429` |
| lane/G6-eval | `2c5e2a12` | `e89a6177` |
| lane/G7-eval | `81b31ac8` | `08852e50` |
| lane/W7-01 | `447058cb` | `01056fec` |
| lane/PF-05-fin | `123a9367` | `365aa187` |
| origin/int/INT-19 | `f3ffa7c6` | `b2f5e99e` |
| lane/BUGS-tests-1 | `530c4019` | `d39788f3` |
| lane/AUD-tests | `d47e75ef` | `6d8276a8` |
| lane/W8-03 | `453b31f5` | `b3cdc5db` |
| lane/AUD-io | `976b03d9` | `04fe751a` |
| lane/AUD-pluginfw | `c493dfa5` | `8f97089b` |
| lane/W8-02 | `c3fd4bbd` | `ece1f7a5` |
| lane/W8-04 | `a5a2554f` | `55487125` |
| origin/int/INT-19 | `f5062835` | `ce1578aa` |
| origin/int/INT-19 | `d702c6f8` | `ae9105eb` |
| lane/BUGS-721 | `6815c11d` | `6f375e96` |
| lane/SEC-high | `bd0a6ed9` | `419e3dd2` |
| lane/W8-06 | `f63630d8` | `339fea01` |
| origin/int/INT-19 | `3a51d44d` | `2b0f7527` |
| origin/build/main-plan | `2722c381` | `0658f15c` |
| lane/BUGS-demo-3 | `1a5ee8c3` | `b79f9f11` |

**Held after merging (decision 8 (b)):** W8-04 (its rows `cc4fdbf8` and `55487125` above are
reverted by 134b3c1c) and W1-15 (re-applied by 031fde56, reverted by 535e9274), so neither is in
the tree, as on INT-19.

Already in `int/INT-19` (ancestors, nothing to merge): none of the listed lanes. `BUGS-contracts`
was not in `int/INT-18` when it was merged here (first row); the newer `int/INT-18` merges it too
(9cfef6c9), which is the same head, so the two meet without conflict when INT-19 brings it.

## Lanes left out, and why

| Lane | Head | Why |
|---|---|---|
| W1-17 | `e245042b` | Instruction: not merged unless its status says complete. Its status file (the lane's only commit not in this branch, e245042b) records results but does not say the package is complete. Its code (`shape.io.provenance`, `shape.chaos.input_check`, remote confirmation) is already in `int/INT-19` through the INT-18 squash d948da9c, so leaving the lane out removes no code. |
| P6-01 lanes (P6-01-fin, -int, -perf-domains, -perf-engine, -perf-r4, -seed, a, b, c, d, e, e-seed) | - | Instruction: not merged unless their status says complete. None does: P6-01d "three acceptance items are NOT met", P6-01e "T-21 verify ... 18 of 18 cells exit 1", P6-01-perf-r4 "built, measured and verified" with the gate reached for some domains only, P6-01-seed "investigation done", P6-01-fin has no status line. |
| W8-05 | `893edd2c` | Status: "blocked, nothing built" (the vault of W5-03 and safe capture of W1-11 were not in its base). |
| W8-04, W1-15 | - | Held, decision 8 (b): the cross-platform proof failed. |
| P6-01-fin (for INT-21) | `83123a31` | Instruction (2026-10-05): merge only if its status says complete; its table says P6-01a "**No.**" (only owner-accepted chance cells at medium), so it is not complete. |
| BUGS-demo-3 | `1a5ee8c3` | **Merged in round 3** (`b79f9f11`) on the lead's instruction of 2026-10-05: its status file reports #308, #309 and #310 fixed with regression tests and its checks run on the final code; its remaining failures are on its base too. See "INT-20 r3 merges". |

W8-04's newest head (1b9e7ac9) is **not** merged: after 55487125 (its a5a2554f) the lane only added
status commits, on top of a merge of the newer `origin/int/INT-18` (30+ commits, among them the
INT-16 landing, that INT-19 has not merged). Merging it brings that INT-18 range in with many
conflicts in files the lane does not touch, so it was aborted; it comes in through INT-19.

W8-03 was left out at first (its status said the python-kernel suite was still running); its
status now records the python-mode results, so it was merged (b3cdc5db).

## Conflicts and how they were resolved

Each resolution keeps both sides' behaviour; where the two sides could not both hold, the choice
is listed under "Decisions for the lead".

- BUGS-contracts: clean (CHANGELOG automerged)
- OWN-425: CHANGELOG; semantic_model.py: HEAD's explicit `measures` argument (per-table measure override) + OWN-425's model-wide qualification of colliding names (applies after both).
- OWN-429: fabric-writers.md + sqldb.py docstring: W2-10's upsert/identity/constraints text + OWN-429's commit-with-rows statements; prepare_table: W2-10's DELETE for FK-referenced truncate kept, without its commit (OWN-429). Found interaction: W2-10 constraints=disable commits after NOCHECK -> commits truncate/drop early; test 6d55f242, fix follows.
- BUGS-plugins-2: #429 docs part (5ec0b033, d8d54a16) superseded by OWN-429 per owner: docs keep OWN-429 text; warehouse.py docstring states the OWN-429 behaviour; test_write_mode_atomicity.py kept and its expectations follow OWN-429 (truncate/replace keep the 7 old rows; docs claim "old rows"). test_offline_lock.py: BUGS-plugins-2's #352 assertion + 2 new tests, with HEAD's #259 tests. CHANGELOG: #294 entry.
- BUGS-stream-1: runtime.py imports+constants both; emit/runtime.py: HEAD's _lag/_reason + lane's send_start/first_start one-batch elapsed, then HEAD's dead-letter counts and progress finish. CHANGELOG lone ### Fixed folded.
- BUGS-profile-1: infer.py import of both _local_timestamp (HEAD) and _first_is_iso (lane).
- BUGS-demo-2: vectors_lib VOLATILE keeps HEAD's proposed_at/decided_at and lane's session_id/started_at/finished_at. Bridge vector JSON auto-merged (the vector tests pass).
- BUGS-gen-1: temporal.py: lane's _profile check and number checks + HEAD's unknown-key check (#149), exclusive-stop days and _Edges; hour_of_day: HEAD's accumulate ('7'=='07', #134) inside lane's number check. DEMO.md/GENERATION_STRATEGIES.md both texts.
- BUGS-packaging-1: pyproject dev = HEAD's list (setuptools, jsonschema, fastavro, protobuf) + lane's pytest>=9.0.3, defusedxml; databases plugin: HEAD's new extras + lane's pymysql>=1.1.1; build_pure_wheel docstring: lane's (HEAD's AUD-docs docstring described the code the lane replaced).
- BUGS-portability-1: 8 files: HEAD's code with the lane's newline='\n' on each text write (gitcmds incl. HEAD's .gitignore vault write, learn, main verify/profile html, tiers multi-output, charts _new_file, parquet mark_complete); files.local_path uses the lane's schemes.file_uri_path (drive + UNC) with HEAD's nested imports; jobs.py imports stat+sys.
- BUGS-builtins-1: formula _TOO_DEEP joins both messages (HEAD #137 'the expression is nested too deeply' + lane's advice); providers: both refusals (HEAD #140 _is_provider and the lane's Faker-API names); size bound auto-merged.
- HUNT2-cli: main.py: _write_json = lane's (text first, finite check) + newline; diff: lane's --only check (#692) before HEAD's planned-change/semver diff; verify: HEAD's ci timer + lane's context hint a.shape; from-ddl: lane's BOM-aware reader, same-file refusal and empty-script error (replaces HEAD's read_text); check/diff documents: lane's _load_document (also in HEAD's _cmd_diff_documents) + HEAD's ci reports.
- HUNT2-io: file sinks: the lane's replace_atomically (#729/#746 modes, symlink written through, device in place) replaces HEAD's mkstemp (#297, BUGS-builtins-1); HEAD's #297 tests (hardlink replaced, umask mode, no temp left) hold with it. sources/files.py: HEAD's take_options/can_open_suffix + lane's output_path. sql.py: DECISION FOR LEAD: #285 (BUGS-builtins-1: flatten line breaks in names in every dialect, split every T-SQL line break in values) and #724 (HUNT2-io: refuse a T-SQL name with a line break, other dialects keep it; split only values with a GO line; it updated tests/generation/test_writers.py to expect the refusal) contradict. Took HUNT2-io's (refusal instead of a silent rename that can merge two names; fewer script bytes change). BUGS-builtins-1's test_bugs_builtins_sql_injection.py: test_tsql_literal_uses_nchar now uses values with a GO line; test_identifier_line_break_is_flattened became test_identifier_line_break_is_refused (tsql dialects). HEAD's identity/NOTE header kept inside the lane's atomic replace.
- HUNT2-kernels: lib.rs #323: HEAD (BUGS-profile-1) and the lane made the same fix (hooks flag set after set_scalar_hooks); HEAD's kept (identical logic, name HOOKS_INSTALLED). Kernel tests: both sides' new tests kept.
- HUNT2-privacy: registry/local.py: lane's commit (metadata checked before any write), _replace_text for refs and tags, _read_id for resolve, with HEAD's utf-8/LF writes; damaged-ref message carries both lanes' words ('damaged', 'corrupt'); duplicate _CONTENT_ID dropped. safe_validator: HEAD's full-capture rejection + lane's format/version check.
- HUNT2-profile: drift/joint.py rebuilt from HEAD (multi-determinant dependencies of W3-08, safe-capture skipped list W1-11, multivariate) with the lane's per-column scope/thresholds/min_severity (#619) applied to each joint kind; diff_joint(table, bt, ct, policy, skipped, *, scope); engine.py: View.unknown (#618) beside univariate/suppressed; distribution check has both the unknown test and HEAD's not-evaluable path; for_column/skips use tname (lane, single-table table.column). DRIFT.md both. Follow-up 3bfc3948: lane's #595 tests save capture=full (W1-11 safe default drops sketches).
- HUNT2-quality: cli/scorecard.py: lane's --history needs --name + HEAD's --slice-by checks.
- HUNT2-scenario: charts: lane's personal-data withholding + HEAD's integer-share folding and stable _categories order; demo manifest: HEAD's not-an-object check + lane's unknown-field tolerance; run manifest: HEAD's FormatError naming (#519) + lane's _check_types; runner: HEAD dropped _FILE_FORMATS, lane's JSONL_BATCH_ROWS kept; DEMO.md both.
- HUNT2-generation: _packaged.py/retail.py: both made the same deep copy (#343), HEAD's comment kept; schema.py: HEAD's identity checks + lane's max_length check; DRIFT.md ground truth: HEAD's peak_weight text + lane's format key.
- AUD-stream: streaming/cli.py _WindowFile: lane's repair_tail + partial-window rewrite (#153) with HEAD's LF append. Brings INT-18 c45d1f95, 81a7e485 (not yet in INT-19).
- HUNT2-streaming (carries an older lane/AUD-stream): #298 fixed twice (BUGS-stream-1: keyed._inflate, runtime.MAX_STATE_BYTES; HUNT2-streaming: checkpoint.inflate/MAX_STATE_BYTES): one implementation, checkpoint.inflate (bounded, zlib errors -> ValueError), whose messages carry both lanes' words ("corrupt checkpoint", "size limit", "truncated"); keyed._inflate decodes base64 then calls it; _unpack(max_items=None) bounded by checkpoint.MAX_STATE_BYTES, whole-items check kept; runtime keeps its own MAX_STATE_BYTES (tests patch it). emit runtime: lane's staged answer-key commit and ReaderGone (stop "reader-closed") with HEAD's no-retry for FileNotFoundError/PermissionError, lag and send_start. cli/emit.py: answer key opened after the checkpoint is read (lane, append on resume, staged) inside HEAD's drift-plan/dry-run flow; lane's _check_live_options uses HEAD's _LIVE_REPORT_SUFFIXES and creates no folder in a dry run; HEAD's fraction checks kept (lane's duplicate loop dropped); summary line: lane's "the reader closed" + HEAD's dead-letter count. AnswerKey: lane's _waiting + HEAD's LF file.
- AUD-chaos: tier3 psi_report: HEAD's #577 (HUNT2-quality: PSI ignores infinities; a column that gained/lost infinities is drifted) and lane's #404 (fail closed, method error) — a gained/lost infinity or a non-finite PSI is now one 'error' drifted result (both tests' assertions hold); bootstrap n_rows message carries both texts; groundtruth: lane's _option parsing for fuzz too; CHAOS.md both.
- AUD-io: capture/core.py: HEAD's int64 tracking and empty-column summary (#687) + lane's mode-aware _capture_rows and _as_float; excel.py: HEAD's stricter limits (100x, 16 MB) with the lane's whole-archive total check (#563); readers.py: HEAD's empty-source check + lane's existing-name-with-glob-chars; HEAD's identifier-text CSV reads + lane's missing-column ReaderError; both helpers; Parquet: lane's _select_fields/_readable then HEAD's check_parquet budget (footer only).
- AUD-pluginfw (brings the INT-18 tip, 9 commits not in INT-19): run_folder: same fix both sides, HEAD kept; packs/domains load_domain: lane's validation (utf-8 already); profile/workbook: HEAD's reference_pairs (#319) + INT-18's multivariate opt-in (49a1fcd7); bridge make_vectors: lane's lib.environment wrapper with HEAD's SHAPE_HOME sessions; vectors_lib doc both.
- W7-03 (on build/main-plan + lane/W3-07): contracts/v1.py: rule strength (STRENGTHS, _finish split into violations/warnings) on top of HEAD's data rules (timeseries/reconcile, hard), safe-capture not_evaluable (passed only with no failing rule and nothing not evaluable), tables validation and valid_as; check(profile, contract, data=None, *, strict, enforce_learned); a violation of a rule kind without a strength is hard. cli check: HEAD's project/planned/ci flow + --strict/--enforce-learned. drift: both sets of kinds/thresholds. profile: HEAD's sampling, sketches, validators, multivariate + lane's time_column. DECISION (precedent f94de925/49a1fcd7, T-19): mixture and seasonality join the opt-in univariate depth (computed only with univariate=True / --univariate). Docs say so.
- W7-05b: see merge commit: content of W7-05/W1-17/etc already in INT-19 via the squashed d948da9c; conflicts -> INT-19, 4bb987fb applied; duplicate SINKS/THREAT_MODEL sections dropped. W1-17's commits (WIP) enter the history only as ancestors, no content.
- DECISION #541 x W7-05 compat: demo commands run from bridge 1.2; 1.0/1.1 requests get the old refusal (frozen vectors hold); compat 'pending' -> null is a gain; BUGS-demo-2 tests use api12; demo vectors 1.2 + served-as-1.1 case; bridge_1to1 sessions.py sends 1.2 for demo_* (harness: request version only). W7-05b suites: CLI envelope unwrap (W1-14), vectors re-recorded (W6-03 library), BRIDGE.md examples.
- W8-01: healthcare-standards.md intro names both (HEAD's 277CA/X12 writers + lane's companion tables).
- W8-02: CHANGELOG only. W8-04: runner _table_formats uses HEAD's FILE_FORMATS (HEAD moved the map), HEAD's _inside kept.
- G7-eval: fabric_api/spark: lane's on_fabric_api host check (its error types, #631/#434) before HEAD's same_origin/_fabric_url checks; ddl: HEAD's unique constraint names (#642) + lane's usedforsecurity=False (bandit B324); trust: same #579 fix both sides, HEAD kept; DECISION #650 policy.release_for: HUNT2-privacy filters the joint block (k cohorts, hidden columns' values out), G7-eval withholds it whole when a column is classified: the block is withheld whole when every column it covers is above the target, else filtered (both lanes' tests hold).
- merge origin/int/INT-19 (f3ffa7c6): formula: INT-19's MAX_NESTING (#137) + the BUGS-builtins-1 advice in _TOO_DEEP; profile.py: HEAD's time_column with INT-19's check_reference_pairs.
- BUGS-tests-1 (status done, escalation #337 part 2): testing.py same RLock both sides (HEAD's comment); pyproject dev: HEAD's list + coverage>=7.10; [tool.coverage.run] patch subprocess.
- **AUD-tests (d47e75ef) merge 6d8276a8**
  - CHANGELOG: both entries kept.
  - tests/integrations/test_fabric_spark.py: AUD-tests' separate-process run (#328) kept, with HEAD's warehouse dir as a file URI (portability).
  - tests/iss_gaps/test_landing_and_batches.py: both sides fixed the pyarrow-19 hive-partition read; AUD-tests' `ParquetFile(...).read()` kept (#333).
  - tests/kernel/test_hashing.py: AUD-tests' float16 take() path (#333) on HEAD's `null_at` mask name; float16 construction from numpy (both sides).
  - tests/security/test_credential_refs.py: AUD-tests' fresh-interpreter check + its #77 regression test (HEAD had the same idea; one copy kept).
- **W8-03 (453b31f5) merge**
  - CHANGELOG keep both.
  - cli/machine.py NATIVE_DRY_RUN: union (W6-03's canary make, gameday run + W8-03's registry prune); tests/cli/test_ci_flags_coverage.py: both exemptions.
  - registry/local.py: HEAD's validated-JSON line + newline="\n" log append, with W8-03's `_now()` commit time; `tag` keeps HEAD's atomic `_replace_text` inside W8-03's `_writing()` lock.
- **Second merges of moved lanes**
  - AUD-io (976b03d9), AUD-pluginfw (c493dfa5), W8-02 (c3fd4bbd): no conflict (status files; W8-02's CLI test helper, its plugin tests pass, 131).
  - W8-04 (a5a2554f): CHANGELOG only, from the lane's re-merge of INT-18 (its side of every hunk was empty): this branch's text kept.
  - merge origin/int/INT-19 again (f5062835, its lane status only): no conflict.

- **merge origin/int/INT-19 d702c6f8 (ae9105eb)**: the resolutions of decision 8's list, redone:
  `cli/scorecard.py` (HUNT2-cli's `--project FILE|folder`, upward search, `--no-project`,
  `--source`; `load_project`, an invalid file an error; with no chosen source INT-16's
  `ProjectOwners` over every source in name order; the legacy `owners:` fallback dropped) and
  `docs/SCORECARD.md`; `contracts/v1.py` (W7-03's strength checks with INT-19's int-safe
  `_is_finite_number`, one definition) and both new contract tests; `ddl.py`, `changes.py`: this
  branch's; `manifest.py`, `test_reproducibility.py`, `GENERATION_STABILITY.md`,
  `test_w1_15_replay.py`: INT-19's side; shape-databases `test_sinks.py` and CHANGELOG: both.
- **BUGS-721 (6f375e96)**: `tests/integrations/test_fabric_udf_helpers.py` (this branch's #367
  tests and the lane's #721 tests) and CHANGELOG: both.
- **SEC-high (419e3dd2)**: 16 files; decision 9 below.
- **W8-06 (339fea01)**: every conflict is W8-06's `identifiers` next to W1-15's `generators` (and
  W8-04's `writers`), all kept at the time; the vault flow of `cli/generation.py` with the
  identifiers switch. After the hold (decision 8 (b)) only `identifiers` remains.

## Fixes on the merged tree (merge-caused; test first where behaviour changed)

Each commit fixes something that failed only on the merged tree (it passed on the lane and on
`int/INT-19`), or a conflict two lanes left between them; the regression test came first where
behaviour changed (6d55f242 before 9ec0add4). `1c648b1b` fixes a test this integration's own
`a8f0f20b` broke.

| Commit | Fix |
|---|---|
| `6d55f242` | regression test: with constraints disabled, a failed truncate or replace keeps the old rows |
| `3bfc3948` | HUNT2-profile's snapshot-version tests save with capture=full |
| `ac76efe4` | W7-03's no-strength goldens for the five bridge contract fixtures of int/INT-19 |
| `64c52bb7` | W7-03's mixture_change and seasonality_change get a semver class (cosmetic) |
| `9623eb24` | W7-03's mixture and seasonality tests ask for the opt-in univariate depth |
| `3186dae2` | a file:///C:/ URI is the drive path again on every platform |
| `067a4e08` | HUNT2-io's tests follow int/INT-19's #276 host check and #167 header names |
| `9bc29e59` | the demo commands run from bridge api_version 1.2; 1.0 and 1.1 answer as they did |
| `94ea0cf5` | W7-05b's suite commands against int/INT-19's CLI envelope and scenario library |
| `42f1eec5` | the bridge parity harness asks the demo commands as api_version 1.2 |
| `a9ab30b9` | DriftMonitor fails closed on a column that gained or lost infinite values |
| `46fd049f` | HUNT2-streaming's regression tests on int/INT-19's scale check and option messages |
| `9ec0add4` | constraints=disable keeps a truncate or replace in the write's one transaction |
| `d9530e24` | every text write in src names its newline (BUGS-portability-1's #238 scan on the merged tree) |
| `3ceb9e53` | CHANGELOG: the T-SQL line-break entry describes the merged behaviour (#724's) |
| `690e7f5c` | mypy: one annotation of 'gaps' in contracts.v1.check (W7-03 merge) |
| `963f6745` | OWN-429's truncate tape re-recorded with W2-10's key probe; Items API refusal is a ValueError again |
| `02eb612f` | W8-01's companion-table list covers int/INT-19's x12-277ca writer |
| `8b6b0853` | the demo wheel test expects pyproject.toml's core floors (BUGS-packaging-1 #252 on BF-76's floors) |
| `a8f0f20b` | the [pytest] extra's floor is the advisory-free pytest 9.0.3 (BUGS-packaging-1 #267 on W5-05's extra) |
| `0bdcb5a9` | docs/API.md names W7-03's check(strict, enforce_learned) and profile(time_column) |
| `d83dc904` | CLI surface baseline gains W7-03's three flags (scripts/cli_surface.py --write; 0 breaking) |
| `db0c5cb7` | HUNT2-cli's #666 tests read pack list's W1-14 result envelope |
| `c5efcf9e` | BUGS-portability-1's cp1252 cat test profiles with full capture (W1-11's safe default withholds the names) |
| `4e773519` | HUNT2-scenario's #716 repeated-header test follows #167 (the CSV reader names the second column a.1) |
| `ebe5550f` | the docs site nav lists the 17 pages the merged lanes added (P8-03's nav check) |
| `d1974b94` | W8-04's float-digit test declares no key on its keyless table (HUNT2-io refuses a primary_key that is not a column) |
| `205b203f` | the project schema takes W7-03's mixture_weight / seasonality_strength thresholds and mixture_change / seasonality_change kinds |
| `842bb549` | W3-07's opt-in test strips every univariate field, W7-03's mixture and seasonality included |
| `c0ec9300` | docs follow P8-03's site on the release doc checks (docs extra lock set, docs/plans/ path, home links the CLI page in the repository) |
| `7fea0b72` | the Spark lane's off-API URL refusals are ServiceUrlError again (G7-eval's host check on #275's ValueError contract) |
| `78b72c82` | AUD-scenario's #281/#514 tests define the scale they run (HUNT2-generation #717 refuses an undefined scale) |
| `1f2eeb49` | W8-03's lock and prune writes name their newline (BUGS-portability-1's #238 scan) |
| `5a0de101` | AUD-tests' census-loader test expects AUD-pluginfw's #380 refusal of an unknown kind |
| `1c648b1b` | W5-05's pytest-extra test follows the advisory-free floor pytest>=9.0.3 |
| `cab2d012` | the in-process mypy test restores the recursion limit mypy raises (HUNT2-generation's #654 depth test after it) |
| `65c629c0` | the Fabric drift report gives W7-03's `mixture_change` and `seasonality_change` their thresholds (`test_every_kind_the_engine_reports_has_a_threshold_rule` failed on the merged tree) |
| `db2ffccb` | decision 7: the demo parity harness names #701 (`dry-run-judges-files`), tests first |
| `6b51e594`, `134b3c1c`, `535e9274` | decision 8 (b): W8-04 and W1-15 held again (and the two commits built on them) |

## Decisions for the lead

### The lead's answers of 2026-10-05 (recorded; applied in this round)

1. **#724 kept** (a T-SQL name with a line break is refused; a value is split only at a `GO` line).
   Accepted as merged. It also decides the SQL sink part of SEC-high's #724/#285, see 9 (a).
2. **W7-03's mixture and seasonality are opt-in** with the univariate depth. Accepted.
3. **#541**: the demo commands run from bridge 1.2, 1.0/1.1 get the old answer. Accepted.
4. **#650**: the joint-block merge rule (withheld whole when every column it covers is above the
   target, else filtered). Accepted; SEC-high's small-cell suppression now runs after it, see 9 (d).
5. **#167 kept** (unique names for a repeated CSV header). Accepted.
6. **Dependency floors vs T-07: escalated to the owner, nothing changed here.** BF-76's floors in
   `pyproject.toml` (`numpy>=2.3,<3`, `pyarrow>=19.0.1`) differ from T-07's text (`numpy>=2.0,<3`,
   `pyarrow>=14.0.1`). T-07 is a fixed decision: T-07, §2 and the floors are not edited in this
   integration. The lead takes the difference to the owner, together with the question whether
   `numpy>=2.3` is installable on the Fabric runtime (BF-76 status, "Consequences for the lead").
7. **#701 registered, not reverted**: the deliberate difference `dry-run-judges-files` in
   `benchmarks/vs_refengine/demo_1to1/differences.py` (owner's 2026-10-01 policy, the pattern of
   INT-18's `rows-are-what-runs`): `gone_files_left_alone` maps only the baseline's exact
   `  [dry-run] Would remove: file/NAME` line of a local file that is not there (exactly once,
   else `ValueError`) to Shape's `  Left alone: file/NAME (already gone)`, after the lines of what
   would be removed; the probe `probe_cleanup_dry_run_files` checks both sides (the baseline lists a
   gone file; Shape's dry run leaves it alone, lists and keeps a file that is there, and its real
   cleanup leaves the gone file alone too); the negative control tampers three ways. Tests first in
   `tests/benchmarks/test_demo_parity_differences.py` (7 failed before). Commit db2ffccb.
8. **W1-15 x W8-04: (a) was tried and failed its proof, so (b).** Merged `origin/int/INT-19`
   d702c6f8 (ae9105eb) and re-applied W1-15 (031fde56, the revert of INT-18's bea22e4a). The proof
   on that tree, in this container (an AVX-512 CPU), both kernels, 405 tests per run of
   `tests/generation/test_w1_15_pinned_fixtures.py`, `tests/generation/test_w8_04_byte_identity.py`
   and `tests/scenario/test_w1_15_replay.py`:

   | Run | rust | python |
   |---|---|---|
   | as is | 405 passed | 405 passed |
   | `NPY_DISABLE_CPU_FEATURES="X86_V4"` (numpy reports X86_V4 and AVX512_* not found) | **33 failed**, 372 passed | **33 failed**, 372 passed |

   The 33: the pinned dataset id of the seven continuous distribution families (`beta`,
   `exponential`, `gamma`, `log_normal`, `pareto`, `power_law_cutoff`, `weibull`; two tests each,
   28) and W8-04's golden byte corpus (5 tests). The same in both kernels, because those families
   are drawn through numpy's transcendental functions, whose last bits differ between the AVX-512
   and the AVX2 code paths. So the cross-platform promise does not hold and CI was **not**
   dispatched for (a) (a failing local proof already decides it). **#768 closed as (b):** W8-04 is
   held too: its merges 55487125 and cc4fdbf8 reverted (134b3c1c, with this branch's d1974b94 and
   the `sql` writer's `format_version`), and the W1-15 re-apply reverted (535e9274); first the two
   commits that built on them (6b51e594: W8-06's faker generator version 2, b72c69d5, and the
   golden corpus rewrite, 87d706c3). **Both W1-15 and W8-04 are held for a follow-up lane that
   makes the distribution generators bit-identical across CPUs** (for example by computing the
   transcendental functions of the distribution families in the kernel, not through numpy's
   CPU-dispatched loops). When they come back: W8-06's faker change becomes `faker` generator
   version 2 again (b72c69d5 has the code, the pinned fixture, the `RAISED` map and the tests), and
   SEC-high's PostgreSQL `E''` literal changes one SQL file of the golden corpus
   (`golden-types/values.postgres.sql`, line 54), so `sql` goes to `format_version` 2 with the corpus
   rewritten (87d706c3).

### New in this round

9. **SEC-high (#724/#285, #275, #535, #533, #650, #276, #411, #629, #663, #395) x fixes of the same
   issues already on this branch.** Most of SEC-high's findings were also fixed by lanes merged
   earlier (HUNT2-io, BUGS-builtins-1, AUD-bridge, AUD-security2 in INT-19, G7-eval,
   HUNT2-privacy, HUNT2-scenario). Every resolution keeps both sides' tests except where noted:
   - (a) **#724/#285, the SQL sink.** SEC-high refuses a name with any control character in every
     dialect and writes every T-SQL line break as `NCHAR(10)`; decision 1 refuses a T-SQL name with
     a line break, lets the other dialects quote it, and splits a value only at a `GO` line. The
     sink follows decision 1, but a `GO`-line value now goes through SEC-high's
     `sqltext.tsql_text`, which adds its `CAST(N'' AS NVARCHAR(MAX))` prefix for a value over
     8,000 bytes (a truncation bug of the old splitter); SEC-high's PostgreSQL `E''` literal is
     kept. **Three SEC-high tests follow decision 1** (they lose no assertion about behaviour the
     tree has): `test_724_carriage_returns_are_kept_as_characters` and
     `test_724_a_long_value_with_a_line_break_is_not_truncated_by_the_concatenation` use values
     with a `GO` line (and assert a value without one keeps its bytes);
     `test_724_a_name_with_a_control_character_is_refused` expects the refusal for the two T-SQL
     dialects and the quoted name for postgres and mysql. `tests/generation/test_writers.py`'s
     name test (auto-merged into an inconsistent mix of both lanes) is HUNT2-io's again. The
     contract and design DDL keep SEC-high's `sqltext` as is (refuse a control character in a
     name; split every T-SQL line break), since decision 1 was about the sink. **If the lead
     prefers SEC-high's stricter rule for the sink:** use `sqltext.check_identifier` and
     `tsql_text` in `sinks/sql.py`; then HUNT2-io's three `tests/io/test_hunt2_io.py` T-SQL tests
     and decision 1 change, and with W8-04 back the `sql` writer's bytes change for any value with
     a line break.
   - (b) **#535, bridge `verify`.** AUD-bridge (in INT-19) withheld the range gate's actual extreme
     of every column unless `include_raw_values`; SEC-high, per the lead's 2026-10-05 decision,
     only of a classified column (an unclassified one keeps the frozen 1.1 text). The merged tree
     follows the lead's decision (SEC-high's `redact_gate_messages`), in AUD-bridge's form: the
     extreme reads `(actual max withheld)` and the gate says `"redacted": true`. Both lanes' tests
     hold (AUD-bridge's test column is classified). `docs/BRIDGE.md` states the decision.
   - (c) **#275.** Both fixed it: this branch with an unredirected `Authorization` header and a
     check that the answer came from the same origin, SEC-high with a redirect handler that never
     follows another origin. Both are on (`_OPENER` with `SameOriginRedirect`, then
     `redirected_away`), in the Fabric/OneLake transport and the shape-fabric Kusto transport. The
     transport tests of this branch patch `_OPENER` instead of `urllib.request.urlopen`, as
     SEC-high's own tests do (`tests/scale/test_http_redirects.py`,
     `plugins/shape-fabric/tests/security/test_redirects.py`,
     `plugins/shape-fabric/tests/test_uncovered_paths.py`; assertions unchanged).
   - (d) **#650.** Decision 4's rule first (withheld whole when every covered column is above the
     target, else this branch's filter of hidden columns and cells below k), then SEC-high's
     `suppress_joint` on what is left (small values fold into `__OTHER__`, unchecked entries
     withheld); the filter now lists the cells it drops in `removed` (SEC-high's test asks for it).
   - (e) the rest are the same fix twice: #533 (both label parsers), #276 (SEC-high's
     `check_storage_host`, also at parse time as this branch did), #411 (this branch's value
     patterns with SEC-high's escaped single quotes, and SEC-high's refusal to load a tape holding
     a secret), #629/#663/#395 (this branch's code, SEC-high's max of pattern rates).
10. **Lanes for INT-21.** BUGS-demo-3 (1a5ee8c3) and P6-01-fin (83123a31): their status files do
    not say the work is complete (BUGS-demo-3 reports its fixes and checks but no completion line;
    P6-01-fin's table says P6-01a "No."), so they are not merged.
    *(Round 3: BUGS-demo-3 merged on the lead's instruction, `b79f9f11`; P6-01-fin still not.)*

### Recorded before the answers (unchanged)

1. **#285 vs #724 (T-SQL names and values with line breaks).** BUGS-builtins-1 (#285) flattens a
   line break in a name in every dialect and splits every T-SQL line break in values; HUNT2-io
   (#724) refuses a T-SQL name with a line break (other dialects keep it) and splits only values
   that hold a `GO` line. They contradict; INT-20 keeps #724 (a refusal instead of a silent rename
   that can merge two names). BUGS-builtins-1's tests now assert the refusal
   (`test_identifier_line_break_is_refused`) and use a `GO` line in the NCHAR test. CHANGELOG
   describes #724's behaviour (3ceb9e53).
2. **W7-03's mixture and seasonality are opt-in** with the rest of the univariate depth
   (`univariate=True` / `--univariate`), following the INT-18 precedent for W3-07 and W3-08
   (f94de925, 49a1fcd7; T-19 profile cost). On the lane they were computed on every profile.
   `time_column` only matters with `univariate=True` (docs/API.md says so).
3. **#541 (bridge demo commands) x W7-05 (frozen bridge 1.0/1.1 vectors).** The demo commands run
   from bridge `api_version` 1.2; a 1.0 or 1.1 request gets the answer it got before, so the
   frozen vectors hold. The parity harness (`benchmarks/vs_refengine/bridge_1to1/sessions.py`) sends
   1.2 for `demo_*` (request version only; nothing compared changed).
4. **#650 `release_for` and the joint block.** HUNT2-privacy filters the joint block (k cohorts,
   hidden columns' values out); G7-eval withholds it whole when a column is classified. Merged:
   withheld whole when every column it covers is above the target, else filtered. Both lanes'
   tests hold.
5. **#167 vs HUNT2-io #734 and HUNT2-scenario #716 (repeated CSV header).** `int/INT-19` makes a
   repeated header name unique (`a`, `a.1`, #167). The two lanes' tests expected a refusal (written
   on a base where pyarrow refused). The merged tree keeps #167; the #734 profile test and the
   #716 demo test now assert the #167 names / a run without traceback (067a4e08, 4e773519). If a
   refusal is wanted instead, #167 is the decision to revisit.
6. **Demo wheel floors (T-07 text vs `pyproject.toml`).** BUGS-packaging-1 (#252) derives the pure
   wheel's metadata from `pyproject.toml`, which on `int/INT-19` already has BF-76's floors
   (`numpy>=2.3,<3`, `pyarrow>=19.0.1`); T-07's text still says `numpy>=2.0,<3`,
   `pyarrow>=14.0.1`. The wheel now matches the platform wheels (T-29: one metadata per version);
   the demo wheel test asserts the pyproject floors (8b6b0853). §2.3/T-07 should record BF-76's
   floors, and whether `numpy>=2.3` is installable on the Fabric runtime needs checking
   (BF-76 status, "Consequences for the lead").
7. **#701 and the demo equivalence verifier.** HUNT2-scenario's #701 makes `shape demo cleanup
   --dry-run` judge local files by the real cleanup's rules, so a local file that is already gone is
   "Left alone: file/a (already gone)" instead of "Would remove". The demo verifier's
   `cleanup --dry-run lines` check compares those lines with the baseline byte for byte and now
   fails (it passed on `int/INT-19`). Not changed here: either the difference is registered in
   `benchmarks/vs_refengine/demo_1to1/differences.py` with a probe (a verifier change, the lead's) or
   #701's dry-run change for missing files is reverted. The demo verifier fails on `int/INT-19`
   already (five other checks), see "Checks".

8. **BLOCKER: W1-15 is held (INT-18 bea22e4a, lead decision 2026-10-05, #768), but W8-04 is
   built on W1-15.** `origin/int/INT-19` moved to d702c6f8 (it merged INT-18 up to 6594c830,
   which reverts W1-15). Merging it into INT-20 removes W1-15, and W8-04 (merged here, 55487125)
   depends on W1-15 in three places:
   - its golden byte corpus (`scripts/golden_bytes.py`, `tests/generation/golden_bytes/hashes.json`)
     is generated from W1-15's pinned specs (`tests/generation/pinned/*.json`, which carry W1-15's
     `generators` pins) through W1-15's `tests/generation/pinned_support.py`;
   - its documentation is a section of W1-15's `docs/GENERATION_STABILITY.md`;
   - it edits W1-15's `tests/scenario/test_w1_15_replay.py`.

   With the merge, 11 tests of `tests/generation/test_w8_04_byte_identity.py` fail (the corpus
   tests, the coverage test and the update/report tests; `golden_bytes.py` stops with
   `No module named 'pinned_support'`). Making them pass needs one of three choices, all the
   lead's: (a) land W1-15 with W8-04 (undo the hold for INT-20); (b) hold W8-04 too (revert its
   merges 55487125 and cc4fdbf8 in INT-20); (c) re-cut W8-04's corpus without the pinned specs
   (changes its golden hashes and narrows "the corpus covers every strategy"). **So the merge of
   d702c6f8 was not committed** (aborted, nothing pushed); INT-20 stays at INT-19 f5062835.
   The other conflicts of that merge are resolved and kept ready (in the session scratchpad;
   summarised here so they can be redone):
   - `src/shape/cli/scorecard.py` × INT-16 131513b3: both fixed the scorecard's owners (#611,
     W3-06 × W1-04). Merged as: HUNT2-cli's `--project FILE|folder`, upward search, `--no-project`
     and `--source`; the project read with `load_project` (an invalid file is an error, INT-16's
     test); with no chosen source, INT-16's `ProjectOwners` lookup over every source in name order.
     HUNT2-cli's unread legacy `owners:` fallback is dropped (INT-16 refuses such a file). Without
     this, `shape scorecard` fails on `ImportError: PROJECT_FILE` (merge-caused). Tests:
     `tests/quality`, `tests/regressions/test_hunt2_cli.py`, `tests/project` 979 passed.
   - `docs/SCORECARD.md`: one paragraph with both behaviours.
   - `src/shape/scenario/manifest.py`, `tests/scenario/test_reproducibility.py`: `generators`
     (W1-15) out, `writers` (W8-04) kept: only valid with choice (a) or (c).
   - `src/shape/contracts/emit/ddl.py`, `src/shape/project/changes.py`: this branch's code (it holds
     INT-18's `usedforsecurity=False` and `nosec B506` already, plus #642's unique names).
   - `plugins/shape-databases/tests/test_sinks.py`: both new tests kept; CHANGELOG both.

## Checks on 535e9274 (2026-10-05, after the answers)

Every command ran in this session, in a fresh container (venv `.[dev,streaming,advanced]` +
shape-domains, shape-eventhubs, shape-sqlserver, shape-fabric editable; the pinned baseline from
`setup_refengine.sh`, 422e78d, only read). Full suites are left to the lead's 7 shards; here only
what the instruction lists.

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (T-27 paths) | clean / 1986 files formatted |
| `mypy` | no issues in 684 source files |
| `bandit -r src -ll` | exit 0, no issues |
| `scripts/check_*.py` (conformance coverage, doc links, plugin skeletons, requirements, secrets, shipped data, user facing, v1 done) | exit 0 each; `check_user_facing: clean` (D-13) |
| `gen_exit_codes`, `gen_failure_modes`, `gen_plugin_api_docs`, `gen_unicode_case_table` `--check`, `cli_surface.py --check` | exit 0 each |
| `vulture`, `lint-imports`, `compileall` | clean; 1 contract kept |
| `.github` vs `origin/int/INT-19` | no difference (the three new lanes need no workflow change) |

Tests of the directories this round's merges and fixes touched (`tests/generation tests/scenario
tests/bridge tests/cli tests/api tests/security tests/scale tests/privacy tests/demo_cmd
tests/contracts tests/io tests/benchmarks tests/integrations tests/quality tests/project
plugins/shape-fabric/tests`, `-m "not emulator and not live"`):

| Kernel | Result |
|---|---|
| `SHAPE_KERNEL=rust` (default) | 3 failed, 9559 passed, 20 skipped |
| `SHAPE_KERNEL=python` | 3 failed, 9559 passed, 20 skipped (the same three) |

The three: `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output`
(INT-19's escalated E2, as before); `tests/cli/test_dry_run.py::test_a_sink_is_a_send_with_the_secret_redacted`
(needs shape-kafka: passes with it installed, checked) and
`plugins/shape-fabric/tests/test_sql_write_path_cli.py::test_upsert_is_a_write_mode_choice_and_other_databases_refuse_it`
(needs shape-databases: passes with it installed, checked). Decision 8's proof runs are in the
decision's table.

Equivalence verifiers (no timing in this round):

| Verifier | Result |
|---|---|
| `profile_1to1/verify.py --impl shape` (datasets from `datasets.py`) | exit 0, every dataset PASS (49) |
| `safe_profile_1to1/verify.py` | exit 0, PARITY OK (the 120 `validators` mismatches are gone with INT-18 69f6ffef, now merged) |
| `domain_1to1/verify.py --domain retail --scale small --impl shape` | exit 0, VERDICT PASS |
| `bridge_1to1/verify.py` | exit 0, 56/56 checks, 17/17 commands |
| `demo_1to1/verify.py --negative-control` | exit 0, VERDICT PASS (every check, including `cleanup --dry-run lines` and the new probe; the negative control flagged every tampering) |

The earlier rounds' results below are for `ce1578aa` and are kept as they were.

## Checks on the merged tree

All on the merged head `ce1578aa` (the tests, the static checks) unless named; the baseline is
`origin/int/INT-19` (f3ffa7c6; f5062835 adds only its status file) in the same container, the same
venv layout and the same kernel build. Every command ran in this session.

### Static checks (`make check`'s steps, T-27 scope)

| Check | Merged | Note |
|---|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | clean | |
| `ruff format --check` (same paths) | clean, 1986 files | |
| `mypy` | 1 error | `src/shape/importers/pydantic_models.py:95` pydantic `import-not-found`: pydantic is not installed in this venv (it is optional); identical on INT-19 |
| `compileall`, `vulture`, `lint-imports` | clean (1 contract kept) | |
| `check_requirements`, `check_secrets`, `check_user_facing`, `check_shipped_data`, `check_plugin_skeletons`, `check_conformance_coverage`, `check_v1_done`, `check_doc_links` | exit 0 | `check_user_facing: clean` (D-13) |
| `gen_exit_codes`, `gen_failure_modes`, `gen_plugin_api_docs`, `gen_unicode_case_table` `--check` | exit 0 | |
| `scripts/cli_surface.py --check` | 0 breaking, 0 notes | the baseline JSON was rewritten with `--write` for W7-03's three new flags (additions only) |
| `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test` | clean; 34 passed | no Rust change since HUNT2-kernels; the extension was rebuilt with `maturin develop --release` |
| `mkdocs build --strict` (the `docs` extra's pins, in a scratch venv) | exit 0 | P8-03's site |

### Test suites: `pytest -m "not emulator and not live"`

Run like the CI `test` job (`--ignore=tests/demo/fabric --ignore=tests/demo/content`, which the
`fabric-demo` job runs), each in its own worktree with a private `TMPDIR`. The venv has core
`.[dev,streaming,advanced]` and `plugins/shape-domains`, as `make bootstrap` installs.

| Suite | int/INT-19 | INT-20 (merged) |
|---|---|---|
| `SHAPE_KERNEL=rust` | 7 failed, 15812 passed, 1 error | **6 failed, 17472 passed, 1 error** |
| `SHAPE_KERNEL=python` (two halves: `tests/` entries 1-40 and 41-82) | see below | **6 failed, 17472 passed, 1 error** (4 + 2 failed, 8934 + 8538 passed) |
| plugin suites, `plugins/*/tests` (core + every plugin installed editable) | 51 failed, 2818 passed, 27 errors | **51 failed, 3068 passed, 27 errors**, the same node ids |

Every failure of the merged tree also fails on INT-19; none is merge-caused:

- Environment only (they pass with the plugins installed, checked): the collection error of
  `tests/scale/test_kql_sink_uri.py` (`No module named 'shape_fabric'`),
  `tests/cli/test_dry_run.py::test_a_sink_is_a_send_with_the_secret_redacted`,
  `tests/demo_cmd/test_audit.py::test_521_an_existing_file_is_not_overwritten[formats1-retail_model.bim]`
  and the two `tests/demo_cmd/test_notebook_and_outputs.py` semantic-model tests. AUD-tests' #331
  workflow diff installs the plugin in CI.
- Real, escalated by INT-19 (its E1 and E2, contradictions between two lanes' tests):
  `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb` and
  `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output`.
- INT-19's seventh, `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  (heavy), passes on the merged tree in both kernels.

The python-kernel suite was not run whole on INT-19 here; each merged failure above was checked
on INT-19 (the rust run, same node ids, and the plugin rerun), and the python-kernel set equals
the rust set.

Plugin failures (identical on both trees): `shape-fabric` `test_publish_report.py` (31,
`publish-report` exits 2), `shape-dbt` (25: `_ParsedColumn` has no `raw_type`, `from-dbt` exits 1),
`shape-databases` `test_cli.py` (10: non-local target needs `--yes`, as AUD-tests and BUGS-tests-1
noted), `shape-kafka` (5), `shape-fabric` `test_synapse.py` (3) and five single tests.

**The first full merged run** (at d39788f3, before the fixes) had 36 failed; 30 were merge-caused
and are fixed by the commits listed under "Fixes"; the rest are the INT-19 failures above.

**The lanes' "pre-existing" counts.** AUD-stream (150), BUGS-tests-1 (136) and W8-04 (167,
`W8-04-base-failures.txt`) counted failures on `int/INT-18` at the time in their own containers.
On INT-19 here, 3 of W8-04's 167 node ids fail (the three environment-only tests above) and the
rest pass; INT-19 as a whole fails 7 tests, so none of the larger sets reproduces on INT-19 or on
the merged tree. AUD-stream's and BUGS-tests-1's sets are described, not listed; their named
groups (`shape-result` envelopes, `history --coarse`, the `shape.behaviors` scaffold, `--yes`
confirmation) pass here except the plugin tests above.

### Equivalence verifiers (before any timing)

Merged tree with the merged venvs; INT-19 with its own (same pinned baseline 422e78d, same data).

| Verifier | INT-19 | Merged | Note |
|---|---|---|---|
| writers, chaos, ddl, domain_shape, transform, stream, pack, scale, incremental, db | not rerun (these passed on the first merged head and on the lanes) | PASS (exit 0 each) | |
| mask, verify (20/20 scenarios agree), learn (D2 and MT: 0 unexplained) | PASS | PASS | data from `profile_1to1/datasets.py D2 MT` and `domain_1to1/generate.py` |
| profile, generate, stream (inside `run.py --quick`) | PASS | PASS | every workload, before timing |
| safe_profile | FAIL, 120 mismatches | FAIL, the same 120 | every one is the `validators` key of W3-12 on one side only; INT-18's 69f6ffef ("safe-profile parity checks W3-12's column validators on both sides") fixes the harness and is not in INT-19 yet |
| fabric_commands | FAIL: `publish: landing zone, manifest and report ...` | FAIL: the same check | the merged tree adds OWN-425's probe `repeated measure names are qualified` (PASS) |
| bridge | FAIL 50/51: `list: every Shape domain is a baseline domain` | FAIL 53/54: the same check | three more checks hold (W7-05b's suite commands) |
| demo | FAIL: catalog, list, cost estimate, notebooks, probe domain ignored | FAIL: those five **and `cleanup --dry-run lines`** | the sixth is HUNT2-scenario's #701, decision 7 |

### Benchmarks: `benchmarks/vs_refengine/run.py --quick` (verifiers first, then timing)

Each run on a quiet machine (load 1.01 merged, 0.88 INT-19, no other job), `--out` to the scratchpad
(the committed results are not changed); both exit 0. Times in seconds as `run.py` reports them (`--quick`: 3 runs).

| Impl | Workload | Verifier (INT-19 / merged) | Baseline s (INT-19 run / merged run) | Shape s INT-19 | Shape s merged | Speedup INT-19 | Speedup merged |
|---|---|---|---|---|---|---|---|
| reference_port | `profile:d1.csv` | pass / pass | 1.47 / 1.53 | 0.33 | 0.42 | 4.43x | 3.62x |
| reference_port | `profile:d1.parquet` | pass / pass | 1.35 / 1.45 | 0.37 | 0.33 | 3.68x | 4.4x |
| reference_port | `profile:d2.csv` | pass / pass | 29.22 / 28.73 | 2.8 | 2.69 | 10.45x | 10.69x |
| reference_port | `profile:d2.parquet` | pass / pass | 24.62 / 24.23 | 2.18 | 2.16 | 11.3x | 11.2x |
| reference_port | `generate:retail:small` | pass / pass | 0.25 / 0.25 | 0.14 | 0.11 | 1.84x | 2.22x |
| reference_port | `generate:retail:medium` | pass / pass | 5.02 / 4.88 | 0.95 | 0.95 | 5.27x | 5.13x |
| shape | `profile:d1.csv` | pass / pass | 1.5 / 1.44 | 0.24 | 0.25 | 6.14x | 5.86x |
| shape | `profile:d1.parquet` | pass / pass | 1.38 / 1.34 | 0.12 | 0.11 | 11.47x | 11.88x |
| shape | `profile:d2.csv` | pass / pass | 29.53 / 29.43 | 2.14 | 2.17 | 13.8x | 13.59x |
| shape | `profile:d2.parquet` | pass / pass | 25.35 / 25.48 | 1.31 | 1.29 | 19.38x | 19.73x |
| shape | `stream:retail:order:small` | pass / pass | 0.27 / 0.27 | 0.13 | 0.12 | 2.1x | 2.2x |
| shape | `stream:retail:order:medium` | pass / pass | 14.08 / 13.5 | 0.9 | 0.89 | 15.65x | 15.19x |

The `shape` rows of the merged tree are within 0.03 s of INT-19's (the reference-port rows within
0.09 s), which is run-to-run noise at this size; the speedups do not move in either direction
beyond it. Neither run has a
`shape generate` row: the quick set times generation through the reference port only, on both trees.

## Workflow diffs from the merged lanes (not applied)

Four merged lanes need a workflow change; each diff is copied verbatim from the lane's status file.
The other merged lanes state that no workflow change is needed.

### AUD-tests (from `docs/plans/lane_status/AUD-tests.md`)

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ jobs: test: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      # #331: the demo semantic-model tests (tests/demo_cmd) need the shape-fabric plugin.
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
@@ jobs: zero-network: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
+      # #331/#332: DuckDB's delta extension is a download; fetch it (as the user the tests run as)
+      # before the network is removed, so the delta-fallback tests read it from the local cache.
+      - name: Pre-fetch DuckDB's delta extension
+        run: sudo env "PATH=$PATH" python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL delta'); c.execute('LOAD delta')"
       - name: Prove the namespace has no route out
@@ jobs: database-plugins: steps
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev]' -e plugins/shape-databases
-      - run: python -m pytest -q -m "not emulator and not live" plugins/shape-databases/tests
+      - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-databases
+      # #334: the two tests in tests/ that need shape-databases run here, the only job with it
+      # (they generate from an installed domain, hence shape-domains).
+      - run: python -m pytest -q -m "not emulator and not live" plugins/shape-databases/tests tests/cli/test_generate_to.py tests/streaming/emit/test_table_sink.py
       - run: python -m shape.plugins.kit sqllocks-shape-databases
@@ jobs: fabric-demo: steps
       - run: pip install -e '.[dev]' -r tests/demo/fabric/requirements.txt
-      - run: python -m pytest -q tests/demo/fabric tests/demo/content
+      # #757: the #335 regression test in tests/integrations needs delta-spark, installed only here.
+      - run: python -m pytest -q tests/demo/fabric tests/demo/content tests/integrations/test_fabric_spark.py
```

### P8-03 (from `docs/plans/lane_status/P8-03.md`)

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -253,6 +253,19 @@ jobs:
           path: |
             ${{ github.workspace }}/bench-out/results.json
             ${{ github.workspace }}/bench-out/verify/
+  docs:
+    # P8-03 (T-24): the documentation site builds with no warning and every link resolves.
+    # The hooks read the parser from src/, so only the docs extra's pins are needed.
+    runs-on: ubuntu-latest
+    steps:
+      - uses: actions/checkout@v4
+      - uses: actions/setup-python@v5
+        with: {python-version: '3.13'}
+      - run: python -m pip install -U pip
+      - run: pip install 'mkdocs>=1.6,<2' 'mkdocs-material>=9.5,<10'
+      - run: mkdocs build --strict
+      - run: python scripts/check_doc_links.py --site site
+      - run: python scripts/check_user_facing.py --site site
   build:
     runs-on: ubuntu-latest
     steps:
```

### PF-05-fin (from `docs/plans/lane_status/PF-05-fin.md`)

```diff
--- a/.github/workflows/container.yml
+++ b/.github/workflows/container.yml
@@ -4,9 +4,9 @@
 on:
   push:
     tags: ['v*']
-    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'rust/**', 'src/**', 'pyproject.toml']
+    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'scripts/image_size.py', 'rust/**', 'src/**', 'pyproject.toml']
   pull_request:
-    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'rust/**', 'src/**', 'pyproject.toml']
+    paths: ['Dockerfile', '.dockerignore', '.github/workflows/container.yml', 'scripts/image_size.py', 'rust/**', 'src/**', 'pyproject.toml']
   workflow_dispatch: {}
 permissions: {contents: read}
 env: {IMAGE: ghcr.io/sqllocks/shape}
@@ -18,9 +18,10 @@
       - uses: docker/setup-buildx-action@v3
       - uses: docker/build-push-action@v6
         with: {context: ., load: true, tags: 'shape:ci', cache-from: 'type=gha', cache-to: 'type=gha,mode=max'}
-      - name: Image size is under 500 MB
+      - name: Image size is under 500 MB (uncompressed layers, whatever the image store)
+        # `docker image inspect .Size` is the compressed size under the containerd image store.
         run: |
-          size=$(docker image inspect shape:ci --format '{{.Size}}')
+          size=$(python3 scripts/image_size.py shape:ci)
           echo "image size: $((size / 1000000)) MB"
           test "$size" -lt 500000000
       - name: Runs as a non-root user with the [azure] packages and the Rust kernel
@@ -39,8 +40,8 @@
           mkdir -p "$RUNNER_TEMP/out" && chmod 777 "$RUNNER_TEMP/out"
           for f in d1.parquet d1.csv; do
             docker run --rm -v "$BENCH_DATA_DIR/profile:/data:ro" -v "$RUNNER_TEMP/out:/work" \
-              shape:ci shape profile "/data/$f" -o "/work/${f%.*}.shape" --json "/work/${f%.*}.json"
-            test -s "$RUNNER_TEMP/out/${f%.*}.shape"
+              shape:ci shape profile "/data/$f" -o "/work/${f/./_}.shape" --json "/work/${f/./_}.json"
+            test -s "$RUNNER_TEMP/out/${f/./_}.shape" && test -s "$RUNNER_TEMP/out/${f/./_}.json"
           done
   publish:
     # Only for a version tag; the owner creates tags (plan section 9).
```

### W7-01 (from `docs/plans/lane_status/W7-01.md`)

```diff
@@ jobs: (after abfss-live, before sqlserver-e2e)
+  fabric-git-sync-live:
+    # W7-01: a shape/ folder of .shape files next to Fabric items survives Fabric Git integration in
+    # both directions. Runs only where the owner's O-02 secrets and the Git remote exist; uploads the
+    # shape-live-check JSON result (no secret, tenant or workspace id).
+    runs-on: ubuntu-latest
+    timeout-minutes: 45
+    env:
+      HAVE_GIT_SYNC: ${{ secrets.FABRIC_CLIENT_ID != '' && secrets.FABRIC_WORKSPACE_ID != '' && secrets.FABRIC_GIT_REMOTE != '' }}
+      FABRIC_TENANT_ID: ${{ secrets.FABRIC_TENANT_ID }}
+      FABRIC_CLIENT_ID: ${{ secrets.FABRIC_CLIENT_ID }}
+      FABRIC_CLIENT_SECRET: ${{ secrets.FABRIC_CLIENT_SECRET }}
+      FABRIC_WORKSPACE_ID: ${{ secrets.FABRIC_WORKSPACE_ID }}
+      FABRIC_GIT_REMOTE: ${{ secrets.FABRIC_GIT_REMOTE }}
+      FABRIC_GIT_TOKEN: ${{ secrets.FABRIC_GIT_TOKEN }}
+      SHAPE_LIVE_RESULT: ${{ github.workspace }}/git-sync-result.json
+    steps:
+      - if: env.HAVE_GIT_SYNC != 'true'
+        run: echo "no Fabric Git sync secrets configured; nothing to run"
+      - if: env.HAVE_GIT_SYNC == 'true'
+        uses: actions/checkout@v4
+      - if: env.HAVE_GIT_SYNC == 'true'
+        uses: actions/setup-python@v5
+        with: {python-version: '3.11'}
+      - if: env.HAVE_GIT_SYNC == 'true'
+        run: pip install -e '.[dev]' -e 'plugins/shape-fabric[entra]'
+      - if: env.HAVE_GIT_SYNC == 'true'
+        run: python -m pytest -m live plugins/shape-fabric/tests/test_live_git_sync.py -q
+      - if: always() && env.HAVE_GIT_SYNC == 'true'
+        uses: actions/upload-artifact@v4
+        with:
+          name: fabric-git-sync-result
+          path: git-sync-result.json
+          if-no-files-found: ignore
```

## Open items

- **W7-01 item 1:** the live Git sync test (`plugins/shape-fabric/tests/test_live_git_sync.py`,
  marker `live`) is built but has not run live: it needs the O-02 owner secrets. Its nightly job is
  the workflow diff above. #139 stays open until a passing run is recorded.
- **PF-05-fin:** the `container.yml` workflow has never run a job; apply the diff above and
  dispatch it on the integration branch to record the CI image build and its size check.
- **AUD-tests:** #331, #332 (downloads), #334 and #757 need the workflow diff above.
- **P8-03:** publishing the site (Pages, deploy workflow) is not part of the package; the `docs`
  job is the diff above. `mkdocs build --strict` passes on the merged tree (run here with the
  `docs` extra's pins).
- **For INT-21:** P6-01-fin (not complete by its status file; BUGS-demo-3 was merged in round 3), and W1-15 with
  W8-04 once a lane makes the distribution generators bit-identical across CPUs (decision 8).
- **Escalated by the lead to the owner:** T-07's floors vs BF-76's (decision 6).
- **Decision 9** (SEC-high's sink rule vs decision 1, #535's form) for the lead to confirm.
- The `INT-19` items that waited on INT-18 (mypy's pydantic error, the safe_profile mismatches)
  are resolved on this tree (see the checks above).
- Lanes' own open items, unchanged by this integration (see each status file): AUD-io (#508 not
  reproducible; multi-file schema is the first file's), AUD-pluginfw (14 E501 lines in generated
  `integrations/` notebooks, outside T-27), AUD-stream (CRLF files kept), BUGS-gen-1 (four
  escalations: name pools, gender, `is_active` type, healthcare domain), BUGS-profile-1
  (`make bootstrap` installs no shape-fabric for two demo tests; AUD-tests' diff covers CI),
  G6-eval (P8-01 status and the `compare` alias vs D-13), G7-eval (11 high issues open by content,
  `open_security_issues.md`), HUNT2-generation (#711, #703 rest, #219, #615), HUNT2-kernels (three
  recorded kernel differences), HUNT2-plugins (#583, key-file sign, RECORD scope), HUNT2-profile
  (#643, #648, #686, #689, #595 follow-up), HUNT2-scenario (#668 and its proposed diff), W7-01
  (items 1-5 of "Things the lead must decide"), W7-03 (decisions 1-8), W8-02 (CMS-HCC V24 and
  the other in-package decisions), W8-03 (interpretations 1-7), W8-04 (release step for the SQL
  golden hashes).

## Landing (2026-10-05)

`int/INT-20` at 9e24dfe9 merged into `build/main-plan` (2722c381, the INT-18 landing) as a merge
commit ("INT-20: land (with INT-19)", 668b6b10); the plan's §2.3 and §11 are updated in the next
commit (e5853623), and this section in the one after. The landed tree equals `int/INT-20`'s apart
from `docs/plans` (`git diff origin/int/INT-20 HEAD` touches only `docs/plans/COMPLETION_PLAN.md`
and this file). INT-19 lands inside it: `int/INT-20` merged `int/INT-19` at 3a51d44d. Nothing was
force-pushed; `$REFENGINE_ROOT` was not touched; T-07's floors stay (`numpy>=2.0,<3`,
`pyarrow>=14.0.1`), and the floor raise remains an open owner escalation.

### Sharded verification (35619600)

Each shard ran in its own cloud container, with pyarrow 19.0.1 from `tests/demo/fabric/requirements.txt`;
reports in `docs/plans/evidence/INT-20/shard_<X>.md` on `verify/INT-20-<X>`.

| Shard | Result | Failures and classification |
|---|---|---|
| P1 (`SHAPE_KERNEL=python`, bounded-memory test) | 1 passed (1 h 15 min) | none |
| P2 (python: streaming, demo) | 1058 passed | none |
| P3 (python: the rest of `tests`) | 16513 passed, 10 failed | docs nav (fixed by 3eb47e21); streamed-CSV `ReaderError` x5 (fixed by c5f9a21f); reconcile NaN with pyarrow 19 (fixed by 9e24dfe9); E1 and E2 (below); `plugins/*/build` left by the shard's own setup (environment) |
| P4 (python: plugin suites, `tests/plugins` three ways, kits) | 4596 passed, 20 failed, 7 errors | shape-dbt `raw_type` x24 (fixed by 0f2f75c6); the healthcare-without-faker test needs the domains plugin (moved to `plugins/shape-domains/tests`, 01deb973, not skipped); two pyarrow 19 cases (#76 class, pre-existing) |
| R1 (rust: streaming, demo) | 1058 passed | none |
| R2 (rust: the rest of `tests`) | 16514 passed, 10 failed | the same ten as P3 |
| R3 (rust: plugins, coverage 94.61% >= 86%, heavy, python `tests/kernel`, zero-network, cargo) | 38131 passed, 39 failed, 21 errors | shape-dbt (0f2f75c6); streamed CSV (c5f9a21f); docs nav (3eb47e21); E1, E2; pyarrow 19 x3 (two #76 class, the reconcile one fixed by 9e24dfe9); Maven fetch on a cold ivy cache (14 errors, pass once cached); git commit signing inside `unshare --net` (also on the base); `test_a_long_text_value_is_tokenized_in_linear_time` 5.197 s against its 5.0 s bound under coverage at load 5 (passes alone 3/3; recorded as flaky, bound unchanged) |

shape-dbt with dbt installed and the moved test, both kernels, on 0f2f75c6 plus the move:
`docs/plans/evidence/INT-20/dbt_and_plugins.md` (shape-dbt 115 passed in each kernel; `tests/plugins`
installed / all twelve uninstalled / reinstalled and `plugins/shape-domains/tests` pass in each kernel).

### Fixes of the final session (tests first)

| Commit | What |
|---|---|
| c5f9a21f | the streamed-CSV schema probe (INT-18's c1999097, which reads the first blocks in memory) refuses duplicate names (#734) and turns a missing column into `ReaderError` (#499), as the reader path does; no reader of the file is opened on the success path. The five shard tests failed before. |
| 9e24dfe9 | `reconcile` treats the same infinity on both sides as equal (`inf - inf` is NaN). New test `test_an_infinite_value_reconciles_with_the_same_infinity` fails before with any pyarrow; the #572 test failed with pyarrow 19 (a per-key max of a NaN-only group is `-inf`). |

### Checks of the final session

Venv `/tmp/claude-0/kv20` (Python 3.11, `.[dev,streaming,advanced]` with the Rust kernel, every
plugin editable, pyarrow 19.0.1 from the demo requirements); pyarrow 25.0.1 checks put the pinned
baseline venv's pyarrow first on `PYTHONPATH`.

| Check | Result |
|---|---|
| the 5 streamed-CSV tests and the reconcile test, before the fixes | fail (pyarrow 19.0.1; the CSV ones also with 25.0.1) |
| `tests/io tests/sources tests/profile tests/quality` (`not heavy`), after the fixes | 1836 passed in each kernel |
| `tests/quality`, pyarrow 19.0.1 and 25.0.1, each kernel | 618 passed (4 runs) |
| landed tree (e5853623): `ruff check`, `ruff format --check` (1990 files), `mypy` (684 files), the eight `scripts/check_*.py` (`check_user_facing: clean`) | clean; every script exits 0 |
| the covering test files of the issues closed below (240 entries), rust kernel, on 9e24dfe9 (the landed code) | 6982 passed, 2 failed: E1 (so #283 and #396 stay open) and the pyarrow 19 Lakehouse case (#76 class) |
| the covering tests of #308, #309, #310, #721, #766 (including `tests/demo/fabric/test_udf.py`), rust kernel | 217 passed |

### Still failing on the landed tree, for the lead

- **E1 (#283, #396):** `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb`
  expects `is_raw_profile` to return `False` for an oversized manifest; the #283 fix (AUD-security2)
  refuses it with `RegistryError`. INT-19's escalation, unchanged.
- **E2 (#281):** `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output`
  expects a refusal; the merged tree makes the run id safe instead (AUD-scenario). INT-19's escalation, unchanged.
- Two pyarrow 19 cases of the #76 class (`test_lakehouse.py::test_parquet_to_a_local_folder_round_trips`,
  `test_scd2_file_drops.py::test_deltas_have_the_snapshot_columns_in_the_snapshot_order`).

### Issues

Closed after this landing, each with a comment naming the landing commits and its covering tests:
#721 (owner decision), #766 (W8-06), #308, #309, #310 (BUGS-demo-3), and the issues of the lanes'
fixes whose fix commit and covering test are on `build/main-plan` and whose lane status says fixed
(the list prepared from the range's commits and the lane status files, `close.json` in the landing
session): **433 issues closed in all** (428 from that list plus the five named), none left over, no rate limit hit.

Left open: the issues the lane status files leave open, partial or for the lead (#129, #152, #153,
#219, #220, #238, #242, #251, #259, #260, #267, #282, #285, #323, #328, #330, #332, #335, #336, #343,
#398, #425, #429, #473, #481, #493, #505, #509, #522, #528, #534, #535, #541, #543, #546, #552, #558,
#560, #582, #618, #619, #650, #683, #701, #712, #716, #717, #723, #724, #727, #730, #759); #283 and
#396 (E1); #281 (E2); #768, #567 and #92 (W1-15 and W8-04 held); #76 (pyarrow 19). The work-package
issues of the packages now done in §11 (#99, #101, #102, #142, #226, #233, #235, #565, #566) were not
in the list given for closing and stay open for the lead.
