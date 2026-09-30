# Morning summary (lead, overnight 2026-09-30)

Last updated: see the git log for this file.

## Merged into `main` and verified by the lead

| WP | What | How it was verified by the lead (not taken on trust) |
|---|---|---|
| DM-00 | Public-readiness cleanup: MIT license, notices, README/CHANGELOG, 163 stale files removed, false claims removed from kept docs | Claims grep and license grep both clean; full pytest green |
| DM-01 | `shape.profile`: the pure-Python profiler | **Spindle re-run fresh: 30/30 datasets PASS, every field bitwise identical**, with nothing in the "within tolerance but not identical" section |
| DM-02 | save/load, check (contracts), diff, HTML report, CLI | Tests green; README quick start and every CLI exit code (0, 1, 2) executed by hand |
| DM-03 | Pure-Python wheel `sqllocks_shape-0.9.0-py3-none-any.whl` (~185 KB) | Built; clean-venv install on Python 3.11 runs the core tests; `twine check` PASSED |
| DM-03b | `.github/workflows/publish.yml` (trusted publishing) | **TestPyPI dry run passed** (Actions run 36665613888). `pip install sqllocks-shape==0.9.0` from TestPyPI in a clean venv, then `shape.profile`, works |
| DM-04 | Demo data (retail medium + D2) and documented day-2 drift, contracts (`demo/`) | Lead re-ran `make_data.py` (13 files, 168.6 MB, 9 s) and the content tests: day-1 contracts pass, day 2 fails on exactly the 4 documented rules |
| DM-08 (talk kit) | `demo/TALK.md`, generated `demo/BENCHMARKS.md`, `LIVE_TIMINGS.md` placeholder | Every number is generated from and cites `benchmarks/baselines/2026-09-29/`; table names and contract paths match L2's pipelines and runbook |
| DM-05, DM-05b, DM-06, DM-07, DM-08 (runbook) | Fabric Python notebook, Environment + PySpark notebook, 5 UDFs, 3 pipelines, RUNBOOK.md | **159 demo tests pass against the real API**, including Spark vs Python notebook equivalence on local PySpark and the UDFs through Microsoft's SDK |

Totals on `main`: full `pytest` (all but Fabric) gives 835 passed; `pytest tests/demo` gives 188 passed. **GitHub CI is green on all 11 jobs** (Linux, macOS and Windows × Python 3.11–3.13; the Fabric demo job, which also runs the demo content tests against pinned Spindle; and the build). Runs: 36666589939 (8fc8ef8), 36666928868 (dd6eab4, the L3b merge) and 36667088329 (410ecde).

## Fixes the lead made during integration

- The wheel metadata said **Apache-2.0** (hard-coded in the builder). It now reads MIT from pyproject (PEP 639), and a regression test guards it.
- The package description (the PyPI headline) overclaimed. It is now "Shape as Code: profile, check and compare how your data behaves. Early access."
- The README quick start used old calls that fail. It was rewritten and every line was executed.
- Removed the Fabric tests' silent fallback to a stub API. They must now pass against the real API.
- CI: the Fabric tests get their own Linux job (Java, unixODBC, PySpark), and `hatchling>=1.27` is required for the license metadata.
- Plan fix: §8.3 would have deleted `spindle_coverage.tsv` and `demo_status/`. Lane L3 caught this.

## Found by CI once Actions ran (fixed)

- **Windows was broken:** pyarrow's `strftime` needs a timezone database that Windows lacks, and the profiler used `fork` processes. Both are fixed, and parity with Spindle is unchanged (verified again: exit 0, all bitwise).
- **Shape could not be installed next to the Fabric UDF SDK.** Microsoft's `fabric-user-data-functions` pins `pyarrow<20`, and Shape required `>=23`. Plan decision T-07 said `>=25`, which was wrong for the same reason. It is now `pyarrow>=14.0.1` (plan-fix commit 8fc8ef8, logged in §2.3). Benchmarks still pin 25.0.1 in both venvs.

## Things to know for the talk (from DM-04)

- `shape.diff` with **default** thresholds does not flag the +40% `order_total` shift (0.43 std, below the 0.5 default). The contract gate still fails on day 2 (on `order_total.max`). If you show diff live, pass `thresholds={"mean_shift_std": 0.25}`.
- The diff also reports `order_total` "new categorical values" on day 2, because the profiler (like Spindle) keeps value lists for that float column. The severity is low, but a viewer may notice it.

## Update, 2026-09-30 1:10 PM EDT

- **sqllocks-shape 0.9.0 is on PyPI** (Publish run 36749097798 from `main` at b2dd663, approved by you). `pip install sqllocks-shape` works; verified by the lead in a clean environment. The next release must use a higher version.
- Phase 0 of the main plan (P0-00..P0-07, gate G0) is merged into `main`. Phase 1 (Rust engine) is in progress on `build/main-plan`.
- Fixed on `main` before the release: `shape check`/`diff` with a missing `.shape` now exit 2; the README and Fabric runbook warn that a `.shape` file holds real data values.

## Your steps, in order

1. ~~Make the repo public~~, ~~confirm CI runs green~~, ~~TestPyPI dry run~~: all done.
2. ~~PyPI publisher, reviewer and real publish~~: done (0.9.0).
3. **Live Fabric dry run (before the Oct 3 talk):** follow `integrations/fabric/RUNBOOK.md`, and record timings in `demo/LIVE_TIMINGS.md`.
