# AUD-chaos: audit-and-fix lane (chaos, fidelity, report)

Area: `src/shape/chaos/**`, `src/shape/fidelity/**`, `src/shape/report/**`, `tests/chaos/**`,
`tests/fidelity/**`, `tests/report/**` (new), `docs/CHAOS.md`, `docs/FIDELITY.md`,
`docs/FIDELITY_TIERS.md`. Branch `lane/AUD-chaos` from `origin/build/main-plan`.

## Phase 1: hunt

Read: the three packages, their docs, their tests, plan §2, §6, P4-09, P4-11, P6-02 and the
chaos and fidelity-tier parity harnesses. Starting coverage (`pytest tests/chaos tests/fidelity
--cov`): chaos 93 to 100%, fidelity 96 to 99%; report 96% (through `tests/demo/core`). 200 passed.

The six categories are bound to the baseline by the chaos parity harness (statistical, on finite
fixtures); every fix below changes behaviour only on inputs where the code crashed or was wrong,
and the harness is re-run after any change to `categories.py`.

### Findings

| # | Sev | Where | Reproduction | Expected | Actual |
|---|---|---|---|---|---|
| 1 | high | `chaos/groundtruth.py` `_orphan_keys` (~404) | parent `customer.customer_id` 1..20M step 100, child `order.customer_id` <= 100k, `orphan_keys=0.5@order.customer_id`, no `references` (the CLI with `--input` and no schema) | every orphan matches no parent row (CHAOS.md) | 11 of 500 "orphans" are real parent keys: the base is `max(9e6, 2 x child max)`, the parent is not consulted unless declared; the log is wrong |
| 2 | medium | `chaos/groundtruth.py` `_orphan_keys` | int32 FK whose max is 2e9 | a clear error or orphans that fit | `OverflowError: Python integer 4000215404 out of bounds for int32` |
| 3 | medium | `chaos/groundtruth.py` `_orphan_keys` | float FK holding `inf`; decimal FK; a date or boolean column named as the key | orphans, or an error naming the column and type | `OverflowError: cannot convert float infinity to integer`; `ArrowTypeError: Expected bytes, got a 'decimal.Decimal' object` |
| 4 | medium | `chaos/groundtruth.py` `_negative_amounts` (~476) | `negative_amounts=0.5@t.u` with `u` uint32 = 5 | a sign flip (or an error: unsigned cannot be negative) | `5` becomes `4294967291`, logged as the change; auto-picked uint columns too |
| 5 | medium | `chaos/groundtruth.py` `corrupt_tables` / `_columns` (~362) | `date_shift=0.5@t` where `t` has no date column | an error: "a corruption that cannot apply to what it is aimed at is an error, not a silent no-op" (CHAOS.md) | silently nothing, nothing in `applied` |
| 6 | medium | `fidelity/tier3.py` `psi_report`, `DriftMonitor._ks` | a float column holding `inf` on one side | drift scored, or "fails closed (`method: error`, drifted)" (FIDELITY_TIERS.md) | PSI is NaN, `is_drifted: false`, `drift_score: 1.0`: fails open |
| 7 | medium | `chaos/anomaly.py` `_out_of_range` (~88) | `inject_anomalies(batch{x: [1e308, 2, 3]}, fraction=1, seed=1, kinds=["out_of_range"])` | an out-of-range value | `OverflowError: high - low range exceeds valid bounds` (crashes the emitter) |
| 8 | medium | `chaos/categories.py` `ValueChaosMutator._out_of_range` (~356) | a float column holding `inf` or values > 1.8e305 | an out-of-range value | `OverflowError: high - low range exceeds valid bounds` (the baseline crashes too; parity fixtures are finite) |
| 9 | low | `chaos/groundtruth.py` `corrupt_tables` | `seed=-1`; `batch=-1` | an error naming the seed / batch | `ValueError: expected non-negative integer`; a negative batch silently changes nothing |
| 10 | low | `chaos/groundtruth.py` `Corruption.parse` (~150) | `duplicates@t:from=x`, `date_shift@t:days=1.5` | the error names the option | `invalid literal for int() with base 10: 'x'` |
| 11 | low | `chaos/groundtruth.py` `Corruption` | `duplicates=0.1@order.status` | an error (duplicates is per table) | the column is silently ignored |
| 12 | low | `chaos/groundtruth.py` `read_ground_truth` (~689) | a log with `log_version: 2`, or a malformed line | refused / an error naming the line | read silently; a bare `JSONDecodeError` |
| 13 | low | `chaos/config.py` `ChaosConfig.validate` (~100) | `categories={"value": {"enabled": True, "weight": "high"}}`; weight -1; seed -1; an override of an unknown category | listed by `validate()` | `validate()` returns `[]`; then `should_inject` raises `ValueError: could not convert string to float`, a negative weight never fires, a negative seed crashes `ChaosEngine` |
| 14 | low | `chaos/anomaly.py` `inject_anomalies` (~206) | a batch with nothing eligible, or `count == 0` | the same report `details` keys as otherwise | `details` lacks `fraction` and `kinds` |
| 15 | low | `docs/CHAOS.md` anomaly table | a `date64` column | the doc says "timestamp and date columns" | `date64` is never eligible (only `date32`) |
| 16 | low | `fidelity/_frame.py` `Frame.from_arrow` (~153) | a table with two columns named `c`, any tier | a result | `KeyError: Field "c" exists 2 times in schema` |
| 17 | low | `fidelity/tier3.py` `bootstrap_table` (~400) | `n_rows=-1` | an error naming `n_rows` | `ValueError: negative dimensions are not allowed` |
| 18 | low | `report/html.py` `_bars` (~64) | a NaN or negative share | a valid SVG | `height="nan"`, negative bar heights |
| 19 | low | `chaos/config.py` `ChaosOverride.params` | an override with `params` | used, or documented as unused | never read (the baseline also ignores it); docstring silent |
| 20 | low | `fidelity/_frame.py` `_column` (~181) | `timestamp[s]` value 2**62 | correct nanoseconds or an error | int64 overflow wraps silently (year > 2262 only; not fixed, recorded) |

