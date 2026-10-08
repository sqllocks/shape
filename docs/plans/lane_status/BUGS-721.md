# BUGS-721 — Fabric UDF results withhold raw values of classified columns (lane/BUGS-721, issue #721)

Status: **built; awaiting lead verification.** Branched from `int/INT-18` (af57fb8a). Owner decision
(2026-10-04): withhold by default. No gate, tolerance, D-xx or T-xx decision changed; no test skipped,
xfailed or weakened; §11 and §2.3 not edited; no `.github/workflows` edit; `$REFENGINE_ROOT` only built
by `setup_refengine.sh` and read. No PR. The issue was not commented on or closed.

## What changed

| # | Asked | Where |
|---|---|---|
| 1 | UDF results withhold raw `min`/`max` of classified columns; check violations and diff changes carry no raw values of them | `src/shape/integrations/fabric/udf.py`: `_profile_result` applies the bridge's `redact_summary(summary, classified_columns(profile))`; `check_profile` applies `redact_entries(violations, classified_columns(profile))`; `diff_profiles` applies `redact_entries(changes, classified(baseline) \| classified(current))`. Same helpers as the bridge's `profile`/`check`/`diff` (`shape.bridge.handlers.flow`, stdlib-only imports, so the pure-wheel UDF path is unaffected), so the rule is `pii_gate_fires` and the output is exactly the bridge's: `null` plus `"redacted": true`. Redaction happens before `fail_on_violation` / `fail_on_drift` raise, so the `UserThrownError` properties are redacted too (its message only names columns and rules). |
| 2 | Optional opt-in parameter | `include_raw_values: bool = False` on `profile_lakehouse_file`, `profile_lakehouse_table`, `check_profile`, `diff_profiles`, `profile_data_frame` (the bridge's opt-in is `options.include_raw_values`); `includeRawValues: bool = False` added **last** to each of the five bindings in `integrations/fabric/udf/function_app.py`, so every existing positional or named call keeps working. `generateSample` unchanged. |
| 3 | Unclassified columns unchanged | pinned by tests (below) |
| 4 | Docs | `function_app.py` and `udf.py` docstrings (the UDF reference), `integrations/fabric/RUNBOOK.md` §6 (new paragraph) and §8 (parameter row), `CHANGELOG.md` ("Changed (#721): ... withhold ... by default"). `demo/TALK.md` **not changed**: no beat shows the min/max of a classified column (see below). |

## Tests (fail before, pass after)

Run with the implementation stashed: 8 failed (the new/updated tests below except the two pins, which
pass both ways by design); with it: all pass.

* `tests/integrations/test_fabric_udf_helpers.py` (pure helpers, CSV/Parquet/table/inline on an
  `email`/`ssn` dataset as in the issue): `test_profiles_withhold_min_and_max_of_classified_columns_by_default`
  (also: no raw email/SSN string anywhere in the JSON), `test_profiles_return_raw_values_when_asked`,
  `test_unclassified_columns_are_unchanged` (unclassified columns equal `shape.profile(...).summary()`;
  a classified column differs only in `min`, `max`, `redacted`),
  `test_check_violations_carry_no_raw_value_of_classified_columns` (result and raised error; `amount`
  and `status` keep `observed`), `test_diff_changes_carry_no_raw_value_of_classified_columns`.
* `tests/demo/fabric/test_udf.py` (through the real `fabric-user-data-functions` bindings):
  `test_signatures_match_plan` now includes `includeRawValues: False` on the five functions;
  `test_the_fixture_columns_the_gate_classifies` (pin: `email`, `customer_id`, `amount`),
  `test_profiles_withhold_classified_min_max_unless_include_raw_values`,
  `test_check_and_diff_withhold_classified_values_unless_include_raw_values`,
  `test_pipeline_b_udf_gate_still_fails_on_day2_for_the_same_reason` (pin; drives the two activities
  with the parameters in `shape_gate_udf.DataPipeline/pipeline-content.json`).
* `tests/demo/content/test_demo_data.py::test_udf_gate_on_day2_fails_for_the_documented_reason`
  (pin, on the real medium-scale demo data, `customers`/`orders`/`products`).

## Demo pipeline (b) and beat 7

On the real demo data (`demo/make_data.py --scale medium`, seed 42) the gate classifies only
`orders.order_id` (and nothing the contract checks). `shape_gate_udf` (profileLakehouseFile, then
checkProfile with `failOnViolation`): day 1 passes; **day 2 still fails with
`Contract check failed with 2 violation(s): status: allowed_values; order_total: max`**, and the
violations are identical to the raw ones (`status` observed `{"unexpected_values": ["lost"]}`,
`order_total` observed 7189.882). `customers` (if run): `email: max_null_rate` still fails, and its
`observed` null rate is now withheld because `email` is classified (`includeRawValues = true` shows
it). On the fixture data of `tests/demo/fabric` the day-2 failure is `email: max_null_rate; status:
allowed_values`, unchanged. The gate classifies the fixture's `customer_id` and `amount` too (nearly
every value distinct), so their `min`/`max` are withheld in the fixture's UDF summaries.

