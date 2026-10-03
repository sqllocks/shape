# P6-01-perf round 5 - re-port onto INT-16 (lane/P6-01-perf-r4)

Status: **re-ported and verified; no generated value differs from `int/INT-16`; Shape's own time at medium is 23% to
53% lower than INT-16's in every domain. The GEN-IN gate (10x at medium with the verifier exit 0 on the timed output)
counts for retail only (17.5x): INT-16's reserved identifier values (ISS-gen, owner issues 11 and 12) fail the verifier's
vocabulary check on e-mail, phone and URL columns in the other 13 domains, on INT-16's own tree as on this one, and only
retail's `customer.email` is in INT-16's allow-list (`benchmarks/vs_spindle/domain_1to1/domain_differences.py`).** That is
INT-16's to settle (extend the named allow-list, or give those domain columns `"domains": "realistic"` /
`"range": "assignable"`); this lane changed no allow-list, gate, tolerance, seed, floor or test expectation to get green.

Round 4's record (profile, levers, what was measured and not adopted) is `P6-01-perf-r4.md`; this file is round 5 only.

Machine: Intel(R) Xeon(R) Processor @ 2.80GHz, 4 vCPU, 15 GiB, Python 3.11.15, numpy 2.4.6, pyarrow 25.0.1 (both venvs),
baseline 3.0.1 (`422e78df`) built by `benchmarks/vs_spindle/setup_spindle.sh` in this session. A fresh container: this VM
is faster than round 4's (retail medium, Shape 0.38 s here against 0.47 s there), so absolute numbers are not comparable
with round 4's; every number below was measured here, both trees in one sitting.

## 1. What was done

1. **Merge of `origin/int/INT-16` (69df49a8)**, `492abcf1`. Nine files conflicted; both sides' behaviour is kept:
   * `builtins/strategies/providers.py`: INT-16's `domains` (`reserved` | `realistic`) and `range` (`reserved` |
     `assignable`) modes and the `digits` / `digit_ids` providers, on this lane's fused `compose_strings` route
     (`_domain_piece`: the reserved hosts or the realistic pool, one uniform pick per row, as `pool_take` of `_pick`).
   * `builtins/strategies/temporal.py`: INT-16's `_Range` (a date `end` stands for its whole day) on the fused uniform
     route (`window.check`, `start`/`stop`) and the int64-buffer timestamps.
   * `builtins/sinks/files.py`, `generation/output.py`: the native Parquet writer stays for a local path; an open file
     (INT-16's rolling and cloud files) goes through pyarrow; INT-16's `_check_destination`, `sink.target` and
     `_paths(fmt, sink, ...)`.
   * `generation/engine.py`: INT-16's declared output types (`finalize`, `_declared_callbacks`) with this lane's scheduler
     (the wrapped callbacks are the ones `_generate_tables` calls).
   * `generation/schema.py`: INT-16's `compat` checks and `x_` fields with the `validated` fast path; `_plain_json`
     (INT-16) replaces this lane's `_json_copy`; the docstring no longer names the plugin package (INT-16's
     `test_core_ships_no_retail_data_or_domain_code`).
   * `shape_domains/retail.py`: INT-16's `PackagedDomain` form (the `validated` flag comes from `PackagedDomain`).
   * `CHANGELOG.md`, `domain_1to1/README.md`: both entries / INT-16's line.
   * `shape_domains/_digests.py` regenerated (`scripts/update_domain_digests.py`, which re-checks every schema): INT-16
     changed the packaged schemas and `generation-schema-v1.json`.
   * `tests/generation/test_temporal_fast_units.py`: INT-16 changed the message for a range that ends before it starts
     ("is before the start"); the test matches it, and three ranges were added to the fused-against-stepwise test (a
     one-day date range, an empty instant range, a reversed range) plus a test that the fused route keeps the end day.
