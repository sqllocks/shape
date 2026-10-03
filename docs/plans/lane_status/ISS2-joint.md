# ISS2-joint — joint distributions and plausibility (issue #47)

Branch `lane/ISS2-joint`, built on the lead's integration tree (`abd78cb`) with `origin/lane/ISS-profile`
merged (conflicts in `cli/main.py`, `profile/reference/profile.py` and `sources.py` resolved by keeping
both sides: Delta `--version/--as-of` and the CSV options). Status: **built; the checks below were run in
this session** (the final table at the end says which and when). No gate, tolerance, decision (D-xx, T-xx)
or test was changed, skipped, xfailed or disabled; §11 and §2.3 are untouched; the baseline checkout was
only read.

Not done by this lane: no PR, no comment, label or close on the issue.

## Integration note (for the lead)

The brief says the tree holds the generation issue fixes. `origin/lane/ISS-gen` (#17-#19, the address
coherence fix) is **not** in this tree (`git merge-base --is-ancestor 7f8f1ea HEAD` is false), and merging it
conflicts with the CLI error-handling and `generate` changes already integrated (`cli/main.py`,
`cli/generation.py`: both sides rewrote `_main`, `--rows` and the global options). I aborted that merge and did
not resolve it. The generation work here does not edit `builtins/strategies/address.py`: it adds new
strategies (`hierarchy`, `hierarchy_field`, `conditional_table`) over reference datasets, so the two lanes do
not overlap. When ISS-gen is integrated, the `address` strategy's coherence (#18) and these strategies stack.

## Reproduction (before any change)

