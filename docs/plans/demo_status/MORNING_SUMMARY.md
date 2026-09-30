# Morning summary (lead, overnight 2026-09-30)

Last updated: see the git log for this file.

## Merged into `main` and verified by the lead

| WP | What | How it was verified by the lead (not taken on trust) |
|---|---|---|
| DM-00 | Public-readiness cleanup: MIT license, notices, README/CHANGELOG, 163 stale files removed, false claims removed from kept docs | Claims grep and license grep both clean; full pytest green |
| DM-01 | `shape.profile`: the pure-Python profiler | **Spindle re-run fresh: 30/30 datasets PASS, every field bitwise identical**, with nothing in the "within tolerance but not identical" section |
| DM-02 | save/load, check (contracts), diff, HTML report, CLI | Tests green; README quick start and every CLI exit code (0, 1, 2) executed by hand |
| DM-03 | Pure-Python wheel `sqllocks_shape-0.9.0-py3-none-any.whl` (~185 KB) | Built; clean-venv install on Python 3.11 runs the core tests; `twine check` PASSED |
| DM-03b | `.github/workflows/publish.yml` (trusted publishing) | Workflow reviewed: a TestPyPI dispatch cannot reach real PyPI |
| DM-05, DM-05b, DM-06, DM-07, DM-08 (runbook) | Fabric Python notebook, Environment + PySpark notebook, 5 UDFs, 3 pipelines, RUNBOOK.md | **159 demo tests pass against the real API**, including Spark vs Python notebook equivalence on local PySpark and the UDFs through Microsoft's SDK |

Totals on `main`: `pytest --ignore=tests/demo/fabric` gives 806 passed; `pytest tests/demo` gives 159 passed.

## Fixes the lead made during integration

- The wheel metadata said **Apache-2.0** (hard-coded in the builder). It now reads MIT from pyproject (PEP 639), and a regression test guards it.
- The package description (the PyPI headline) overclaimed. It is now "Shape as Code: profile, check and compare how your data behaves. Early access."
- The README quick start used old calls that fail. It was rewritten and every line was executed.
- Removed the Fabric tests' silent fallback to a stub API. They must now pass against the real API.
- CI: the Fabric tests get their own Linux job (Java, unixODBC, PySpark), and `hatchling>=1.27` is required for the license metadata.
- Plan fix: §8.3 would have deleted `spindle_coverage.tsv` and `demo_status/`. Lane L3 caught this.

## Still in progress

- **DM-04** (demo data and day-2 drift) and the **talk kit** (DM-08): lane L3b is building them from `main`. The lead merges them after verifying.

## Needs you: GitHub Actions is not running any jobs

Every CI, Security and Publish run on this repo fails in about 2 seconds with **0 billable minutes and no logs**. The jobs never start. This predates tonight's merges. For a private repo that means the Actions minutes are exhausted, or billing or a spending limit is blocking runs. As a result:
- **The TestPyPI dry run did not run.** The run exists (Publish #1), failed before starting, and nothing was uploaded.
- CI results on `main` are not available yet. Everything above was verified locally by the lead instead.

**Likely fix: make the repo public** (standard runners are free for public repos). Alternatively, check **Settings → Billing → Actions** on your GitHub account.

## Your steps, in order

1. **Flip `sqllocks/shape` to public** (Settings → General → Danger Zone). DM-00 is on `main`, so the public repo shows the MIT license and no false claims.
2. Confirm Actions now run: open the Actions tab and check that CI on `main` goes green. If jobs still fail instantly, check Billing.
3. Re-run the **TestPyPI dry run**: Actions → Publish → Run workflow → `repository: testpypi`. The pending publisher you set up matches.
4. On pypi.org, add the **pending publisher** (environment `pypi`) if not done. In GitHub, add yourself as a **required reviewer** on the `pypi` environment (possible once the repo is public).
5. **Real publish:** Actions → Publish → `repository: pypi`, then approve.
6. **Live Fabric dry run:** follow `integrations/fabric/RUNBOOK.md`, and record timings in `demo/LIVE_TIMINGS.md`.