2. **New test** `tests/generation/test_provider_modes_fused.py` (129): every identifier provider in every mode, both
   kernels, chunks of 0 to 70,001 rows, against INT-16's stepwise code written out in the test (draws, gathers,
   `template_strings`), plus e-mails from the row's names, the reserved ranges and the mode errors.
3. **A domain loads without the composite machinery** (`d1654732`): INT-16 made `shape_domains._packaged` import
   `shape.generation.composite` and the composition table at module load (2.4 ms + 0.2 ms in a fresh process, by
   `-X importtime`); they are now imported by `composition()`. Test: `plugins/shape-domains/tests/test_load_imports.py`
   (fails before the change, passes after).
4. **`cargo test` in a debug build** (`0ab4daaa`): the Zipf guide-search test multiplied a counter by a 64-bit constant
   with `*`, which panics on overflow in the debug profile `make check` uses (round 4 ran `--release`); it is now
   `wrapping_mul` (test code only, same points as the release build).
5. **Merge of `origin/int/INT-16` again (0fdbde55)**, `152751a3`, no conflict: INT-16's fix for `emit --to file://` with
   a plugin emitter that also lists `file` (the one failing test of section 3, below), the publish key set, INT-16's status.

## 2. Equivalence (before any timing)

* **Table digests** (`docs/plans/evidence/P6-01-perf-domains/tools/digest.py`, seed 1042, sha256 of the IPC bytes of every
  table, 14 domains x 3nf/star x small/medium = 496 tables): the merge and the `int/INT-16` tree (a worktree with its own
  venv and kernel build) give the same file: `evidence/P6-01-perf-r5/table_digests_final.json` =
  `table_digests_int16.json`. (Against round 4's final tree, 316 of the 496 tables differ: INT-16 changed values -
  reserved identifiers, inclusive date ends - so INT-16 is the reference now.)
* **Written tables**: every domain written through the product path (`write_engine`, Parquet) on both trees and read back:
  496 files, 0 differ (`tools/write_all.py`, `tools/compare_all.py`, `written_tables_compare.txt`). The Parquet bytes
  differ (native writer here, pyarrow's on INT-16; the engine lane's recorded T-17 deviation), the values do not.
* Items 3 to 5 of section 1 do not touch generation.

## 3. Verifier (T-21), 28 cells, before timing

`"$SPINDLE_PY" domain_1to1/verify.py --domain D --scale S --impl shape` (Shape seed 1042, reference 42, baseline 43-46):
`evidence/P6-01-perf-r5/verify/` (`exit_codes.txt`, one report per cell). **Exit 0: retail small and medium,
capital_markets small, iot small; exit 1: the other 24.** `tools/classify_verify.py` compares every cell's findings with
round 4's (`verify/classification.txt`):

* every finding of round 4 is still there (the owner-accepted clause-(h) chance misses), and the only one that moved is
  manufacturing small's `work_order` fidelity, 96.33 -> 96.32 (INT-16's inclusive date end changes `start_date`);
* **every new finding is INT-16's**: vocabulary (and, at medium, distinct-ratio) failures of `email`, `contact_email`,
  `phone`, `destination` (a phone provider) and `page_url` (a URI provider) in hr, real_estate, pulse, supply_chain,
  insurance, education, financial, healthcare, marketing and telecom, and the clause-(h) fidelity of the tables that hold
  them (marketing `web_visit`, telecom `usage_record`, marketing small `lead` 98.06 -> 98.07);
* the same verifier on the **`int/INT-16` tree** (`PYTHONPATH` on the worktree, its own output directory) gives the same
  findings, cell for cell, for hr small, hr medium, insurance medium and marketing medium
  (`evidence/P6-01-perf-r5/verify_int16/`); the other cells' outputs are byte-identical (section 2), so their verdicts are.

## 4. Performance