No unsafe patterns found (no shell, SQL or path building from input beyond the CLI's output
paths); HTML escapes every user string (checked with `<script>` table and column names); results
are deterministic per seed.

## Phase 2 and 3: issues and fixes

Every finding but 19 (a docstring) was filed; each fix has a regression test committed first,
failing, with its output in the commit message.

| Finding | Issue | Test commit | Fix commit | State |
|---|---|---|---|---|
| 1 | #397 | a6c24bb | 222c081 | fixed |
| 2, 3 | #399 | ecac599 | f4e8500 | fixed |
| 4 | #401 | 9d60b1e | d688386 | fixed |
| 5 | #403 | 92f8a89 | 3da55ca | fixed |
| 6 | #404 | fadbb42 | 336b9af | fixed |
| 7, 8 | #406 | 5812bcd | c11efa6 | fixed (chaos parity `--quick` exit 0 after it) |
| 9, 10 | #408 | 4ee2219 | 90d61cf | fixed |
| 11 | #408 | 4ee2219 | reverted in the follow-up commit | **open, for the lead** (below) |
| 12 | #410 | 7485362 | 1afac7f | fixed |
| 13 | #414 | 7845c93 | 3d4906c | fixed |
| 14 | #418 | db4ec57 | 30a6198 | fixed |
| 15 | #421 | 9d6932e | 9003df8 | fixed (docs; eligibility unchanged) |
| 16 | #423 | 531ca63 | 222edd2 | fixed |
| 17 | #427 | 1a2ae8c | 07daebc | fixed |
| 18 | #430 | b4ee776 | 6f8fb08 | fixed |
| 19 | (none) | | 3d4906c | docstring: `ChaosOverride.params` is not read |
| 20 | #555 | 58f5340 | 7855de7 | fixed |

### Left open, for the lead

* **Finding 11 (`duplicates` with a column).** `tests/diff/test_drift_sweep.py:222` (outside this
  lane) builds `Corruption("duplicates", 0.6, "orders", "order_id")` and relies on the column being
  accepted. Refusing it broke that test's collection, so the refusal was reverted and the existing
  test left unchanged; the column is still silently ignored. Decide whether `duplicates` should
  refuse a column (and that test drop it) or document that it is ignored.
* **Outside this lane, not changed:** `src/shape/cli/chaos.py` passes `--seed` and the derived
  batch straight through; with #408 a negative `--seed` now gets the message "the seed is an
  integer 0 or more" from `corrupt_tables` instead of numpy's.

### Behaviour changes to note

* `corrupt_tables` now raises where it silently did nothing (#403) or wrote wrong values (#401,
  #399 for non-key types). Ground-truth logs are not compared by any equivalence verifier; the
  six categories' output on finite inputs is unchanged (#406 only caps a baseline that overflowed).
* No gate, tolerance, D-xx or T-xx decision changed; no existing test's expectation changed; no
  workflow edited.
