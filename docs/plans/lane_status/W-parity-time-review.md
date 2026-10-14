# W-parity-time review

Reviewer: independent (did not build this lane). Head reviewed: `4c7f93c8d6f25ce71d4684273e37f240fb88e7e8`
(matches the expected prefix), diff `origin/release/0.9.3...HEAD` (base `a8e476ca`).

## Verdict: APPROVE

`verify.py` skips exactly `dow_histogram` and `temporal_histogram`, exactly for
`edge/x_time_only.csv` and `edge/x_time_ampm.csv` (exact string match on the dataset prefix),
with a comment citing README lines 142-143 (which do state that time-only text is dated today).
`hour_histogram` and every other field keep their rule and tolerance; `RULES`, `TABLE_RULES`,
the product code and the recorded store are untouched (changed files: `verify.py`,
`tests/benchmarks/test_profile_verify_recorded.py`, the lane status file).

## Commands

| Command | Exit | Result |
| --- | --- | --- |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2150 files formatted |
| `mypy` | 0 | no issues in 701 files |
| `python scripts/check_release_workflows.py` | 0 | OK |
| `python -m pytest -p no:cov -q tests/benchmarks/test_profile_verify_recorded.py` | 0 | 23 passed |
| `python -m pytest -p no:cov -q tests/benchmarks benchmarks/vs_refengine/tests tests/profile/test_dtparse.py` | 0 | 539 passed, 5 skipped (sklearn absent) |
| Base `verify.py` restored, lane tests run | 1 | 6 failed, 17 passed (the new tests catch a revert) |
| Fixtures generated with `datasets.edge_deviations()` into a scratch `PROFILE_DATA_DIR`; lane `verify.py edge/x_time_only.csv edge/x_time_ampm.csv` on a Wednesday (UTC) | 0 | both PASS, `hour_histogram: 2/2`, `0 mismatches` |
| Same with base `verify.py` | 1 | `dow_histogram: 1/2` on both, `2 mismatches` (the nightly failure reproduced) |

The new test pins `dtparse._midnight` to Thursday 2027-11-04 (different weekday, month and
year), asserts both skipped fields really differ from the recording, expects 0 mismatches, and
with `hour_histogram[0] += 2e-9` expects exit 1 and exactly one `.hour_histogram` mismatch. A
second test proves both fields are still compared for `edge/x_dates_a.csv` and for near-miss
names (`x_time_only.csv`, `other/x_time_ampm.csv`); a third perturbs every other field on both
time-only fixtures and expects one mismatch each. The input-digest check still runs.

## Findings

None blocking.

1. Non-blocking, `benchmarks/vs_refengine/profile_1to1/verify.py:224`: skipped fields disappear
   from the printed matrix rather than appearing as "skipped", so a reader of the parity artifact
   cannot see that they were intentionally not compared. Optional: print a
   `  dow_histogram: skipped (README 142-143)` line for those two fixtures.
2. Non-blocking, `tests/benchmarks/test_profile_verify_recorded.py:178`: the conditional
   expression `assert "..." in output if not change_hour else "..." in output` is correct but easy
   to misread; an explicit `if/else` would be clearer.