## Checks run in this session

Environment: Python 3.11.15, venv per §1.1 (`pip install -e '.[dev,streaming,advanced]'` plus the
plugins CI installs: domains, sqlserver, eventhubs, fabric; also kafka and dbt, needed by
`tests/cli/test_dry_run.py` and `tests/demo/fabric/test_dbt.py`), `tests/demo/fabric/requirements.txt`,
`unixodbc` (needed by `fabric-user-data-functions`), pinned RefEngine via `setup_refengine.sh` (needed by
`tests/generation/test_composite_p601e.py`). Private `TMPDIR` under the session scratchpad on every run.

**Final tree (80e164c7, after the last merge of `origin/int/INT-18`; 80e164c7 = this lane + int/INT-18
at 6594c830):**

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check` (same paths) | 1708 files already formatted |
| `mypy` | no issues in 633 source files |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `pytest -m "not emulator and not live"` (Rust kernel) | **13969 passed**, 23 skipped, 0 failed |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"` | **13925 passed**, 23 skipped, 0 failed |
| `SHAPE_KERNEL=python pytest -m heavy tests/joint tests/streaming/emit` | 2 passed |

The Python-kernel run was split (heavy / not heavy) because one run exceeds the 2-hour limit of a
background command. The Python-kernel `heavy` tests in `tests/kernel`, `tests/profile` and
`tests/streaming` (the ~1 h memory tests) were run on f4d64e55 (44 passed, 1 h 38 min); the last
merge changed no file under `src/shape/{kernel,profile,streaming,io}`, `rust/` or those test folders,
so they were not repeated; the heavy tests of `tests/joint` and `tests/streaming/emit`, which generate
data (the merge changed generation), were.

**Earlier tree (f4d64e55, after the first merge):** Rust kernel 14334 passed, 2 failed; Python kernel
14290 + 44 heavy passed, 2 failed. The 2 failures were the `compat[list]` vectors below (also on
af57fb8a with this lane's source reverted); they pass on the final tree.

**Demo tests (requested):** `pytest tests/demo` 376 passed (369 in the full run plus the 7
`test_dbt.py` tests that needed `shape-dbt` installed, then 16/16); `pytest tests/demo/fabric`
**238 passed** (separate run). `make check` was also run once on the pre-merge tree: every static
step passed; its pytest step had only environment failures (plugins not installed, RefEngine not set up)
and the two `compat[list]` failures, all resolved or explained above.

## For the lead

1. **Plan text:** §12.6 DM-06 lists the five UDF signatures without `includeRawValues`. Lanes do not
   edit the plan; the lead may add the parameter there (owner decision 2026-10-04).
2. **Seen and gone:** `tests/bridge/test_compat_1_0.py` and `test_compat_1_1.py` `[list]` failed on
   `int/INT-18` at af57fb8a and fcbdb1a5 (recorded `list` vectors held the domain descriptions from
   before INT-16, 293cf7b0; reproduced with this lane's source reverted). They pass after the merge
   of 6594c830. Nothing to do unless they come back.
3. The merge commit 80e164c7 kept git's `# Conflicts: CHANGELOG.md` comment lines in its message
   (cosmetic; not amended, the branch is pushed). The conflict was two entries added at the top of
   "Unreleased"; both are kept.
