# G7-eval: Gate G7 evidence (lane/G7-eval, from int/INT-18)

Status: **G7 NOT MET. Two of the three checks miss.** The gate was not marked. §11 and §2.3 were
not edited (the lead does that). No gate, tolerance, harness or D-xx/T-xx decision was changed.

- Base: `int/INT-18` at `f94de92`.
- Machine: 4 vCPU, Python 3.11.15, Rust 1.97.0, pinned RefEngine `422e78d` (§1.2, unmodified).
- Evidence: `docs/plans/evidence/G7/`.

| G7 check | Result | Evidence |
|---|---|---|
| SEC3 and P19 have regression tests | **PASS** | `sec3_p19_rust.txt`, `sec3_p19_python.txt` (32 passed each, 0 skipped); `fail_before_SEC3.txt`, `fail_before_P19.txt`; new table test `tests/regressions/test_phase7_bugs.py` |
| Safe-profile parity passes | **MISS** (already failing on INT-18, not caused by this lane) | `safe_profile_parity_rust.txt`, `safe_profile_parity_python.txt` (exit 1, both kernels) |
| No high-severity findings are open | **MISS** | `open_security_issues.md`: 0 open issues by label (the labels do not exist); 11 high by content still open after 6 were fixed here; bandit's high finding fixed here (`bandit_before.txt`, `bandit_after.txt`) |

## 1. SEC3 and P19 regression tests

Appendix A defines the two bugs:
- **SEC3**: `privacy/policy.py:6` vs `privacy/classification.py:9`, two inconsistent
  classification taxonomies. Fixed in P7-01, `b04bf32`.
- **P19**: `.shape` authenticity is checksum-only, and `secure.py` is unused. Fixed in P7-03,
  `06300e7`.

The regression tests:

| Bug | Tests |
|---|---|
| SEC3 | `tests/privacy/test_taxonomy.py`: 5 test functions, 11 cases |
| P19 | `tests/artifact/test_signing.py`, which includes `test_forged_artifact_with_rewritten_hashes_fails_verify` (the P7-03 acceptance), `test_forged_and_stripped_signature_fails` and `test_attacker_resigns_with_own_key_fails_trusted_key` |

- **Both kernels:** `SHAPE_KERNEL=rust` and `SHAPE_KERNEL=python` each give 32 passed, 0 skipped,
  exit 0. That count includes the new table test.
- **They fail before the fix.** The current test files were run against a worktree at the commit
  before each fix (`PYTHONPATH=src`, `SHAPE_KERNEL=python`):
  - SEC3 at `b04bf32^`: 11 failed.
  - P19 at `06300e7^`: collection error, `ImportError: cannot import name 'ArtifactSignatureError'`.
    Before the fix there was no signing to test.
- **Added:** `tests/regressions/test_phase7_bugs.py`, a G7 bug table in the same form as G1's
  `test_phase1_bugs.py`. It fails if SEC3 or P19 loses its named regression tests.

## 2. Safe-profile parity: MISS

Command (equivalence check; no timing was taken):
`source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/safe_profile_1to1/verify.py`
on D2. D2 was regenerated with `profile_1to1/datasets.py D2` (1,000,000 x 20).

**Result:** exit 1, `PARITY FAILED`, in both kernels.
- All 6 mapper variants fail, with 20 columns each (120 mismatches).
- Every mismatch is the same one: `keys differ by ['pattern_contains_rates', 'pattern_rates']`.
- The validator matches on all 28 artifacts.
- The output is byte-identical with this lane's source changes stashed, so the failure is in
  `int/INT-18` itself.

**Cause.** `20456436`, "ISS-profile: PII rates (#2)", added `pattern_rates` and
`pattern_contains_rates` to the profile and copied them into `SafeColumnProfile`
(`src/shape/privacy/safe_profile.py:320-321, 415-416`). The pinned baseline's safe profile has no
such keys. That commit updated `profile_1to1/verify.py` but not `safe_profile_1to1/verify.py`.

**Diagnostic only, not committed.** A scratch copy of the harness (outside the repo) added the two
keys to `ADDED_KEYS`, the harness's existing list of product-only keys that parity skips
(`cells_suppressed` is already there). With that copy:
- every variant passes;
- the validator passes;
- exit 0, `PARITY OK`.

So everything the baseline publishes still matches exactly; the only difference is the two added
keys.

**For the lead (escalation, §0.4).** Pick one; this lane did neither, because both change what the
gate's verifier or the product's output is:
- **(a) Accept the keys as product-only.** Add them to `ADDED_KEYS` in
  `benchmarks/vs_refengine/safe_profile_1to1/verify.py`, and add a test that pins their content in
  the safe profile, as was done for `cells_suppressed`. Exact diff:
  ```diff
  -ADDED_KEYS = {"cells_suppressed"}
  +ADDED_KEYS = {"cells_suppressed", "pattern_rates", "pattern_contains_rates"}
  ```
