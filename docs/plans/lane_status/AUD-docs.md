# AUD-docs: audit and fix lane, documentation, examples and scripts

Area: `docs/*.md` (not `docs/plans/**`), `docs/talks/**`, `examples/**`, `scripts/**`, `README.md`,
`CONTRIBUTING.md`, `tests/release/**`. Branch `lane/AUD-docs`, from `origin/build/main-plan`
(`5c91ea5`).

Status: done for this area; one finding (#352) left for the lead, see below.

## How the area was hunted

- Every `shape ...` command in the docs (fenced blocks and inline code, nested subcommands, long
  and short flags) was run against the real parser's `--help` with all first-party plugins
  installed: one command path or flag missing (`docs/DEMO.md` `-o`, #305).
- Every ```` ```python ```` block in the docs was parsed and its `shape` imports and `shape.X`
  attributes resolved: all resolve.
- Every relative Markdown link and every repo path in backticks was resolved; every `*.md` file
  name the docs cite was looked up; every `sqllocks-shape[extra]` the docs print was checked
  against `pyproject.toml`.
- `docs/TUTORIAL.md` and `docs/QUICKSTART.md` were run literally on generated CSVs.
- `docs/talks/shape-v1/verify_snippets.sh` (the talk's claim gate) was run on freshly generated
  demo data (`demo/make_data.py`).
- Each script was read, run, and probed with edge inputs; the area's tests were run with
  `--cov=scripts` (51% before this lane; `check_user_facing.py`, `check_secrets.py`,
  `check_requirements.py`, `gen_plugin_api_docs.py`, `fuzz_artifacts.py`, `pacing_diagnostic.py`
  at 0%).

## Findings

Severity, file and line, reproduction, expected, actual. "Filed by" names the lane that filed the
issue first when it was not this one.

1. **high** `docs/talks/shape-v1/verify_snippets.sh` slide 20 block; `SCRIPT.md:432,442`,
   `DECK.md:87`, `NUMBERS.md:123` (N-77), `STATUS.md:65`, `READINESS.md:25`. #377.
   Repro: `python demo/make_data.py --out "$BENCH_DATA_DIR/demo"`, then
   `PY=$SHAPE_VENV/bin/python bash docs/talks/shape-v1/verify_snippets.sh`. Expected `CLAIMS OK`.
   Actual: `raw .shape contains 2 of 47,515 customer email addresses`, `AssertionError: slide 20
   quotes 502`. Since #37 a near-unique text column keeps no top values; the claim is stale.
2. **high** `scripts/offline_lock.py:46-67` (`_plugin_dependencies`, `_expand`). #259 (filed by the
   CI audit lane). `declared_sets()["postgres"]` holds core only: the extras a first-party
   requirement asks for (`sqllocks-shape-databases[postgres]`) are dropped, so the `postgres`,
   `mysql` and `databases` locks never hold `psycopg[binary]` / `pymysql` and `check` passes.
3. **medium** `scripts/check_shipped_data.py:285-300` (`_network_imports`). #261 (CI lane).
   `from urllib import request`, `from http import client` and `urllib3` are not reported.
4. **medium** `scripts/check_secrets.py:4`. #262 (CI lane). No pattern for the credentials Shape
   handles: `AccountKey=`, `SharedAccessKey=`, GitHub tokens, AWS key ids, `client_secret = "..."`,
   `BEGIN ENCRYPTED/DSA PRIVATE KEY`.
5. **medium** `docs/TUTORIAL.md:6` (step 6). #347. The tutorial diffs two `shape capture` models,
   the legacy path that ignores `--fail-on-drift`, `--json` and thresholds (#107, CLI, not this
   area): `shape diff a.json c.json --fail-on-drift` exits 0 on a `high` `column_removed`.
6. **medium** `scripts/offline_lock.py:128-130` (`check_lock`). #352. A requirement whose marker is
   false on the checking host is skipped, so Linux CI never requires `tzdata; sys_platform ==
   'win32'` in a universal lock. **Not fixed: pinned by an existing test**
   (`tests/release/test_offline_lock.py::test_check_directory_flags_dropped_dependency[tzdata]`
   asserts `problems == []`); recorded for the lead.
7. **medium** `docs/SCALE.md:43`. #247 (packaging lane; the missing extra itself is
   `pyproject.toml`, outside this area). The doc tells users to
   `pip install 'sqllocks-shape[fabric]'`, an extra that does not exist: pip warns and installs
   core only.
8. **medium** `docs/DEMO.md:14`. #305 (demo lane). `shape demo notebook retail --mode seeding -o
   retail.ipynb` exits 2: the option is `--output`.
9. **low** `docs/INSTALL.md:3,26-28`; `README.md:3`. #255 (packaging lane). "Supported Python:
   3.11–3.13" (T-06: 3.11–3.14); the offline-lock extras list omits `delta-fallback`, `postgres`,
   `mysql`, `databases`; the README's relative `LICENSE` link breaks on the package index page.
10. **low** `README.md:121-122`. #346. "A privacy-safe profile ... is planned", while the same
    README and `docs/PRIVACY_MODEL.md` document the shipped `shape profile safe`.
11. **low** `docs/PRODUCT_ARCHITECTURE.md:184,202`. #348. Cites
    `07_SENSITIVE_CLASSIFIED_SECURITY_SPEC.md`, which does not exist (`docs/SECURITY_SPECIFICATION.md`
    does); maps "MCP -> Shape MCP", which this repository does not ship (D-08: `shape bridge`).
12. **low** `docs/CONTRIBUTING.md`. #349. Points to an `rq/` benchmark that does not exist, a
    "frozen Shape 1.0" contract (the version is 0.9.0, T-10), and a pre-submit list that is not
    what CI and `make check` run.
13. **low** `scripts/check_secrets.py:6-7`. #350. A virtualenv in the tree not named `.venv`
    (`python -m venv venv`) fails the scan on pip's vendored `cacert.pem`; git-ignored files are
    scanned too.
14. **low** `scripts/fuzz_artifacts.py:24`. #263 (CI lane). `--iterations 0` (or negative) fuzzes
    nothing and exits 0.
15. **low** `scripts/pacing_diagnostic.py:7`. Docstring says the script is run by
    `.github/workflows/pacing-diagnostic.yml`, which CI-FIX removed. Improvement, no issue.
16. **low** `scripts/build_pure_wheel.py:9-12`. Docstring says the stricter pins of
    `pyproject.toml` "return with plan work package P0-05"; `pyproject.toml` now declares the same
    core requirements as the pure wheel (T-07). Improvement, no issue.

Outside this area (seen while hunting, already filed by other lanes, not fixed here): `shape diff`
on capture JSON ignores its options (#107); `shape quality` exits 2 on a failed check (#114);
unsigned-artifact notices printed as raw Python warnings by `shape compatibility` and others
(#109, #311); `pyproject.toml` lacks the T-08 `fabric`, `dbt`, `healthcare`, `all` extras (#247).

For the owner (not a defect of this lane's code): `verify_snippets.sh` part 3 reports R1, R3, R4,
R5, R8 and R9 as READY while slides 25-27 call them planned (#377).

## Fixes

Each defect got a failing regression test first (its failing output is in that commit's message),
then the fix. Findings by number:

| # | Issue | Regression test (failing commit) | Fix commit |
|---|---|---|---|
| 1 | #377 | `verify_snippets.sh` itself (failing output in the fix message) | `72db630` |
| 2 | #259 | `f07bd2e` `tests/release/test_offline_lock.py` (2 tests) | `c302365` |
| 3 | #261 | `dac3e1b` `tests/release/test_shipped_data.py` (7 cases) | `416cfb8` |
| 4 | #262 | `c33d7f7` `tests/release/test_check_secrets.py` | `7480b2e` |
| 5 | #347 | `8b352fb` `tests/release/test_docs.py` | `1823a31` |
| 6 | #352 | not fixed: pinned by an existing test (below) | — |
| 7 | #247 (docs part) | `8b352fb` | `ec65fc0` |
| 8 | #305 (docs part) | `8b352fb` | `c119445` |
| 9 | #255 (docs part) | `8b352fb` | `0b63582` |
| 10 | #346 | `6e2e2da` | `bdee336` |
| 11 | #348 | `8b352fb` | `4f7aa56` |
| 12 | #349 | `322071f` | `a1800f5` |
| 13 | #350 | `c33d7f7` | `7480b2e` |
| 14 | #263 | `c1819cd` `tests/release/test_fuzz_driver.py` | `b092c7e` |
| 15 | (improvement) | docstring only | `764f56a` |
| 16 | (improvement) | docstring only | `173fe9b` |
| 17 | #507, found by the new docs test: `docs/specs/SHAPE_2.md:3` cites `SHAPE_1_0_GA.md` (it is `SHAPE_1_0.md`) | `8b352fb` (cited files exist) | `e9b85c9` |

Improvements (behaviour-preserving, one commit each): `9d1309a` 19 tests for
`scripts/check_user_facing.py` (the D-13 gate had none); `scripts/check_secrets.py` is now
importable (`findings()`, `main()`) as part of `7480b2e`.

New regression test `tests/release/test_docs.py` (85 tests, 2.4 s, in process) keeps the user
docs in step with the code: every documented command path and long or short option exists in
its `--help` (plugin commands are checked when their plugin is installed), every
`sqllocks-shape[...]` extra exists, relative links and cited `.md` files exist, INSTALL.md states
the CI matrix's Pythons and every offline-lock set, `shape diff` in the docs compares profiles,
the README names the safe profile, and the contributing guide names `make check`.

## Left open, and why

- **#352** (offline lock check skips Windows-only `tzdata` on Linux). The existing test
  `tests/release/test_offline_lock.py::test_check_directory_flags_dropped_dependency[tzdata]`
  asserts the current behaviour (`problems == []`, "tzdata is Windows-only: not required on this
  platform"). Fixing it changes that expectation, which this lane may not do. Proposed fix for the
  lead: in `check_lock`, skip a requirement only when its marker is false for every supported
  platform (evaluate with `sys_platform` in `linux`, `darwin`, `win32` and the T-06 Pythons), and
  change the tzdata case to expect a finding.
- Parts of #247, #255 and #305 outside this area: the `fabric` (and `dbt`, `healthcare`, `all`)
  extras in `pyproject.toml`, the five `src/` error messages that print
  `pip install 'sqllocks-shape[fabric]'`, the relative links in `plugins/shape-fabric/README.md`,
  and whether `shape demo notebook` should also accept `-o`. Note: the first-party plugins are
  not on PyPI yet (#310), so `pip install sqllocks-shape-fabric` (docs/SCALE.md, docs/DEMO.md)
  works only from a wheelhouse or the repository until they are published.
- For the owner: `verify_snippets.sh` part 3 reports R1, R3, R4, R5, R8, R9 READY while slides
  25-27 call them planned (#377). Talk content; not changed.
- Outside this area, seen while hunting and already filed by other lanes: #107, #109, #114, #311.

No workflow change is needed: the new tests run in the existing pytest jobs, and the scripts keep
their command lines.
