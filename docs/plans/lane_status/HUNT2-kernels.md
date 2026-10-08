# HUNT2-kernels — second-pass bug hunt, kernels (lane/HUNT2-kernels)

Brief: the lane prompt plus `docs/plans/AUDIT_BRIEF.md` (its hard rules apply). Area:
`src/shape/kernel/**`, `src/shape/_kernel.pyi`, `rust/shape-kernel/**` and `tests/kernel/**`.
Branch started from `origin/int/INT-17` (8683435d). No gate, tolerance, D-xx or T-xx decision
changed. §11 and §2.3 are untouched, and so is `.github/workflows` (no workflow change is needed).
The pinned RefEngine checkout was not touched. No force-push or rebase.

The first audit (`origin/lane/AUD-kernel`, `docs/plans/lane_status/AUD-kernel.md` there,
issues #482-#553) is **not** merged into INT-17 (nor INT-18), so its fixes are absent on this
branch. None of its findings was re-filed. Mismatches that the hunt reproduced but that are
already filed were left to those issues: #534 (text patterns, trailing newline), #552 (`-0.0`
min, `count_numeric` on empty input or `top_n=0`, int64 extremes, `finalize(top_n=-1)`,
self-merge) and #686 (mean/m2 rounding order of float32/float16 columns).

#323 was also fixed on `lane/BUGS-profile-1` (bdcf037, tests in
`tests/kernel/test_fit_determinism.py`). Both fixes change `install_numpy_hooks` in
`rust/shape-kernel/src/lib.rs` in the same way (publish "ready" only after the hooks are set, no
lock held across Python). Expect a textual conflict when both lanes merge; keep either version.

Every fix has a regression test committed before it, with the failing output in the test
commit's message. The tests run against both kernels and include boundary cases.

## Findings, issues and fixes

| # | Issue | Severity | Finding | Test commit | Fix commit |
|---|---|---|---|---|---|
| 1 | #323 | medium | first fits on concurrent threads ran with libm `exp`/`ln` (last-ulp `distribution_params`, content id differs in ~4% of threaded runs) | cdb797b2 | 4e01d929 |
| 2 | #748 | medium | profile kernel: mean of finite values near the float maximum is `-inf`/NaN (native) or `inf` (twin); variance `-inf` or NaN | c6b4960f | 2da2afa6 |
| 3 | #755 | low | profile kernel: exact quantiles of such values are NaN (quantile 0) or ±inf outside `[min, max]`, both kernels | 127f5433 | 3b2b634b |
| 4 | #749 | low | twin `Hll.update_hashes`, `SpaceSaving.update_keys`, `update_array`, `Kll.update_values` accept int64/negative keys, lists, chunked arrays that the native kernel rejects | 978cd482 | 8635acf7 |

Notes on the fixes:

- #748 and #755 redo only the step that overflows (on halves, which is exact), so every finite
  path keeps its arithmetic and results: outputs of ordinary data are byte-identical.
- #323's cause and evidence are in the issue's comment and in 4e01d929's message.

## Recorded, not filed

- `numeric_stats` with NaN in its input reports a different outlier count in the two kernels.
  Its contract is "a float64 array without nulls" and every caller removes NaN first.
- `date_iso` returns `None` natively for years outside 0000-9999 (the caller falls back to
  Arrow's cast, by design); the twin formats them. Both give the same final text.
- Native error messages for unsupported Arrow types spell the type in Rust's form
  (`List(Int64)`) and the twin in pyarrow's (`list<item: int64>`). This is cosmetic.

## Commands and results (this session)

Environment: Python 3.11, pyarrow 25.0.1, `pip install -e '.[dev,streaming,advanced]'` plus
`plugins/shape-{domains,eventhubs,sqlserver,kafka,fabric}`; pinned baseline set up with
`benchmarks/vs_refengine/setup_refengine.sh` (`$REFENGINE_ROOT` only read). A first `make check` without
the baseline and the fabric plugin had 8 failures, all environmental
(`tests/generation/test_composite_p601e.py` imports the pinned checkout,
`tests/demo_cmd/test_notebook_and_outputs.py` needs `shape-fabric`); they pass once both are present.

```text
make check (SHAPE_KERNEL=auto -> rust):
  ruff check / ruff format --check (1411 files) / mypy (494 files)      clean
  compileall, vulture, lint-imports (1 kept, 0 broken)                  clean
  check_requirements / check_secrets / check_user_facing /
  check_shipped_data / check_plugin_skeletons / check_conformance_coverage   OK
  pytest -m "not emulator and not live and not heavy" --cov             9214 passed, 23 skipped; coverage 92.87%
  pytest -m heavy tests/kernel tests/profile tests/streaming            see note; rerun: 42 passed
  SHAPE_KERNEL=python pytest tests/kernel                               341 passed
  cargo fmt --check; cargo clippy --all-targets -D warnings             clean
  cargo test                                                            34 passed
SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"
  (--ignore tests/demo/fabric tests/demo/content, as in CI)             9214 passed, 23 skipped
benchmarks/vs_refengine/stream_prof/verify.py (after #748 and after #755) stream == batch PASS;
                                                                        identical across processes PASS; exit 0
```

Note on the heavy stage: in `make check`, and then twice more on its own,
`tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows` failed its 10% bound
the other way round. The 24M-row child's peak was the larger one: 479/389, 480/430 and 529/455 MB
(24M/48M). The test then passed 8 times in a row on the same machine: 3 times with the INT-17 kernel,
2 with only this lane's `profile.rs`, 2 with this lane's HEAD built by `maturin develop`, and 2 with
HEAD built by `pip install -e` as CI builds it. The whole heavy stage then passed (42 passed). Because
HEAD both failed and passed, the lane's changes are not the cause. The cause of the high 24M peaks
was not found. The test was not changed.