- **(b) Drop the keys from the safe profile.** `to_dict` would leave them out; the mapper still
  uses them internally for the PII gate (`_column_rates`).

  Privacy note: the rates are aggregate shares, but a rate such as 1e-6 for `ssn` reveals that a
  single SSN-shaped value exists. That would be an argument for (b), or for applying the minimum
  cohort to the rates.

## 3. Open high-severity findings: MISS

**Labels.** The repo has no `high`, `critical` or `security` label (`get_label`: not found), and
none of the 735 open issues carries a severity label. The label query returns 0, which would make
this check pass vacuously. So the open issues were triaged by content; 106 candidate bodies were
read. Rubric, ratings and the full table: `open_security_issues.md`.

**Fixed in this lane.** Six high issues. For each, the test was written first and fails with the
fix stashed: 9 failed without the fixes, 17 passed with them.

| # | Fix |
|---|---|
| #631 | The Spark router follows `continuationUri` and `Location` only on the Fabric API host (`shape.scale.http.on_fabric_api`). |
| #434 | The shape-fabric `FabricApi._await` does the same. |
| #579 | Plugin signatures refuse a RECORD path with a line break. |
| #683 | `share-bundle verify` refuses a data member whose suffix is not lower case. |
| #394 | `redact_sensitive` removes every `VALUE_KEYS` entry. |
| #650 | `release_for` withholds `joint` when a column is above the target, and treats `placeholders` as values. Partial: small cells inside `joint` for all-public profiles are not suppressed. |

**Static analysis.** `bandit -q -r src -ll`, the command `security.yml` runs, exited 1 on INT-18:
- High: B324, SHA-1 used for a DDL constraint-name suffix;
- Medium: B506, `yaml.load` with a `SafeLoader` subclass.

Neither is a security use. Fixed with `usedforsecurity=False` (a test pins the names, which are
unchanged) and with `# nosec B506` plus its reason, in the repo's existing `nosec` style. bandit
now exits 0.

**Still open: 11 high by content**, each with a proposed fix in `open_security_issues.md`:
- statement or formula injection: #724/#285 and #629;
- tokens to arbitrary hosts: #275 and #276;
- disclosure of classified values: #395, #533, #535, #663 and #721;
- secrets kept in tapes: #411.

Two of them need the lead or owner:
- **#395** changes the safe-profile output.
- **#721** changes a documented return value; the issue asks for an owner decision.

**Gate reading.** If "high-severity" means a severity label, the check passes vacuously, since
none exists. By content it misses until those 11 are fixed, or the lead or owner re-rates them.
The lead decides which reading applies.

## Checks run in this session

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | pass |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1571 files already formatted |
| `mypy` | no issues in 565 source files |
| `lint-imports` | 1 kept, 0 broken |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `python scripts/check_secrets.py` | OK |

Output: `static_checks.txt`.

`pytest -m "not emulator and not live"`, full suite, private TMPDIR:
- `SHAPE_KERNEL=rust`: 5 failed, 12011 passed, 23 skipped, exit 1 (`pytest_full_rust.txt`). The same 5
  fail on INT-18 without this lane: `tests/bridge/test_vectors.py` `report_card` and `report_card_read`
  (an extra fidelity tier's reason with the optional packages installed),
  `test_w1_06_spec_schema::test_the_shipped_file_is_what_the_builder_makes`,
  `test_trust_reach` (the shape-fabric plugin's `kerberos.py` starts a subprocess; CI's core job
  does not install that plugin) and `test_removed_modules[history]`. The last one is a real INT-18
  conflict: P0-04 deleted `shape.history`, and W3-03 (`fcda3a7`) added it back. Not fixed here (out
  of this lane's scope); for the lead.
- `SHAPE_KERNEL=python`: 5 failed, 12011 passed, 23 skipped, exit 1, in 3 h 04 min (`pytest_full_python.txt`).
  These are the same 5 failures as the rust kernel. The run includes the `heavy` tests, which CI
  never runs under the python kernel; the 48M-row bounded-memory test alone took over an hour.

`plugins/shape-fabric/tests` (CI's plugin job markers): 5 failed, 916 passed. The same 5 fail on
INT-18 without this lane (5 failed, 915 passed): `test_lakehouse`, `test_publish` (#640),
`test_sql_write_path_cli` and two in `test_synapse`. None touches the changed code.
`plugin_shape_fabric.txt`.

Environment notes:
- `tests/demo/fabric` needs `tests/demo/fabric/requirements.txt` and unixODBC (§6.2.7); both were
  installed here.
- Installs used: `.[dev,streaming,advanced]` plus shape-domains, shape-kafka, shape-eventhubs,
  shape-sqlserver, shape-fabric and shape-dbt.

No Rust code changed. No workflow change is needed.
