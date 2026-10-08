# INT-19: integration of the audit, bug-hunt and roadmap lanes

Branch `int/INT-19`, created from `origin/int/INT-18` (`f94de925`). INT-18 was still being finished
by a parallel session; it was merged in again with merge commits whenever it moved (last merge:
see "INT-18 merges" below). Merge commits only; no rebase, no force-push. Commit prefix `INT-19: `.
§11 and §2.3 of the plan were not edited. No `.github/workflows` file was changed
(`git diff origin/int/INT-18 -- .github` is empty); the lanes' workflow diffs are collected below.
No gate, tolerance, D-xx or T-xx decision was changed, and no test was skipped, xfailed or
loosened. `$REFENGINE_ROOT` was only read.

## Merged lanes

| Lane | Head merged | Merge commit | Conflicts |
|---|---|---|---|
| P3-04 | `8a4bac78` | `5029c650` | none (status file only; the code was integrated earlier) |
| P4-10r2 | `83f2220c` | `9d9488b7` | `cli/main.py`, `CHANGELOG.md`: the lane side was an older build/main-plan state; INT-18's kept (the lane's code commits are all ancestors already) |
| W7-04 | `7e15423c` | `0dbf4570` | none (status file only) |
| G1-mt | `f84ed9dc` | `d648577f` | none (evidence and status) |
| W3-01 | `629e9f0f` | `bddaa4f5` | none |
| W3-03 | `20bb0da7` | `8b1cadff` | `cli/main.py` (INT-18's `context(a, source_flag=False)` does what the lane's namespace copy did), project schema (INT-18 already a superset) |
| W3-05 | `f3e93509` | `d072ba84` | none |
| AUD-bridge | `f85ee7ef` | `3fdf9537` | `types.py` (#260 validation with AUD-api's docstrings), `api.py` (#251 refusals with INT-18's forms and composites), `bridge/core.py` (W7-04's version gate and `jsonable`), `BRIDGE.md`, CHANGELOG |
| AUD-builtins | `88f38914` | `90555cd5` | `families.py`: W1-15's `generator_version` with the lane's constants |
| AUD-ci | `7a0d9155` | `25d95ac7` | `scripts/check_secrets.py` (reformatted INT-18 text with the lane's patterns) |
| AUD-design | `fe662076` | `58c17906` | `proposals/model.py` (W3-02's version-gated kinds, then `_check_identity`), `spec/model.py` (see open item E3) |
| AUD-docs | `263d188d` | `f3da4b39` | `check_secrets.py` and its tests (both lanes' tests kept and passing), `offline_lock.py`, `check_shipped_data.py`, fuzz and user-facing tests, CONTRIBUTING |
| AUD-fabric | `e4168ee3` | `2bb5588d` | `sinks.py` (#443 with W2-10's identity/constraints), `eventhouse.py` (#444 with BF-223's warm-up), `sqldb.py` (W2-10's writer) |
| AUD-gen | `ffb13b16` | `c885f6fe` | `engine.py` (copula before compute and rules, #169), `schema.py`, `drift_plan.py`, `ddl_1to1/differences.py` (W2-10 took F12; AUD-gen's F12-F17 are F13-F18, every reference renumbered) |
| AUD-kernel | `c9481c81` | `d07649a8` | CHANGELOG; the kernel was rebuilt (`maturin develop --release`) |
| AUD-packaging | `5f160fd9` | `1c4aba0b` | `pyproject.toml`: the lane's classifiers and Rust notices with BF-76's dependency floors |
| AUD-portability | `f3f4c7eb` | `995be467` | registry log (UTC `created` and UTF-8 `\n` writes), Fabric test conftest |
| AUD-privacy | `a963c9a1` | `c27c526c` | registry reads (lane checks with UTF-8), credrefs docstring |
| AUD-profile | `573ca818` | `84b4486c` | readers, sources, profile, workbook (W2-01/#46/univariate with the lane's CSV shapes, pools and reference-pair checks), PROFILING_NOTES |
| AUD-scenario | `0cc8c3fc` | `2922714f` | run manifest (W1-01 compat; malformed declarations name the file), runner (provenance and contained paths), validator imports |
| AUD-security2 | `2a247966` | `aafd5920` | Fabric API and notebook, bridge warnings, scale sink temp files, registry (#283), runner (see E1, E2) |
| BUGS-cli-1 | `7504429f` | `49e66cab` | registry `json_object` |
| HUNT2-fabric | `8416dd74` | `e2eb8a46` | scale router (#718 abort with INT-18's close), Spark URLs, Spark worker (#726 with AUD-kernel's #484), job records |
| W5-08 | `f368faf0` | `43471b39` | skeleton list (12 distributions), pytest markers, authoring docs |
| W5-03 | `ab5907bc` | `524b5909` | generate options (with W3-08's `--mixed-copula`), `fit.py` (vault overlay, then mixed copula), profile/plan options, THREAT_MODEL |
| W6-03 | `623c08d1` | `8847e916` | CLI registries (exit codes, machine specs, dry-run tables), generate `--from dataset:NAME`, engine (INT-18's), CHANGELOG (W6-03's own entry only) |

INT-18 merges: `3b49a31b` (INT-18 at `eefec75b`), `e99bb8be` (`81a7e485`: API.md signatures,
bisect `--json`) and `f3ffa7c6` (`9886a7d8`: report-card vectors, W3-08's multivariate opt-in, CLI
parser imports) and `6356eb18` (`e77d5583`: build/main-plan with INT-16 landed, BUGS-contracts, PF-05,
W6-01's final state, the lead's workflow changes, safe-profile and bridge parity fixes; no
conflict); then `6594c830` (BUGS-win, frozen bridge list vectors with INT-16's descriptions,
W1-15 held for W8-04) at `10cc7ff5`; then `97093be6` (`f5dd2f62`: T-07's dependency floors kept,
streamed CSV readers). The first one brought W1-11, W1-14,
W2-07, W3-08, W3-12, W3-13, W5-06, W6-01 and the AUD-quality, AUD-perf, AUD-dbplugins lanes).
Its conflicts: registry `is_raw_profile` (W1-11's capture mode over the merged sniff), engine
(W3-08's mixed copula runs with the correlated copula before rule repair), profile reference
modules (W2-07 sampling, decisions and validators), `api.generate` (`mixed_copula`, refused by the
other forms as #251 asks), privacy CLI (W1-14's machine wrapper), quality policy (INT-18's, which
carries #152), bridge profile scratch file, README, JOINT, PROFILING_NOTES, trust model, markers.

## Not merged

| Lane | Why |
|---|---|
| W1-17 | Held until after the 2026-10-07 demo (§2.3). Its head `e245042b` (a status file) is not merged. **Note:** its code is already in INT-18: `lane/W7-05` merged `origin/lane/W1-17` (`5aa21999`) and INT-18 merged W7-05 (`06e0f886`); INT-18's `e4e4b489` even adapts a test to it. Whether to back it out before the demo is the lead's call (not done here). |
| P6-01 lanes | Owned by another lane, as instructed. |

W5-03 and W6-03 were merged only after INT-18 contained W1-11 (W6-03 also carried W1-11's and
W1-14's work-in-progress commits; INT-18 now holds both lanes). Every other listed lane's status
says it is finished (with open items, below). HUNT2-fabric has no status file; its commits are
test-first fixes of #718 and #726 with CHANGELOG entries.

## Fixes made by this integration (each in its own commit, tests first where a test was missing)

| Commit | What |
|---|---|
| `a8cbc1f2` | AUD-ci's secret check flagged a credential reference (`env://DBX_SECRET`) in W2-08's Databricks test: marked `nosec`, the check's documented marker |
| `152d645b` | `check_secrets.py` line lengths after the AUD-ci merge |
| `5cec122d` | `generation-spec-v1.schema.json` regenerated (`python -m shape.generation.spec_schema`): W2-10's `identity` (already failing on INT-18) and AUD-gen's `date`/`time` output types |
| `7a18bb42` | classifiers and URLs for the four plugins INT-16..18 added (AUD-packaging #249); domains notices = root (#250); the packaging test finds hyphenated wheel names; W1-18's trust test writes its key owner-only (AUD-privacy) and the trust model says so |
| `01f4cf43` | BUGS-cli-1's Deduped test loads `tests/scale/test_jobs.py` by path (a bare import picked `tests/bridge/test_jobs.py` in a full run) |
| `12117ecb` | the Spark worker builds one engine per executor process: with AUD-kernel #484 every partition rebuilt the parent tables, and HUNT2's new test took 1073 s (36 s on its lane); now the whole `tests/scale` folder runs in 58 s |
| `641b1b75` | W5-08's `shape-integrations` gets a core extra (also in `all`), its INSTALL line, its trust-model row, classifiers and URLs; the distribution count is twelve |
| `8398a22d` | W3-08's copula tests edited the profile through `Profile.tables`, which AUD-profile made return copies (#326); they edit `to_dict()` now (assertions unchanged); domains notices = root |
| in `524b5909` | `shape vault` answers `--help` as `migrate` does, exit-code registry entries for `vault` and `cat --verify`, EXIT_CODES regenerated, CLI surface baseline refreshed (additive only) |
| in `8847e916` | the same for W6-03's five commands |
| `f30c1122` | domains notices = root after W6-03 |
| `b2e20553` | W3-03's merge had taken `history` out of `test_removed_module_is_not_importable` (INT-18 renamed the package `shape.versions`, 688e577a, because `shape.history` is on the P0-04 cut list): the gate is whole again and W3-03's legacy check reads `shape.versions` |
| `f55ad7fa` | BUGS-cli-1's encoding test exports a full capture (W1-11 default); W5-10's key files owner-only (AUD-privacy); `save` signature in API.md (W5-03); the VS Code bundled schema (W6-04); DETECTIVE.md (AUD-docs link test) |
| `a2749992` | a formula's nesting limit (AUD-builtins #137) no longer depends on the interpreter's recursion limit: jedi (imported by IPython in the demo tests) sets 3000; regression test `tests/generation/test_formula_depth.py` |

## Check results

Environment: Python 3.11.15, `~/.venvs/shape` with `.[dev,streaming,advanced]`, every plugin
editable, the kernel built with `maturin develop --release` after each Rust change, plus the
`tests/demo/fabric` requirements except `fabric-user-data-functions`' pyarrow pin (numpy 2.4.6 and
pyarrow 25.0.1 kept; installed with `--no-deps`, its other dependencies installed) and unixODBC.
Pinned baseline 3.0.1 (`422e78df`) built by `setup_refengine.sh` into its own venv; the checkout
was only read. Private `TMPDIR` for every pytest run.

| Check | Tree | Result |
|---|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | final | pass |
| `ruff format --check` (same paths) | final | pass (1888 files) |
| `mypy` | final | pass (679 files); before pydantic was installed it reported `pydantic` missing in `importers/pydantic_models.py`, also on INT-18's head |
| compileall, vulture, lint-imports, check_requirements, check_secrets, check_user_facing, check_shipped_data, check_plugin_skeletons, check_conformance_coverage, check_v1_done, `cli_surface.py --check`, `gen_exit_codes.py --check`, `gen_failure_modes.py --check` | final | all pass |
| `python scripts/check_user_facing.py` | final | clean |
| `cargo fmt --check`, `clippy -D warnings`, `cargo test` | `a2749992` | pass (34 tests) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | `8847e916`+`f30c1122` | 16076 passed, 25 failed, 18 skipped (classified below; every merge-caused one fixed in `b2e20553`, `f55ad7fa`, `a2749992`, and each re-run passed) |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | `a2749992` | 16100 passed, 5 failed, 18 skipped (2:27 h) |
| final `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | `f3ffa7c6` | 16109 passed, 3 failed (E1, E2 and the pre-existing `test_emit_to_two_files`), 18 skipped (51 min); the heavy memory test passed |
| final `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | `f3ffa7c6` | 16109 passed, 3 failed (the same three), 18 skipped (2:03 h; a first attempt was cut off at 57% by a restart of the session's worker); the heavy memory test passed |
| **last** `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | `6356eb18` (after INT-18 `e77d5583`) | 16251 passed, 4 failed: E1, E2 and `tests/bridge/test_compat_1_0.py`/`test_compat_1_1.py` `[list]` (domain descriptions; **identical on INT-18's head** `e77d5583`, from INT-16's bridge list change); `test_emit_to_two_files` passes now |
| **last** `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | `6356eb18` | 16251 passed, the same 4 failed, 18 skipped (2:00 h); the heavy memory test passed |
| full `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | `d702c6f8` (after W1-15 was held) | 15880 passed, 2 failed (E1, E2), 18 skipped (46 min); a first attempt aborted when the disk filled with test temp folders, which were then removed |
| full `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | `d702c6f8` | 15879 passed, 3 failed (E1, E2 and the heavy bounded-memory test: 24M rows 512 MB, 48M rows 412 MB), 18 skipped (2:03 h) |
| static checks; `tests/profile tests/streaming tests/io tests/sources`, `tests/release`, `tests/plugins/test_extras.py` | `97093be6` | pass (1523 and 367 passed) |
| full `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | `97093be6` | 15885 passed, 3 failed (E1, E2 and the bounded-memory test: 24M 282 MB, 48M 312 MB), 18 skipped (48 min) |
| static checks (ruff, format, mypy, vulture, lint-imports, every `scripts/check_*`, CLI surface, exit codes, failure modes) | `6356eb18` | all pass; `git diff origin/int/INT-18 -- .github` is empty |
| `safe_profile_1to1`, `bridge_1to1`, `profile_1to1 --impl shape` (rust) | `6356eb18` | exit 0 (53/53 bridge checks); INT-18's parity fixes resolve the two earlier pre-existing failures |
| `demo_1to1` | `6356eb18` | FAIL on 4 checks (catalog, list, cost estimate, notebooks); identical FAIL/PASS lines on INT-18's head `e77d5583` |

Failures and their class:

| Test | Class | Evidence |
|---|---|---|
| `tests/bridge/test_vectors.py` `[report_card]`, `[report_card_read]` | pre-existing on INT-18 | fail identically on INT-18's head in this venv (worktree run); INT-18 fixed them in `2daa629f` and `9886a7d8`, merged in `f3ffa7c6`; they pass after it |
| `tests/streaming/emit/test_faults.py::test_emit_to_two_files` | pre-existing on INT-18 | fails on INT-18's head in this venv |
| `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb` | contradiction between two lanes' tests | E1 |
| `tests/scenario/test_run_paths.py::test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output` | contradiction between two lanes' fixes | E2 |
| `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` (heavy) | open (E7) | failed in 3 of 9 completed full runs (Rust `97093be6`: 24M 282 MB, 48M 312 MB; Rust `8847e916`: 24M 349 MB, 48M 414 MB; Python `d702c6f8`: 24M 512 MB, 48M 412 MB) and passed in the other 6 and when re-run alone; the child process's peak RSS varies by more than the 10% limit from run to run on this VM. Not changed; the lead decides (the test is not relaxed) |
| `test_public_api` `[profile]`, `[generate]` | pre-existing on INT-18 | fixed by INT-18's `c45d1f95` (merged) |
| `test_public_api[save]`, `test_vscode_extension`, `test_removed_modules`, `test_docs` (DETECTIVE), `test_bugs_cli_registry_bom` x5, `w5_10` x6, `test_aud_builtins` formula | merge-caused | fixed (commits above) |
| `tests/versions/test_cli.py::test_bisect_prints_json_equal_to_the_api` | pre-existing on INT-18 | fixed by INT-18's `c45d1f95` (merged) |

Equivalence verifiers (this session, final code except where noted; equivalence before timing):

| Verifier | Result |
|---|---|
| `profile_1to1/verify.py --impl shape`, `SHAPE_KERNEL=rust` and `python` | exit 0 (both) |
| `ddl_1to1` (with the F12-F18 renumbering) | exit 0 |
| `domain_1to1 --impl shape --domain retail --scale small` | PASS |
| `learn_1to1`, `chaos_1to1 --quick`, `pack_1to1`, `mask_1to1`, `transform_1to1`, `verify_1to1` (20/20), `stream_1to1`, `incremental_1to1`, `writers_1to1` | all exit 0 |
| `safe_profile_1to1` | exit 1: every column has an extra `validators` key; **identical on INT-18's head** (pre-existing, INT-18's W3-12) |
| `bridge_1to1` | 50/51: `list` (financial and pulse descriptions and profiles); **identical on INT-18's head** |
| `demo_1to1` | FAIL on 5 checks (catalog, list, cost estimate, notebooks, scenario domain); **identical FAIL/PASS lines on INT-18's head** |
| `fabric_commands_1to1` | not run: it needs the baseline installed in its own venv (INT-14 note) |

`python benchmarks/vs_refengine/run.py --quick` (`a2749992`, results kept in the session scratchpad,
not committed; load 1.74 at start): exit 0, every verifier pass. Shape: profile d1.csv 3.71x,
d1.parquet 4.05x, d2.csv 12.0x, d2.parquet 15.93x; stream retail order small 2.19x, medium
14.92x. Reference port: profile 4.27x/4.06x/11.09x/10.82x, generate retail small 1.37x, medium
5.77x. Against the committed `results.json` (2026-09-30) the reference port's d1.csv is 15% and
retail small 35% slower; both are workloads under 0.4 s timed with the load above 1.5, and no
merged lane changes the reference port; a nightly run is the T-19 reference.

## Open items for the lead

E1. **#283, two lanes' tests contradict each other.** For a zip whose manifest inflates past the
reader's limit, AUD-security2's `tests/registry/test_raw_profile_bomb.py` expects `RegistryError`,
AUD-privacy's `tests/registry/test_aud_privacy.py::test_the_manifest_sniff_does_not_inflate_a_bomb`
expects `False`. The issue says "an oversized manifest is refused with the artifact error", and a
`False` would let a full-capture container past `commit`; the refusal is kept, so the AUD-privacy
test fails. Its expectation is not changed here.

E2. **#281, two lanes' fixes contradict each other.** The issue allows "refused, or replaced with a
safe name". AUD-scenario replaces unsafe characters in the run id and the run succeeds; AUD-security2
refuses the run. The replacement (merged first) is kept: it also keeps composite domain names
(`retail+hr`) running. So `tests/scenario/test_run_paths.py::
test_a_domain_name_that_is_a_path_is_refused_and_nothing_leaves_the_output` (AUD-security2) fails.

E3. **Contract model unknown fields (AUD-design x W1-01).** W1-01's test requires a contract model
to keep fields it does not know; AUD-design's requires a contract with an unknown key to be
refused. Both pass with: a file that declares `format` (written by a Shape release) keeps unknown
fields; a hand-written one refuses them unless they start with `x_`.
`docs/specs/STATE_AND_COMPATIBILITY.md` §5 says so. Please confirm.

E4. **Generator versions.** Moot for now: INT-18 holds W1-15 for W8-04 (`bea22e4a`), and INT-19
dropped the W1-15 parts its resolutions had kept (`generator_version` on the truncated family, the
`pin` exit code, the schema's pin issues). When W1-15 returns, the output changes of AUD-builtins
(#129-#149), AUD-gen (#169, #202, ...) and AUD-kernel need a decision under its rule (raise the
version, keep the old one selectable), as no version was raised for them.

E5. **W1-17 is already in INT-18** (see "Not merged").

E7. **Bounded-memory test variance** (see the failure table). Its result varies between runs of the same tree in this container; whether it needs a quieter runner or a different measurement is for the lead.

E6. **Lane open items, as each lane's status file lists them:** AUD-bridge #541 (demo commands
wiring changes two existing tests), `stream_stop` status value, #543 (fixed by BUGS-cli-1);
AUD-builtins `FastAddressPack` bound; AUD-docs #352 (offline lock and Windows-only `tzdata`), the
`fabric`/`dbt`/`healthcare`/`all` extras wording and plugins not on PyPI (#310), talk readiness
#377; AUD-fabric #425 (duplicate measure names vs the fabric commands verifier), #429 (commit
before insert; a recorded fixture pins it), #445 duration half, pyarrow 19 dictionary test;
AUD-gen #178, #182, #194, #175, MySQL `#` comments, #219, #220; AUD-kernel #547 (non-ASCII
`string_case` between kernels), K5 (µs flooring of exact timestamps, T-13), K8, K12, G11, G13/G14;
AUD-packaging #247, #252, #255; AUD-portability #238-#244; AUD-privacy #554 (credential-reference
test reads all of `sys.modules`), D1-D3; AUD-profile #323, #322 kernel part, #336; AUD-scenario
#513, #522, #528, SCENARIO_PACKS wording; BUGS-cli-1 #311 summary line, #126 owner decision, #152
exit code; W3-03 interpretations 1-6; W3-05 interpretations; W5-03's five items in its status
file; W5-08 Anonymeter's `numpy<1.27` pin vs T-07, the manifest's column list, and its CI diff
(the status file names a "CI diff" section that is not in it); W6-03 decisions 1 and 2; W7-04
project gate names; P4-10 GEN-CLI medium (escalated earlier).

## Workflow diffs from the lanes (not applied)

- **W5-08:** its status says a workflow change is needed for the integrations job and points to a
  "CI diff" section, but the file has none. To be supplied by the lane.
- **AUD-ci:** D1 to D9, copied below from `docs/plans/lane_status/AUD-ci.md` (apply in order).
- Every other merged lane states that it needs no workflow change.


### AUD-ci D1 to D9 (verbatim from `docs/plans/lane_status/AUD-ci.md`, against `5c91ea5`)


Apply in order D1 → D8 (each is against the result of the previous; checked with `git apply` in
sequence on `5c91ea5`; actionlint clean afterwards; the workflow-reading tests
`tests/demo/core/test_publish_workflow.py`, `tests/integrations/test_container.py` and
`tests/diff/test_drift_sweep_ci.py` pass with D1–D7 applied).

#### D1: pin every action to a full commit SHA, on its Node 24 major (#265; findings 1, 3)

SHAs resolved with `git ls-remote` on 2026-10-03 (`refs/tags/<tag>^{}`). Majors chosen are the
first that run on Node 24 (download-artifact v5/v6 are still Node 20; attest-build-provenance v3
uses `actions/attest` v3, Node 24). Breaking changes checked: artifacts are used by name only, so
download-artifact v7's by-ID path change does not apply. **Needs D7**: the existing test pins the
`@release/v1` text, so this lane did not change it (brief: an existing test's expectation is the
lead's call).

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:02.781802652 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:02.808089788 +0000
@@ -25,8 +25,8 @@
           - {os: windows-latest, python: '3.14'}
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
@@ -74,8 +74,8 @@
     # in-process Spark gateway.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
@@ -94,13 +94,13 @@
     # needed to resolve) and checked against pyproject.toml. See docs/INSTALL.md.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip uv packaging
       - run: python scripts/offline_lock.py generate "$RUNNER_TEMP/offline-lock"
       - run: python scripts/offline_lock.py check "$RUNNER_TEMP/offline-lock"
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: offline-lock, path: '${{ runner.temp }}/offline-lock/requirements-*.txt'}
   plugin-skeletons:
     # P2-06: every first-party plugin distribution builds a pure wheel, and the example plugin,
@@ -111,8 +111,8 @@
         os: [ubuntu-latest, macos-latest, windows-latest]
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]'
@@ -130,8 +130,8 @@
       matrix:
         os: [ubuntu-latest, windows-latest]
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
@@ -145,8 +145,8 @@
       matrix:
         os: [ubuntu-latest, windows-latest]
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev]' -e plugins/shape-databases
@@ -156,8 +156,8 @@
     # T-27: cargo fmt / clippy -D warnings / cargo test for rust/shape-kernel.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: Swatinem/rust-cache@v2
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6  # v2.9.2
         with: {workspaces: rust/shape-kernel}
       - run: cargo fmt --manifest-path rust/shape-kernel/Cargo.toml --check
       - run: cargo clippy --manifest-path rust/shape-kernel/Cargo.toml --all-targets -- -D warnings
@@ -165,8 +165,8 @@
   audit:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: python -m pip install -U pip
       - run: pip install -e '.[dev,streaming]'
@@ -174,10 +174,10 @@
   fabric-demo:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
-      - uses: actions/setup-java@v4
+      - uses: actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961  # v5.7.0
         with: {distribution: temurin, java-version: '17'}
       - run: sudo apt-get update -q && sudo apt-get install -y -q unixodbc
       - run: python -m pip install -U pip
@@ -189,8 +189,8 @@
     # and SHAPE_KERNEL=python (no native kernel). Must be green on every PR.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: sudo apt-get update -q && sudo apt-get install -y -q unixodbc
       - run: bash scripts/ci_pure_wheel.sh python
@@ -202,8 +202,8 @@
     timeout-minutes: 90
     env: {BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Pinned RefEngine baseline and venv (plan section 1.2)
         run: source scripts/env.sh && bash benchmarks/vs_refengine/setup_refengine.sh
@@ -222,7 +222,7 @@
           "$REFENGINE_PY" benchmarks/vs_refengine/domain_1to1/generate.py --impl refengine --domain retail --scale small --seed 42
           "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/domain_1to1/generate.py --impl reference_port --domain retail --scale small --seed 1042
           "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/verify_1to1/verify.py
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: always()
         with:
           name: benchmark-results-quick
@@ -232,8 +232,8 @@
   build:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: pip install build twine pip-audit
       - run: python -m pip install --upgrade pip
@@ -244,5 +244,5 @@
       # W6-02: every reference data file is inside the wheel; nothing downloads at run time.
       - run: python scripts/check_shipped_data.py --wheel dist/*.whl
       - run: pip-audit
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: distributions, path: dist/*}
diff -ruN a/.github/workflows/container.yml b/.github/workflows/container.yml
--- a/.github/workflows/container.yml	2026-10-03 13:14:02.781831376 +0000
+++ b/.github/workflows/container.yml	2026-10-03 13:14:02.809228447 +0000
@@ -14,9 +14,9 @@
   build:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: docker/setup-buildx-action@v3
-      - uses: docker/build-push-action@v6
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
+      - uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc  # v7.4.0
         with: {context: ., load: true, tags: 'shape:ci', cache-from: 'type=gha', cache-to: 'type=gha,mode=max'}
       - name: Image size is under 500 MB
         run: |
@@ -29,7 +29,7 @@
           docker run --rm -e SHAPE_KERNEL=rust shape:ci python -c \
             "import adlfs, azure.identity, deltalake, shape; from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
           docker run --rm shape:ci shape plugins list --group shape.sources
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Profile D1, mounted read-only from the host, inside the image
         run: |
@@ -49,19 +49,19 @@
     runs-on: ubuntu-latest
     permissions: {contents: read, packages: write, id-token: write, attestations: write}
     steps:
-      - uses: actions/checkout@v4
-      - uses: docker/setup-buildx-action@v3
-      - uses: docker/login-action@v3
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
+      - uses: docker/login-action@dbcb813823bdd20940b903addbd779551569679f  # v4.6.0
         with: {registry: ghcr.io, username: '${{ github.actor }}', password: '${{ secrets.GITHUB_TOKEN }}'}
       - id: meta
-        uses: docker/metadata-action@v5
+        uses: docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302  # v6.2.0
         with:
           images: ${{ env.IMAGE }}
           tags: |
             type=semver,pattern={{version}}
             type=semver,pattern={{major}}.{{minor}}
       - id: push
-        uses: docker/build-push-action@v6
+        uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc  # v7.4.0
         with: {context: ., push: true, tags: '${{ steps.meta.outputs.tags }}', labels: '${{ steps.meta.outputs.labels }}'}
-      - uses: actions/attest-build-provenance@v2
+      - uses: actions/attest-build-provenance@96278af6caaf10aea03fd8d33a09a777ca52d62f  # v3.2.0
         with: {subject-name: '${{ env.IMAGE }}', subject-digest: '${{ steps.push.outputs.digest }}', push-to-registry: true}
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:14:02.781848828 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:14:02.809013194 +0000
@@ -12,8 +12,8 @@
     timeout-minutes: 360
     env: {BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"}
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Pinned RefEngine baseline and venv (plan section 1.2)
         run: source scripts/env.sh && bash benchmarks/vs_refengine/setup_refengine.sh
@@ -25,7 +25,7 @@
           "$SHAPE_VENV/bin/pip" install -e '.[dev]' 'pyarrow==25.0.1'
       - run: source scripts/env.sh && python benchmarks/vs_refengine/check_coverage.py
       - run: source scripts/env.sh && python benchmarks/vs_refengine/run.py --full
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: always()
         with:
           name: benchmark-results-full
@@ -38,8 +38,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 60
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]'
       - run: python -m pytest -q tests/diff/test_drift_sweep.py
@@ -52,8 +52,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 90
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]' -e plugins/shape-domains
       - run: python -m pytest -q -s -m heavy tests/streaming/emit/test_soak.py
@@ -63,15 +63,15 @@
     # work packages that own those connectors; until then, keep the compose file valid.
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - run: docker compose -f ci/emulators/docker-compose.yml config --quiet
   azurite-e2e:
     # PF-01: the abfss:// source against Azurite (blob endpoint) with the real adlfs.
     # ISS2-sinks: the abfss:// sink (write, read back, rolling files, errors, the CLI).
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev,azure]' azure-storage-blob
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite
@@ -84,8 +84,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka
@@ -98,8 +98,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
       - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-eventhubs
@@ -112,8 +112,8 @@
     runs-on: ubuntu-latest
     timeout-minutes: 30
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs
       - run: docker compose -f ci/emulators/docker-compose.yml up -d kusto
@@ -143,9 +143,9 @@
       - if: env.HAVE_LIVE != 'true'
         run: echo "no live secrets configured; nothing to run"
       - if: env.HAVE_LIVE == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_LIVE == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: env.HAVE_LIVE == 'true'
         run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-fabric
@@ -183,9 +183,9 @@
       - if: env.HAVE_ANY != 'true'
         run: echo "no Fabric secrets configured; nothing to run"
       - if: env.HAVE_ANY == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_ANY == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Microsoft ODBC Driver 18
         if: env.HAVE_ANY == 'true'
@@ -227,7 +227,7 @@
       - if: env.HAVE_ANY == 'true' && env.FABRIC_CLIENT_ID != '' && env.FABRIC_TENANT_ID != '' && env.FABRIC_CLIENT_SECRET != '' && env.FABRIC_SQL_CONNECTION_STRING != ''
         run: python -m pytest -m live plugins/shape-fabric/tests/test_live_commands.py -q -k sql_database
       - if: always() && env.HAVE_ANY == 'true'
-        uses: actions/upload-artifact@v4
+        uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: fabric-live-tapes
           path: live-tapes
@@ -250,9 +250,9 @@
       - if: env.HAVE_ABFSS != 'true'
         run: echo "no abfss live secrets configured; nothing to run"
       - if: env.HAVE_ABFSS == 'true'
-        uses: actions/checkout@v4
+        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - if: env.HAVE_ABFSS == 'true'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: env.HAVE_ABFSS == 'true'
         run: pip install -e '.[dev,azure]'
@@ -267,8 +267,8 @@
       BENCH_OUT_DIR: "${{ github.workspace }}/bench-out"
       SHAPE_TEST_MSSQL: "Driver={ODBC Driver 18 for SQL Server};Server=localhost,1433;UID=sa;PWD=Shape_Emulator_1;Encrypt=yes;TrustServerCertificate=yes"
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - name: Microsoft ODBC Driver 18
         run: |
@@ -308,8 +308,8 @@
       SHAPE_POSTGRES_PASSWORD: shape_emulator
       SHAPE_MYSQL_PASSWORD: shape_emulator
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait postgres mysql
       - run: pip install -e '.[dev]' -e 'plugins/shape-databases[postgres,mysql]'
@@ -323,12 +323,12 @@
     runs-on: ubuntu-latest
     timeout-minutes: 60
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev]'
       - run: python scripts/fuzz_artifacts.py --seed "$(date +%s)" --iterations 3000 --out fuzz-findings
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         if: failure()
         with:
           name: fuzz-findings
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:02.781875936 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:14:02.808329465 +0000
@@ -31,8 +31,8 @@
     name: Build and test the wheel and sdist
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with:
           python-version: "3.11"
       - run: python -m pip install --upgrade pip build twine
@@ -51,7 +51,7 @@
       - name: twine check
         run: python -m twine check dist/*
       - run: ls -l dist
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: dist
           path: dist/*
@@ -66,15 +66,15 @@
       id-token: write
       contents: read
     steps:
-      - uses: actions/download-artifact@v4
+      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131  # v7.0.0
         with:
           name: dist
           path: dist
       - name: Publish to TestPyPI
         if: github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi'
-        uses: pypa/gh-action-pypi-publish@release/v1
+        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33  # v1.14.2
         with:
           repository-url: https://test.pypi.org/legacy/
       - name: Publish to PyPI
         if: github.event_name != 'workflow_dispatch' || inputs.repository == 'pypi'
-        uses: pypa/gh-action-pypi-publish@release/v1
+        uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33  # v1.14.2
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:02.781895520 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:14:02.808446995 +0000
@@ -5,8 +5,8 @@
   release:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.13'}
       - run: python -m pip install --upgrade pip
       - run: pip install build twine pip-audit cyclonedx-bom
@@ -16,7 +16,7 @@
       - run: python -m twine check dist/*
       - run: pip-audit
       - run: cyclonedx-py environment -o sbom.json
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: release-candidate
           path: |
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:02.781911759 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:02.807309044 +0000
@@ -14,8 +14,8 @@
   security:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: actions/setup-python@v5
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: "3.12"}
       - run: python -m pip install --upgrade pip
       - run: python -m pip install -e ".[dev,streaming]"
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:02.781928297 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:02.807611222 +0000
@@ -30,18 +30,18 @@
           - {name: windows-x64, os: windows-latest, target: x64, kind: native}
     runs-on: ${{ matrix.os }}
     steps:
-      - uses: actions/checkout@v4
-      - uses: PyO3/maturin-action@v1
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
         with:
           target: ${{ matrix.target }}
           manylinux: ${{ matrix.manylinux || 'off' }}
           args: --release --out dist
           # the wheel is abi3 (py311+), so one interpreter is enough to build it
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: 'wheel-${{ matrix.name }}', path: dist/*.whl}
       # --- smoke test on the build machine (native kinds) ---
       - if: matrix.kind == 'native'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       - if: matrix.kind == 'native'
         shell: bash
@@ -50,7 +50,7 @@
           python -m pip install dist/*.whl
           SHAPE_KERNEL=rust python -c "import shape; print(shape._kernel.version()); from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
       - if: matrix.kind == 'native'
-        uses: actions/setup-python@v5
+        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.14', allow-prereleases: true}
       - if: matrix.kind == 'native'
         shell: bash
@@ -75,9 +75,9 @@
       fail-fast: false
       matrix: {python: ['3.11', '3.14']}
     steps:
-      - uses: actions/download-artifact@v4
+      - uses: actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131  # v7.0.0
         with: {name: wheel-macos-x86_64, path: dist}
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: |
           python -m pip install -U pip
@@ -86,12 +86,12 @@
   sdist:
     runs-on: ubuntu-latest
     steps:
-      - uses: actions/checkout@v4
-      - uses: PyO3/maturin-action@v1
+      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
+      - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
         with: {command: sdist, args: --out dist}
-      - uses: actions/upload-artifact@v4
+      - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: sdist, path: dist/*.tar.gz}
-      - uses: actions/setup-python@v5
+      - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
       # the sdist must build with Rust >= 1.85 (the runner's stable) and then run
       - run: |
```

#### D2: `timeout-minutes` on every job (#265; finding 4)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:02.808089788 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:46.186536297 +0000
@@ -24,6 +24,7 @@
           - {os: windows-latest, python: '3.11'}
           - {os: windows-latest, python: '3.14'}
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 90
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -73,6 +74,7 @@
     # in-process socket guard (tests/conftest.py) were bypassed. Loopback stays up for the
     # in-process Spark gateway.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -93,6 +95,7 @@
     # W6-02: pinned, hashed requirements for core and each extra, built here (a network is
     # needed to resolve) and checked against pyproject.toml. See docs/INSTALL.md.
     runs-on: ubuntu-latest
+    timeout-minutes: 20
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -110,6 +113,7 @@
       matrix:
         os: [ubuntu-latest, macos-latest, windows-latest]
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -126,6 +130,7 @@
   stream-plugins:
     # P3-04, P5-02, P6-07a, P6-08: the Kafka, Event Hubs, SQL Server and Fabric plugins' contract tests.
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 30
     strategy:
       matrix:
         os: [ubuntu-latest, windows-latest]
@@ -141,6 +146,7 @@
     # ISS2-sinks: the PostgreSQL and MySQL sinks, contract tests against an in-memory server
     # (no driver is installed: the sinks load psycopg / PyMySQL only when they connect).
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 20
     strategy:
       matrix:
         os: [ubuntu-latest, windows-latest]
@@ -155,6 +161,7 @@
   rust:
     # T-27: cargo fmt / clippy -D warnings / cargo test for rust/shape-kernel.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: Swatinem/rust-cache@6323deb102c322ba6fcbdcafc7e3dddab59af2b6  # v2.9.2
@@ -164,6 +171,7 @@
       - run: cargo test --manifest-path rust/shape-kernel/Cargo.toml
   audit:
     runs-on: ubuntu-latest
+    timeout-minutes: 15
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -173,6 +181,7 @@
       - run: pip-audit --skip-editable
   fabric-demo:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -188,6 +197,7 @@
     # function_app.py pass on Python 3.11 with only PyPI numpy, pyarrow and pandas under them
     # and SHAPE_KERNEL=python (no native kernel). Must be green on every PR.
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -231,6 +241,7 @@
             ${{ github.workspace }}/bench-out/verify/
   build:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/container.yml b/.github/workflows/container.yml
--- a/.github/workflows/container.yml	2026-10-03 13:14:02.809228447 +0000
+++ b/.github/workflows/container.yml	2026-10-03 13:14:12.619630135 +0000
@@ -13,6 +13,7 @@
 jobs:
   build:
     runs-on: ubuntu-latest
+    timeout-minutes: 45
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1
@@ -47,6 +48,7 @@
     if: startsWith(github.ref, 'refs/tags/v')
     needs: build
     runs-on: ubuntu-latest
+    timeout-minutes: 45
     permissions: {contents: read, packages: write, id-token: write, attestations: write}
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:14:02.809013194 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:14:12.620144046 +0000
@@ -62,6 +62,7 @@
     # The emulator-backed test jobs (Kafka, Event Hubs + Azurite, SQL Server) arrive with the
     # work packages that own those connectors; until then, keep the compose file valid.
     runs-on: ubuntu-latest
+    timeout-minutes: 10
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - run: docker compose -f ci/emulators/docker-compose.yml config --quiet
@@ -69,6 +70,7 @@
     # PF-01: the abfss:// source against Azurite (blob endpoint) with the real adlfs.
     # ISS2-sinks: the abfss:// sink (write, read back, rolling files, errors, the CLI).
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:02.808329465 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:14:12.620403006 +0000
@@ -30,6 +30,7 @@
   build:
     name: Build and test the wheel and sdist
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
@@ -61,6 +62,7 @@
     name: Publish to ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'TestPyPI' || 'PyPI' }}
     needs: build
     runs-on: ubuntu-latest
+    timeout-minutes: 15
     environment: ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'testpypi' || 'pypi' }}
     permissions:
       id-token: write
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:02.808446995 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:14:12.620539862 +0000
@@ -4,6 +4,7 @@
 jobs:
   release:
     runs-on: ubuntu-latest
+    timeout-minutes: 60
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:02.807309044 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:12.620690208 +0000
@@ -13,6 +13,7 @@
 jobs:
   security:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:02.807611222 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:12.620911996 +0000
@@ -29,6 +29,7 @@
           - {name: macos-x86_64, os: macos-14, target: x86_64-apple-darwin, kind: cross}
           - {name: windows-x64, os: windows-latest, target: x64, kind: native}
     runs-on: ${{ matrix.os }}
+    timeout-minutes: 45
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
@@ -71,6 +72,7 @@
     # the x86_64 macOS wheel is cross-compiled, so install it on a real Intel runner
     needs: wheel
     runs-on: macos-15-intel
+    timeout-minutes: 20
     strategy:
       fail-fast: false
       matrix: {python: ['3.11', '3.14']}
@@ -85,6 +87,7 @@
           SHAPE_KERNEL=rust python -c "import shape; print(shape._kernel.version()); from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
   sdist:
     runs-on: ubuntu-latest
+    timeout-minutes: 30
     steps:
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b  # v1.51.0
```

#### D3: cancel superseded runs on pull requests only (#265; finding 5)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:14:46.186536297 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:14:46.204957151 +0000
@@ -8,7 +8,8 @@
   workflow_dispatch:
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 permissions: {contents: read}
 jobs:
   test:
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:14:12.620690208 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:14:46.218378644 +0000
@@ -8,7 +8,8 @@
   workflow_dispatch:
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 permissions: {contents: read}
 jobs:
   security:
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:14:12.620911996 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:14:46.230960408 +0000
@@ -12,7 +12,8 @@
 permissions: {contents: read}
 concurrency:
   group: ${{ github.workflow }}-${{ github.ref }}
-  cancel-in-progress: true
+  # cancel superseded PR runs only: every push to an integration branch gets a full result
+  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
 jobs:
   wheel:
     name: wheel (${{ matrix.name }})
```

#### D4: release candidate SBOM of the installed wheel, no OIDC token, domains installed; publish to PyPI only from `main` or a tag, and check the published wheel (#266; findings 6, 7)

```diff
diff -ruN a/.github/workflows/publish.yml b/.github/workflows/publish.yml
--- a/.github/workflows/publish.yml	2026-10-03 13:14:46.194043825 +0000
+++ b/.github/workflows/publish.yml	2026-10-03 13:15:25.602779521 +0000
@@ -47,6 +47,10 @@
         run: grep -qi "early access" README.md
       - name: Build the pure wheel, install it in a clean venv and run tests/demo/core
         run: python scripts/build_pure_wheel.py --verify --python python3.11
+      - name: The published wheel passes the user-facing and shipped-data checks (D-13, W6-02)
+        run: |
+          python scripts/check_user_facing.py --wheel dist/*.whl
+          python scripts/check_shipped_data.py --wheel dist/*.whl
       - name: Build the sdist
         run: python -m build --sdist
       - name: twine check
@@ -61,6 +65,10 @@
   publish:
     name: Publish to ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'TestPyPI' || 'PyPI' }}
     needs: build
+    # PyPI only from main or a v* tag; TestPyPI from any branch
+    if: >-
+      (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi')
+      || github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
     runs-on: ubuntu-latest
     timeout-minutes: 15
     environment: ${{ (github.event_name == 'workflow_dispatch' && inputs.repository == 'testpypi') && 'testpypi' || 'pypi' }}
diff -ruN a/.github/workflows/release.yml b/.github/workflows/release.yml
--- a/.github/workflows/release.yml	2026-10-03 13:14:46.194060821 +0000
+++ b/.github/workflows/release.yml	2026-10-03 13:15:25.602615914 +0000
@@ -1,6 +1,7 @@
 name: Release candidate
 on: {workflow_dispatch: {}}
-permissions: {contents: read, id-token: write}
+# nothing here signs or publishes, so no OIDC token (T-25 attestation belongs to the publish step)
+permissions: {contents: read}
 jobs:
   release:
     runs-on: ubuntu-latest
@@ -11,12 +12,19 @@
         with: {python-version: '3.13'}
       - run: python -m pip install --upgrade pip
       - run: pip install build twine pip-audit cyclonedx-bom
-      - run: pip install '.[dev]'
+      # the same install as `make bootstrap` and ci.yml: without shape-domains, 12 test modules skip
+      - run: pip install -e '.[dev]' -e plugins/shape-domains
       - run: make check
       - run: python -m build
       - run: python -m twine check dist/*
       - run: pip-audit
-      - run: cyclonedx-py environment -o sbom.json
+      # T-25: the SBOM of what a user installs (the wheel and its dependencies), not of this
+      # job's environment (pytest, mypy, build tools...)
+      - name: SBOM of a clean install of the wheel
+        run: |
+          python -m venv --without-pip "$RUNNER_TEMP/sbom-venv"
+          python -m pip --python "$RUNNER_TEMP/sbom-venv/bin/python" install dist/*.whl
+          cyclonedx-py environment --pyproject pyproject.toml -o sbom.json "$RUNNER_TEMP/sbom-venv/bin/python"
       - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with:
           name: release-candidate
```

#### D5: pip-audit every offline lock set (#267; finding 8)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:15:25.589314181 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:15:34.200836680 +0000
@@ -101,9 +101,21 @@
       - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09  # v5.1.0
       - uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1  # v6.3.0
         with: {python-version: '3.11'}
-      - run: python -m pip install -U pip uv packaging
+      - run: python -m pip install -U pip uv packaging pip-audit
       - run: python scripts/offline_lock.py generate "$RUNNER_TEMP/offline-lock"
       - run: python scripts/offline_lock.py check "$RUNNER_TEMP/offline-lock"
+      # every extra and plugin driver, at the exact versions the lock pins (the audit job covers
+      # only [dev,streaming])
+      - name: pip-audit every lock set
+        shell: bash
+        run: |
+          rc=0
+          for f in "$RUNNER_TEMP"/offline-lock/requirements-*.txt; do
+            echo "::group::$(basename "$f")"
+            pip-audit --no-deps --disable-pip --progress-spinner off -r "$f" || rc=1
+            echo "::endgroup::"
+          done
+          exit $rc
       - uses: actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f  # v6.0.0
         with: {name: offline-lock, path: '${{ runner.temp }}/offline-lock/requirements-*.txt'}
   plugin-skeletons:
```

#### D6: hygiene: duplicate test path, stale `lane/CI-FIX` triggers, `check_requirements.py` in CI (findings 15–17)

```diff
diff -ruN a/.github/workflows/ci.yml b/.github/workflows/ci.yml
--- a/.github/workflows/ci.yml	2026-10-03 13:15:34.200836680 +0000
+++ b/.github/workflows/ci.yml	2026-10-03 13:15:52.106716512 +0000
@@ -3,7 +3,7 @@
   # Full CI runs on the integration branches; lane branches are verified locally by the lead
   # before they merge (the queue reached 179 runs when every lane push ran the full matrix).
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
   pull_request:
   workflow_dispatch:
 concurrency:
@@ -44,6 +44,8 @@
       - run: vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
       - run: lint-imports
       - run: python scripts/check_conformance_coverage.py
+      # `make check` runs it; CI did not, so an unknown SHAPE-*-NNN reference went unnoticed
+      - run: python scripts/check_requirements.py
       - run: python scripts/check_user_facing.py
       - run: python scripts/check_plugin_skeletons.py
       # The Fabric demo tests need Java, PySpark and unixODBC; they and the demo content tests
diff -ruN a/.github/workflows/nightly.yml b/.github/workflows/nightly.yml
--- a/.github/workflows/nightly.yml	2026-10-03 13:15:34.190290570 +0000
+++ b/.github/workflows/nightly.yml	2026-10-03 13:15:52.106276212 +0000
@@ -77,7 +77,7 @@
         with: {python-version: '3.11'}
       - run: pip install -e '.[dev,azure]' azure-storage-blob
       - run: docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite
-      - run: python -m pytest -q -m emulator tests/builtins/test_cloud_sources_azurite.py tests/builtins/test_abfss_sink_azurite.py tests/builtins/test_abfss_sink_azurite.py
+      - run: python -m pytest -q -m emulator tests/builtins/test_cloud_sources_azurite.py tests/builtins/test_abfss_sink_azurite.py
       - if: always()
         run: docker compose -f ci/emulators/docker-compose.yml down -v
   kafka-e2e:
diff -ruN a/.github/workflows/security.yml b/.github/workflows/security.yml
--- a/.github/workflows/security.yml	2026-10-03 13:15:34.190481557 +0000
+++ b/.github/workflows/security.yml	2026-10-03 13:15:52.106100521 +0000
@@ -3,7 +3,7 @@
   # Full CI runs on the integration branches; lane branches are verified locally by the lead
   # before they merge (the queue reached 179 runs when every lane push ran the full matrix).
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
   pull_request:
   workflow_dispatch:
 concurrency:
diff -ruN a/.github/workflows/wheels.yml b/.github/workflows/wheels.yml
--- a/.github/workflows/wheels.yml	2026-10-03 13:15:34.190498826 +0000
+++ b/.github/workflows/wheels.yml	2026-10-03 13:15:52.106181690 +0000
@@ -4,7 +4,7 @@
 # Nothing is published from here (release is P8-04, by the owner).
 on:
   push:
-    branches: [main, build/main-plan, lane/CI-FIX]
+    branches: [main, build/main-plan]
     paths: ['rust/**', 'pyproject.toml', 'src/shape/_kernel.pyi', '.github/workflows/wheels.yml']
   pull_request:
     paths: ['rust/**', 'pyproject.toml', 'src/shape/_kernel.pyi', '.github/workflows/wheels.yml']
```

#### D7: test change that D1 needs (`tests/demo/core/test_publish_workflow.py`)

Keeps the intent (two trusted-publishing steps) and adds that they are SHA-pinned.

```diff
diff --git a/tests/demo/core/test_publish_workflow.py b/tests/demo/core/test_publish_workflow.py
index 643592c..1fed0f7 100644
--- a/tests/demo/core/test_publish_workflow.py
+++ b/tests/demo/core/test_publish_workflow.py
@@ -50,7 +50,8 @@ def test_environments_and_permissions():
 
 def test_uses_trusted_publishing_and_no_secrets():
     body = code_lines()
-    assert body.count("pypa/gh-action-pypi-publish@release/v1") == 2
+    # pinned to a full commit SHA (AUD-ci, #265), never a branch or tag
+    assert len(re.findall(r"pypa/gh-action-pypi-publish@[0-9a-f]{40}\b", body)) == 2
     assert "https://test.pypi.org/legacy/" in body
     lowered = body.lower()
     assert "secrets." not in lowered
```

#### D8: Dependabot for actions, a real CODEOWNERS, SECURITY.md matches D-09 (#265, #268; findings 12, 13, 18)

`sqllocks` is the repository owner's GitHub login (user id 29076762).

```diff
diff --git a/.github/dependabot.yml b/.github/dependabot.yml
new file mode 100644
index 0000000..b8d0caf
--- /dev/null
+++ b/.github/dependabot.yml
@@ -0,0 +1,8 @@
+# Keeps the SHA-pinned actions current (AUD-ci, #265): one grouped weekly PR.
+version: 2
+updates:
+  - package-ecosystem: github-actions
+    directory: /
+    schedule: {interval: weekly}
+    groups:
+      actions: {patterns: ["*"]}
diff --git a/CODEOWNERS b/CODEOWNERS
index c2f6084..e84f439 100644
--- a/CODEOWNERS
+++ b/CODEOWNERS
@@ -1,4 +1,8 @@
-# Replace placeholder after GitHub repository creation.
-/docs/PRODUCT_ARCHITECTURE.md @OWNER
-/docs/SECURITY_SPECIFICATION.md @OWNER
-/security/ @OWNER
+# Review is requested from the maintainer for every change; the release and CI paths are named
+# explicitly so that a later narrowing of `*` cannot drop them.
+* @sqllocks
+/.github/ @sqllocks
+/scripts/ @sqllocks
+/pyproject.toml @sqllocks
+/SECURITY.md @sqllocks
+/security/ @sqllocks
diff --git a/SECURITY.md b/SECURITY.md
index 98b0703..6e88ef0 100644
--- a/SECURITY.md
+++ b/SECURITY.md
@@ -7,5 +7,6 @@ PII or customer datasets in reports.
 
 Shape is in early access; only the latest release receives fixes.
 
-Shape treats artifacts, packs, plugins, connector responses and reference assets as
-untrusted inputs. See `docs/THREAT_MODEL.md`.
+Shape treats artifacts, packs, connector responses and reference assets as untrusted
+inputs. Plugins are trusted, in-process code that you choose to install: Shape does
+not sandbox them. See `docs/THREAT_MODEL.md`.
```

#### D9: the `test` job installs `shape-fabric`, which the `demo_cmd` tests need (finding 27)

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -29,7 +29,8 @@
       - uses: actions/setup-python@v5
         with: {python-version: '${{ matrix.python }}', allow-prereleases: true}
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains
+      # tests/demo_cmd writes the semantic model through shape-fabric
+      - run: pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains -e plugins/shape-fabric
       # T-27 scope: src tests plugins benchmarks/vs_refengine rust (only the paths that exist yet).
       - run: ruff check src tests plugins benchmarks/vs_refengine
       - run: ruff format --check src tests plugins benchmarks/vs_refengine
```

The `zero-network` job (`ci.yml:81`) has the same install line, but its `-m "zero_network and not
heavy"` selection does not include these tests, so it is left as is. D1 changes the `uses:` lines
above this hunk, not this one: the two diffs apply in either order.