**Interleaved fresh-process comparison, `int/INT-16` tree against the final tree** (`compare_trees.py --old
~/int16/src:~/int16/plugins/shape-domains/src`, 15 pairs, medium, `evidence/P6-01-perf-r5/ab/`): load the domain, build
the engine, generate, write snappy Parquet; no baseline involved.

| Domain | INT-16 median (min), ms | final median (min), ms | change | CPU-s INT-16 | CPU-s final |
|---|---|---|---|---|---|
| hr | 87.9 (74.0) | 45.5 (38.2) | -48.2% | 0.151 | 0.081 |
| real_estate | 118.1 (99.3) | 63.5 (51.3) | -46.2% | 0.214 | 0.119 |
| pulse | 374.3 (341.8) | 206.7 (176.6) | -44.8% | 0.610 | 0.576 |
| supply_chain | 143.4 (131.5) | 78.4 (67.2) | -45.3% | 0.272 | 0.164 |
| insurance | 114.4 (98.7) | 62.2 (51.5) | -45.7% | 0.220 | 0.134 |
| capital_markets | 214.6 (202.6) | 123.3 (113.0) | -42.6% | 0.416 | 0.320 |
| education | 124.9 (110.4) | 69.6 (60.5) | -44.3% | 0.231 | 0.154 |
| financial | 454.5 (430.6) | 289.8 (263.9) | -36.2% | 1.062 | 0.871 |
| healthcare | 406.5 (385.2) | 289.9 (256.1) | -28.7% | 0.938 | 0.765 |
| iot | 127.0 (104.6) | 63.0 (55.6) | -50.4% | 0.213 | 0.131 |
| manufacturing | 67.8 (60.5) | 35.4 (29.5) | -47.8% | 0.106 | 0.062 |
| marketing | 199.7 (182.4) | 94.3 (83.3) | -52.8% | 0.416 | 0.250 |
| telecom | 444.1 (414.0) | 249.8 (229.9) | -43.8% | 0.977 | 0.752 |
| retail | 491.8 (459.8) | 380.5 (339.6) | -22.6% | 1.232 | 1.026 |