The issue's example, rebuilt from the shipped GeoNames US postal reference
(`plugins/shape-domains/.../us_zip_locations.arrow`, 40,977 records): 4,000 random real
(city, state, zip) rows (`good.csv`), and a copy (`bad.csv`) with 8% of ZIPs replaced by `00000` (320 rows)
and 5% by the real ZIP of a different place (200 rows, drawn from the file's own ZIPs). The generator is
`tests/joint/conftest.py` (seed 47). Commands run on the tree before the lane's code:

| Command | Reported |
|---|---|
| `shape.diff(good, bad)` | `drifted=True`, one change: `{"column": "zip", "kind": "uniqueness_change", "baseline": 1.0, "current": 0.87525}`. (The issue saw `drifted=False`; the ISS-diff fix since added the uniqueness change.) No dependency, no placeholder, no value named |
| `zip` column profile | `dtype=integer`, `pattern=None`, `cardinality=3670` (good: 4000), most frequent value `'0'` at 8.0% (the `00000`, read as an integer); nothing marks it as a placeholder |
| `shape fd bad.csv --determinant zip --dependent city` | confidence 0.87575, 178 violating groups of 3501 |
| `shape fd good.csv ...` | confidence 1.0 (4000 groups: every ZIP is unique in the sample) |
| `profile` | no dependency, key or association entry at all (only the numeric `correlation_matrix`) |

(The issue's 0.901 / 95 groups came from another random draw; the shape of the result is the same.)

## Requirements

| # | Requirement | State | Evidence |
|---|---|---|---|
| 1 | Placeholder / sentinel detection in profiles, with share and evidence | **built** | `profile/joint/placeholders.py`; column `placeholders` (`value`, `kind`, `share`, `count`, `share_of_non_null`, `next_share`, `evidence`). Listed sentinels (zeros, 9s, repeated digits, dates, text literals) and a spike rule for high-cardinality columns; reads the profile's value counts, so no extra pass. `tests/joint/test_placeholders.py` (18 tests). On the example: `zip` `'0'` kind `spike`, 8.0%, "107x as frequent as the next" |
| 2a | Approximate FDs and key discovery inside `profile`, every input, bounded | **built** | `profile/joint/analyze.py` (+ `measures.py`), table `joint.dependencies` / `joint.keys`; same numbers as `shape fd` (test compares with `functional_dependency`); runs for CSV, Parquet, Arrow, DataFrame and dicts of tables (all go through `_profile_cols_table` / `profile_dataset_columns`); sample of at most 20,000 rows, 16 columns per role, at most 240 dependency pairs, 120 key pairs; a 80-column 40,000-row table test asserts the caps. `shape fd` / `shape key` already read CSV, Parquet and JSONL (`shape.io.iter_rows`) and are unchanged |
| 2b | Association measures per type pair | **built** | `joint.associations`: Pearson, Spearman, Kendall (numeric); Cramér's V (bias-corrected), Theil's U both ways, normalised MI (categorical); correlation ratio (categorical–numeric); mutual information for all. Known-value tests in `tests/joint/test_profile_joint.py` |
| 2c | Conditional probability tables for categorical pairs | **built** | `joint.conditionals` (pairs with V ≥ 0.25 and at most 30 levels each; top 8 values per row; at most 10 tables) |
| 3a | `diff` reports a broken dependency (confidence drop, violating groups) and a placeholder surge, naming the dependency and the value | **built** | `drift/joint.py`, kinds `dependency_broken` (high), `placeholder_surge`, `implausible_rate_change`, `association_shift`, `reference_match_change`; thresholds `dependency_confidence` 0.02 (never below 3 standard errors), `placeholder_share` 0.01, `implausible_rate` 0.02, `association_shift` 0.2, `reference_match_rate` 0.02; `docs/DRIFT.md`. Acceptance output below |
| 3b | Contract rules `fd`, `implies`, `reference_pair`, `max_implausible_rate`, `no_placeholder` | **built, v1 is not broken** | `contracts/joint.py`, `contracts/v1.py`. See "Contract format v1" below |
| 4a | Hierarchical sampling state → county → city → ZIP → coordinates; 0 mismatches in 2,000 rows | **built** | `generation/hierarchy.py` (`HierarchicalSampler`, `hierarchy_violations`), strategies `hierarchy` / `hierarchy_field` (engine integrated, row addressed, chunk independent). `test_a_generated_table_keeps_every_zip_inside_its_city`: 2,000 rows through `shape.generate`, 0 violations (and a shuffled-ZIP control gives > 1,500). The shipped reference has state, city, ZIP, lat, lng; a `county` level works for any dataset that has the field |
| 4b | Categorical joint tables | **built** | strategy `conditional_table`, written by `fit_schema` from the profile's conditional tables (`shape generate --from`, `shape plan`); on a 6,000-row dept/ward table the pair's total variation distance is 0.008 (0.63 without it) |
| 4c | Per-row plausibility score (NLL under a learned joint model) and a report of impossible combinations | **built** | `generation/joint_model.py`: `fit_joint` (Chow-Liu tree, smoothed tables, numbers binned), `score`, `report`, `sample`. On the example a model fitted on `good` flags 13.0% of `bad` (520 rows corrupted = 13.0%), 5.0% as impossible combinations (the wrong-place ZIPs), 0.025% of `good` itself |
| 4d | Joint fidelity check (TVD/Hellinger on column pairs) | **built** | `joint_fidelity` (`generation/joint_model.py`) |
| 5 | Mixed-type copulas | existing / follow-up | The Gaussian copula over numeric columns (`generation/joint.py`, `fit_schema`) is unchanged; `fit_joint` handles mixed types by binning. A rank-correlation copula for mixed types is a follow-up |
| 6 | Reference-membership checks ("ZIP and city pair is in a reference table") | **built** | `profile(..., reference_pairs=)`, `shape profile --reference-pair`, rule `reference_pair`, drift `reference_match_change`. Domain packs (ZCTA, ICD, drug/procedure tables) are follow-ups: the mechanism takes any reference |
| 7 | Univariate depth: AIC/BIC selection, mixtures, zero inflation, heaping, Benford, tail index, seasonality | **follow-up** | Lower priority by the brief; only placeholders are built |
| 8 | Multivariate outliers (Mahalanobis, isolation forest), PCA, cohort clustering, vine copulas | **follow-up** | Lower priority by the brief |
| 9 | Business-rule constraint satisfaction (HARD/SOFT/LEARNED) | existing / follow-up | The engine's `business_rules` are unchanged |
| 10 | Multi-column determinants; dependencies for the stream profiler's windows | **follow-up** | Single-column determinants and two-column keys only; windows have no `joint` entry |

## Acceptance (the issue's)

`shape.diff(good, bad)` on the example now reports (from `tests/joint/test_diff_contract_joint.py`):

```
dependency_broken  zip -> state  1.000 -> 0.882 ...
dependency_broken  zip -> city   1.000 -> 0.876: "zip no longer determines city: confidence 1.000 ->
    0.876 (178 violating groups; worst: zip='0' maps to 307 city values); placeholder '0' in zip
    (8.0% of rows) (zip was unique, so the dependency held trivially)"
placeholder_surge  zip  0.0% -> 8.0%: "placeholder '0' (spike) ... 8.0% of rows"
implausible_rate_change  0.0 -> 0.08
```

and `shape.check(bad, contract)` with `fd` (zip → city ≥ 0.99), `max_implausible_rate` 0.02 and
`no_placeholder` on `zip` fails all three with the numbers, while `good` passes. A generated table of the
same shape (state/city/zip/lat/lng from the reference) has 0 ZIP-outside-city mismatches in 2,000 rows, and
`shape.diff(real, generated)` reports no `dependency_broken` or `placeholder_surge`.

Note on the placeholder's name: the CSV column is read as an integer (the identifier-column issue), so the
value is reported as `'0'`; the `00000` text is lost before profiling. With a text column it is `'00000'`
(`kind: zeros`).

## Contract format v1 (§12.3)

§12.3 says "every rule is optional" and does not close the rule set; the rules already added after the
sample (`min_true_rate`, `max_true_rate`, the `drift` object) set the precedent. The new rules are new
optional keys: **every contract valid under the earlier readers is read unchanged** (test
`test_v1_contracts_without_the_new_rules_behave_as_before`, and the whole `tests/contracts` suite passes), and
unknown keys are still rejected. The one asymmetry is the reverse direction: an *older* reader given a
contract that uses a new rule raises `ContractError: unknown rules` (fail closed) rather than ignoring it,
which is the intended behaviour (ignoring a rule would pass data it never tested). I therefore treated this as
"allows new optional rules without breaking v1 readers" and did not stop. **If the owner reads "format is final
for 1.0" as "no new keys at all", the alternative is a v1.1 with a version field and these rules gated behind
it; nothing here depends on the choice except the key names.** Not a D-xx/T-xx change.

## Design decisions made (inside the lane's remit)

- Placeholders reuse the profile's value counts (top 500 per column); a unique key determinant is not listed
  in `dependencies`, and `diff` infers confidence 1 for it (so `zip -> city` is named even though the good
  file's ZIPs were all distinct).
- The profile stores dependencies at confidence ≥ 0.8; a rule below that reports "not measured" or
  "below 0.8", never a pass.
- `implausible_rate` is the share of rows in the exceptions of a near-exact dependency (0.95 ≤ confidence < 1)
  or holding a placeholder, judged on the analysed sample; wrong-place ZIPs that are unique values are caught by
  `reference_pair` and by the learned joint model, not by this number (documented in `docs/JOINT.md`).
- New profile fields are additive and outside T-22's compared field lists (`COLUMN_RULES` / `TABLE_RULES` in
  `verify.py`), so no allow-list entry was needed; `SHAPE_PROFILE_JOINT=0` switches the table analysis off.
- `joint.py` (numeric copula) is untouched (it is in the mypy ratchet); the new model is `joint_model.py`
  (strict).

## Performance: profiling speed (G1 workloads) — **needs the owner's decision**

The joint analysis is bounded (a table of at most 20,000 rows is analysed whole; a larger one on a
5,000-row sample, 10 columns per role, 40 dependency pairs) and runs on its own thread beside the column
work (a dataset's tables: after the column pools, for fork safety). Measured on this 4-vCPU container
(load about 1.4, so noisier than the baseline machine), `shape.profile` wall time, fresh process, median of 5,
interleaved, `SHAPE_PROFILE_JOINT=0` (before) against the default (after); script
`scratchpad/ab.py`, outputs in `scratchpad/checks/ab.txt`:

| Workload | before | after | after/before |
|---|---|---|---|
| D1 csv (200k x 6) | 0.180 s | 0.208 s (first run, 8,000-row budget) / 0.150 s (final budget) | 1.15 / 0.82 (noise is about 15%) |
| D2 csv (1M x 20) | 2.665 s | 2.856 s / 2.242 s | 1.07 / 0.97 |
| D3 parquet (5M x 10) | 6.283 s | 6.151 s | 0.98 |
| D4 csv (100k x 200) | 4.581 s | 4.511 s | 0.99 |
| MT (3 tables) | 0.232 s | 0.315 s / 0.269 s | 1.36 / 1.22 |

The single-table workloads are within the measurement noise. **The multi-table workload (MT) costs about
+50 ms (+22% to +36%)**, and a small table such as D1 costs 20 to 30 ms of CPU, which on a 180 ms profile is
at the edge of the noise. PROF-IN MT was already below the 10x gate before this lane (5.4x, G1 escalation of
2026-10-01), D1 csv was 10.3x (so any real cost on it can move it under 10x). I did not change any gate or
tolerance and did not make the analysis opt-in, because the issue asks for it inside `profile`. Options for the
owner: (a) accept it (the joint entry is the issue's deliverable); (b) make the joint analysis off by default
for dataset (multi-table) profiles, or lazy (computed when a profile is saved or diffed, not in the timed
call); (c) set `SHAPE_PROFILE_JOINT=0` in the G1 timing runs. T-22 parity (below) is unaffected. The bench
harness itself (`bench.py`) was started twice but killed by two container restarts, so the official
`bench.py` table was **not** re-run; the numbers above are from the shorter script and are not a PROF-IN
gate measurement.

## Checks (run in this session, on the final tree unless noted)

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` (src tests plugins benchmarks/vs_spindle) | pass |
| `mypy` (strict, 358 files) | pass |
| vulture (`src/shape scripts/vulture_whitelist.py --min-confidence 80`) | clean |
| `lint-imports` | 1 contract kept, 0 broken |
| `scripts/check_user_facing.py` | clean |
| `bandit -q -r src -ll` | no findings (only the existing `nosec` warnings) |
| cargo fmt/clippy/test | not run: no Rust change |
| START (`shape --version`, median of 10) | 74 ms (gate 300 ms) |
| Parity T-22, `profile_1to1/verify.py --impl shape`, both kernels | 49/49 PASS, exit 0 for `SHAPE_KERNEL=python` and `rust` (run on the merged tree before the final budget change, which touches only the additive `joint` entry; `test_the_new_fields_are_the_only_difference_they_make` pins that) |
| Suite, `SHAPE_KERNEL=python` (`-m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, with `.[advanced]` and `-e plugins/shape-domains`) | 5,375 passed (before the last budget change) |
| Suite, `SHAPE_KERNEL=rust` | 5,376 passed (before the last budget change) |
| After the last budget change: `tests/profile diff contracts generation privacy plugins joint` | 2,169 + 76 passed |
| `pytest -m heavy --ignore=tests/demo/fabric` | 42 passed (`tests/demo/fabric` needs `nbformat`, which this environment lacks, so it is ignored as in the suite command) |
| Strategy baselines / `domain_1to1/verify.py --domain retail` | not run separately: no existing strategy, the engine's column draw or the retail domain was changed (new strategies only; the generation suite, which holds the strategy tests, passed) |
| New tests | `tests/joint/` (76 tests): placeholders, joint analysis, bounds, diff, contracts, reference pairs, hierarchy, `conditional_table`, Chow-Liu model, fidelity, privacy |

Notes: a stray empty file `/x` exists at the container root from a mistyped redirect of mine; removing it was
blocked by the safety check, and it is outside the repository. A first draft of `hierarchy` strategies
overwrote `builtins/strategies/hierarchy.py` (`self_referencing`); it was restored from the integration tree
and the new strategies live in `reference_hierarchy.py` (caught by `tests/generation`).

## Round 2 (issue #47)

Merged `origin/build/main-plan` (`ce8fe11`) into `lane/ISS2-joint` with a merge commit (no rebase, no
force-push). Conflicts resolved by keeping both sides: `CHANGELOG.md`; `cli/main.py` (the new `--auth`
and workbook options beside `--reference-pair`, plus `--joint`); `profile/reference/profile.py` and
`sources.py` (workbook profiling beside `reference_pairs`). `docs/plans/COMPLETION_PLAN.md` took the
incoming file unchanged (lanes do not edit it). Merge side effect fixed: the spec-key check from ISS-gen
(`generation/spec_keys.py`) did not list my strategies' keys, so `conditional_table`, `hierarchy` and
`hierarchy_field` are now listed there.

### Decision (1): joint analysis by default

| Profile | Default | On | Off |
|---|---|---|---|
| One table | on | `joint=True`, `--joint` | `joint=False`, `--no-joint` |
| Dataset (dict of tables, `--dataset`, workbook) | off | `joint=True`, `--joint` | `joint=False`, `--no-joint` |

Precedence: the argument, then `SHAPE_PROFILE_JOINT` (`0`/`false`/`no` off, any other non-empty value on,
so `1` also turns it on for a dataset), then the default above. `SHAPE_PROFILE_JOINT=0` works as before
for single tables. A one-entry dict is a dataset. Documented in `docs/JOINT.md`,
`docs/PROFILING_NOTES.md`, `CHANGELOG.md` and the `--joint` help. Tests: `tests/joint/test_joint_default.py`
(Python API single/dataset x default/True/False, environment values, argument over environment, empty
variable, dataset entry differs only by `joint`, CLI single and dataset with and without the flags and
the variable). Two existing tests that profile a dataset and read its `joint` entry now pass
`joint=True` (`test_the_rules_work_per_table_in_a_dataset`,
`test_every_input_kind_gets_the_same_joint_analysis`); no assertion changed. A contract rule that needs
the entry reports "not measured" on a dataset profiled without it, never a pass.

### Decision (6)

Accepted as built: new optional contract rules are additive keys in v1 and an older reader refuses an
unknown rule. No change.

### Performance re-measurement (G1 PROF-IN workloads; a record, not a gate run)

Script `scratchpad/ab.py` (same method as round 1: fresh process per run, median of 5, interleaved).
"before" = `SHAPE_PROFILE_JOINT=0` (no joint analysis, the pre-joint cost); "after" = the default. Data
from `profile_1to1/datasets.py`, 4-vCPU container, load about 1.5, under `flock`. The numbers include
first-call import cost, so they are higher than round 1's and compare only within a row.

| Workload | before | after | after/before |
|---|---|---|---|
| D1 csv (200k x 6) | 0.520 s | 0.525 s | 1.01 |
| D2 csv (1M x 20) | 1.860 s | 2.120 s | 1.14 (noise is about 15%) |
| D3 parquet (5M x 10) | 4.335 s | 4.099 s | 0.95 |
| D4 csv (100k x 200) | 2.842 s | 2.845 s | 1.00 |
| MT (3 tables) | 0.558 s | 0.552 s | 0.99 |

The multi-table workload is back within noise of its pre-joint time (it no longer runs the analysis by
default; round 1 saw +22% to +36%). Single tables keep the analysis and stay within noise. Gates, timing
method and tolerances are unchanged; the official `bench.py` table was not re-run.

### Checks (this session, on the merged tree)

| Check | Result |
|---|---|
| `ruff check` / `ruff format --check` (src tests plugins benchmarks/vs_spindle) | pass |
| `mypy` | pass (365 files) |
| `scripts/check_user_facing.py` | clean |
| `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=python` | 5,611 passed |
| same, `SHAPE_KERNEL=rust` | 5,611 passed |
| `profile_1to1/verify.py --impl shape` (T-22), `SHAPE_KERNEL=python` / `rust` | 49/49 PASS, exit 0 each (pinned Spindle baseline cloned read-only in `$SPINDLE_ROOT`, not edited) |
| Not run | heavy tests, cargo (no Rust change), the other parity harnesses, `bench.py` |