**GEN-IN at medium (T-19, `bench.py --impl shape --domain D --scales medium`, 5 runs after 1 warm-up, each a fresh
process, exclusive `bench.lock`, load average <= 1.5)**, `evidence/P6-01-perf-r5/{after,before}/`, one set each (the
baseline's median moves between sets on a VM - round 4, table B - so the interleaved table above is the measure of the
change; these are the gate's numbers). `after` is the final tree, then the verifier with `--no-generate` on the timed
output; `before` is the `int/INT-16` tree (`PYTHONPATH`). Driver: `tools/final_runs.sh after|before`.

| Domain | INT-16: baseline / Shape s (ratio) | final: baseline / Shape s (ratio) | verifier, timed output |
|---|---|---|---|
| hr | 0.30 / 0.09 (3.45x) | 0.40 / 0.05 (8.65x) | exit 1 (INT-16 identifiers) |
| real_estate | 0.60 / 0.11 (5.45x) | 0.61 / 0.06 (9.40x) | exit 1 |
| pulse | 1.77 / 0.39 (4.56x) | 1.79 / 0.20 (9.19x) | exit 1 |
| supply_chain | 0.56 / 0.16 (3.62x) | 0.72 / 0.08 (8.83x) | exit 1 |
| insurance | 0.66 / 0.11 (5.86x) | 0.80 / 0.06 (13.14x) | exit 1 |
| capital_markets | 1.03 / 0.21 (4.91x) | 1.23 / 0.13 (9.30x) | exit 1 (round-4 chance miss) |
| education | 0.67 / 0.12 (5.43x) | 0.75 / 0.07 (11.57x) | exit 1 |
| financial | 3.18 / 0.45 (7.04x) | 2.92 / 0.30 (9.80x) | exit 1 |
| healthcare | 2.79 / 0.40 (6.97x) | 2.95 / 0.29 (10.31x) | exit 1 |
| iot | 0.72 / 0.12 (6.13x) | 0.59 / 0.06 (9.43x) | exit 1 (round-4 chance miss) |
| manufacturing | 0.21 / 0.07 (3.22x) | 0.32 / 0.03 (9.19x) | exit 1 |
| marketing | 1.12 / 0.21 (5.31x) | 0.91 / 0.10 (8.87x) | exit 1 |
| telecom | 3.48 / 0.45 (7.67x) | 3.52 / 0.26 (13.72x) | exit 1 |
| retail | 6.48 / 0.54 (11.95x) | 6.58 / 0.38 (**17.51x**) | **exit 0** |

(Totals as `bench.py` prints them, rounded to 10 ms; the ratios are of the unrounded medians. The raw runs are in the
JSON reports.) **Gate: retail 17.5x counts.** Insurance, telecom, education and healthcare are above 10x in this set but
their timed output fails the verifier on INT-16's identifier columns, so by the gate as written they do not count; the
rest are 8.7x to 9.8x in this single set.

## 5. Checks (final tree)

| Check | Result |
|---|---|
| `make check` on `1db9e677` (the tree before items 4 and 5) | every step up to the test run passed: ruff, ruff format (1,361 files), mypy (477 files), compileall, vulture, lint-imports (1 kept), check_requirements (89), check_secrets, **check_user_facing: clean**, check_shipped_data (33 files), check_plugin_skeletons (11), check_conformance_coverage; the coverage run: 9,290 passed, 1 failed (`test_emit_to_two_files`, below), coverage 92.77% (gate 86%) |
| the rest of `make check`, run by hand | `pytest -m heavy tests/kernel tests/profile tests/streaming` 42 passed; `SHAPE_KERNEL=python pytest tests/kernel` 464 passed; `cargo fmt --check` clean; `cargo clippy --all-targets -D warnings` clean; `cargo test` 1 failed (the debug-build overflow fixed in item 4), then 43 passed (debug and `--release`) |
| full suite, `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric` (all 11 first-party plugins installed) | 9,370 passed, 1 failed: `tests/streaming/emit/test_faults.py::test_emit_to_two_files` |
| same, `SHAPE_KERNEL=python` | 9,370 passed, 1 failed: the same test |
| `tests/demo/fabric tests/demo/content` in a venv per CI's job (`.[dev]`, `shape-dbt`, `tests/demo/fabric/requirements.txt`; it needs pyarrow < 20, so not in the benchmark venv), both kernels | 272 passed (rust), 272 passed (python) |
| `pytest plugins/shape-domains` | 12 passed |

**`test_emit_to_two_files`** failed on `int/INT-16` 69df49a8 too, in the same environment (`tests/
int16_test_emit_to_two_files.txt`), and passed in a venv without `shape-healthcare-standards` (`tests/
test_emit_to_two_files_cause.txt`): the healthcare plugin's X12 emitter also lists `file`, and took `file://`. INT-16
fixed it in 4edaeb16, merged in item 5. The suites, `make check` and the fabric demo tests on the final tree (after item
5) are in section 6.

## 6. Final tree, after the second merge

FINAL_RESULTS

## 7. For the owner and the INT-16 lead

* **Identifier columns of the 13 non-retail domains.** With ISS-gen's reserved defaults, the domains' `email`, `phone`,
  `contact_email`, `destination` and `page_url` columns fail T-21 vocabulary against the baseline, and with them the
  clause-(h) fidelity of marketing `web_visit` and telecom `usage_record`; only retail `customer.email` is in
  `domain_differences.py`. Either extend that allow-list (with each column's replacement rule, as for retail) or set
  `"domains": "realistic"` / `"range": "assignable"` on those domain columns. Until then GEN-IN can count only retail.
* No gate, tolerance, decision, seed, floor, case or allow-list was changed; the baseline checkout was only read; nothing
  user-facing names the baseline (`check_user_facing` clean).
