# PLUG-INT: integration of the dbt, behavior and healthcare plugins (lane/PLUG-INT)

Branch `lane/PLUG-INT`, cut from `build/main-plan` at ce8fe11. Four merge commits (no rebase, no force-push),
then the extras, documentation, CI and a repair. No §11 or §2.3 edit. `$SPINDLE_ROOT` was not touched. No D-xx,
T-xx, gate or tolerance changed beyond what the §2.3 rows of 2026-10-03 (decisions (2), (5), (7)) decided.

## What merged, in order

| Order | Branch | Content |
|---|---|---|
| 1 | `origin/lane/ISS2-dbt` | issue #44, `plugins/shape-dbt`, `examples/dbt_jaffle_shop`, Fabric pipeline `shape_dbt_gate`, `docs/DBT.md`, the decimal `min`/`max` fix in `shape.check` |
| 2 | `origin/lane/HC-behavior` | issue #49 and #45 part 1: the core `shape.behaviors` entry-point group (`src/shape/plugins/api/v1.py`, `kit.py`), `plugins/shape-behavior`, `examples/behavior-plugin` |
| 3 | `origin/lane/HC-codes` | `plugins/shape-healthcare-codes` |
| 4 | `origin/lane/HC-standards` | `plugins/shape-healthcare-standards` (X12, FHIR, OMOP writers; NCPDP bring-your-own layer) |

`origin/lane/HC-domain` was **not** merged (§2.3 decision (5)). Checked first: none of the 11 commits that only
HC-domain has is an ancestor of any of the four branches (`git merge-base --is-ancestor`, all negative). The four
branches contain only their own commits plus earlier merges of `build/main-plan`.

## Conflicts and resolutions (all keep both sides' behaviour)

| File | Merges | Resolution |
|---|---|---|
| `docs/plans/COMPLETION_PLAN.md` | all four | The lead's text (this branch's) kept whole. The lanes' side differed only by rows and T-08 text the integration tree already carries; lanes never edit §11 or §2.3. |
| `CHANGELOG.md` | dbt | Both entries kept. |
| `pyproject.toml` | dbt | pytest markers: `realtime` (this side) and `dbt` (lane) both kept. |
| `scripts/check_plugin_skeletons.py` `EXPECTED` | behavior, codes, standards | Each lane added its own name to a different base list; resolved to the union. |
| `tests/plugins/test_plugin_kit_install.py` | behavior, codes, standards | The expected set and the built-wheel count follow the union (8, 9, then 10). |

**Resolution error, found and repaired.** In the HC-standards merge my conflict resolver matched across two hunks
of `scripts/check_plugin_skeletons.py` and left the file syntactically broken in the merge commit (a doubled
`EXPECTED` tuple, and HC-standards' hyphen-safe package-name check dropped). `ruff` found it. Repair commit
"repair check_plugin_skeletons.py after the HC-standards merge": ten names in `EXPECTED`, HC-standards' check
kept. Nothing else was affected: the other resolved files were re-read, and `git grep` finds no conflict marker.

## Decisions (2) and (7)

- `scripts/check_plugin_skeletons.py` `EXPECTED`: kafka, eventhubs, fabric, sqlserver, domains, simulation, dbt,
  behavior, healthcare-codes, healthcare-standards (ten). `tests/plugins/test_plugin_kit_install.py` expects the
  same set and ten built wheels. `plugins/shape-*/tests`: dbt 115, behavior 86, healthcare-codes 100,
  healthcare-standards 139 tests (the last with the HL7 validator).
- Core `pyproject.toml` extras: `dbt` (`sqllocks-shape-dbt==0.9.0`); `healthcare` (behavior, healthcare-codes,
  healthcare-standards, each `==0.9.0`); `all` (all ten distributions pinned to `0.9.0`, plus `cryptography`,
  `scipy`, `openpyxl` and `deltalake`, the third-party extras of T-08). New `tests/plugins/test_extras.py` enforces
  that the extras pin every distribution to the core version and that `all` includes all ten.
- Docs: `docs/plugins/authoring.md` §5 names all ten and the two new extras, and says "ten `pyproject.toml`
  files". No other list of the first-party plugins existed (the other mentions are per-plugin guides).
- CI: the existing plugin job (`stream-plugins` in `ci.yml`) now installs the four new plugins (and the
  standards `[test]` extra) and runs their tests with `-m "not emulator and not live and not dbt"`. The `dbt`
  job of `ci.yml` (whole dbt directory) is removed; `nightly.yml` gets `dbt-build` (`-m dbt`: the jaffle-shop
  `dbt build` on DuckDB) and `healthcare-standards-validator` (the official HL7 validator, about one minute, marked
  `live`). No other plugin test takes more than a minute; the behavior scale tests (marked `heavy`) take 20 s in
  total and stay in the fast job.
- Licences: all four new plugins are MIT with a `LICENSE` identical to the repository's
  (`check_plugin_skeletons.py` enforces it); `shape-healthcare-codes` also ships `THIRD_PARTY_NOTICES.md`.
- Decision (4): no plugin ships NCPDP or NUCC content. The standards plugin's NCPDP code is a layout engine that
  reads a JSON layout the licensed user supplies (`ncpdp/layout.py`: "This package never ships those facts"); the
  only NCPDP-shaped file is a synthetic test layout under `tests/` (not in the wheel, with `LICENSING.md`). The
  codes plugin lists the NUCC taxonomy as a bring-your-own asset (loader only); its shipped data is
  `icd10cm.arrow` and `icd10cm.json` (public-domain ICD-10-CM subset, labelled in `test_assets.py`).

## Left for the lead (not decided here)

- **No `fabric` extra exists** in core `pyproject.toml` although T-08 lists `[fabric]`. I did not add one; `all`
  names `sqllocks-shape-fabric` directly.
- `all` also holds the four third-party extras (`sign`, `scipy`, `excel`, `delta` contents). If T-08 means `all`
  to be plugins only, drop those four.
- **Windows is not verified.** `stream-plugins` runs on Windows too and now runs the new plugins' tests there; the
  codes lane could not run Windows either. No POSIX-only call was found by grep (`fcntl`, `fork`, `chmod`,
  `symlink`, `getuid`, fixed `/tmp`).
- `dbt deps` from the package hub does not run in this sandbox (tarball downloads give 403). Locally the dbt
  build tests ran with `SHAPE_DBT_PACKAGES_FILE` pointing at `local:` entries (git clones of dbt-utils 1.4.1,
  dbt-expectations 0.10.9 with its own `packages.yml` removed, dbt-date 0.9.2). **The hub path is untested here**; it
  is what the nightly `dbt-build` job uses. The nightly validator job's `curl` of `validator_cli.jar` ran here and
  worked; the job itself has not run.
- HC-standards' round trips have not been re-run against HC-domain's real tables (not in this repository).
- Not built, per the lanes: external-generator calibration (behavior), Elixhauser and Charlson crosswalks (codes),
  Arrow decimal output in generation (dbt), the live Fabric dbt job activity (`[VERIFY]`, owner O-07).

## Commands and results

Python 3.11, `~/.venvs/shape`: `pip install -e ".[dev,advanced,streaming]"`, all ten `plugins/*` editable,
`dbt-duckdb`, and `plugins/shape-healthcare-standards[test]`. Rust kernel built by maturin.

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_spindle` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_spindle` | 1026 files already formatted |
| `mypy` | no issues in 354 source files |
| `pytest -m "not emulator and not live" --ignore=tests/demo/fabric`, `SHAPE_KERNEL=rust` | 5560 passed, 4 deselected (1237 s) |
| same, `SHAPE_KERNEL=python` | 5560 passed, 4 deselected (4619 s) |
| `pytest tests/demo/fabric` (unixODBC, `tests/demo/fabric/requirements.txt`) | 233 passed |
| `pytest -m "not emulator and not live" plugins` (all ten, `SHAPE_DBT_PACKAGES_FILE` local, HL7 validator jar set) | 1124 passed, 75 deselected |
| `pytest -m live plugins/shape-healthcare-standards/tests/fhir/test_official_validator.py` | 1 passed (33 s) |
| `python -m shape.plugins.kit sqllocks-shape-<dist>` for each of the ten | all OK (kafka 2, eventhubs 2, fabric 3, sqlserver 2, domains 1, simulation 1, dbt 5, behavior 4, healthcare-codes 8, healthcare-standards 9 plugins conform) |
| `python scripts/check_plugin_skeletons.py --build OUT` | OK, 10 distributions, 10 pure `py3-none-any` wheels, no tests inside |
| `python scripts/check_user_facing.py`, and with `--wheel` on all ten built wheels | clean, clean |

Not run here: emulator and live tests (the 4 deselected are core; 75 in plugins), the Windows and macOS legs,
`dbt deps` from the hub, a CI run of the edited workflows (both files parse as YAML).


# Round 2: merge of `origin/int/INT-15`

`git merge origin/int/INT-15` (merge, no rebase, no force-push), merge commit on `lane/PLUG-INT`. Session note: this
container started on a checkout at `ce8fe11`, behind `origin/lane/PLUG-INT`; fast-forwarded to round 1 (`3039907`)
before merging.

## Conflicts and resolutions (both sides kept)

| File | Resolution |
|---|---|
| `.github/workflows/ci.yml` | **Taken whole from `origin/int/INT-15`** (`git checkout --theirs`), as instructed. Round 1's CI change is the diff below, for the lead to apply. `.github/workflows/nightly.yml` merged without a conflict (INT-15's jobs plus `dbt-build` and `healthcare-standards-validator`); it is in the merge commit as git produced it. |
| `docs/INSTALL.md` | INT-15's "Offline and air-gapped installs" section kept whole; round 1's `sqllocks-shape-dbt` paragraph kept; round 1's one-line offline sentence dropped because INT-15's section replaces it. |
| `pyproject.toml` | Extras: INT-15's `postgres`, `mysql`, `databases` and its two new `dev` entries (`duckdb`, `fsspec`) plus round 1's `dbt`, `healthcare`, `all`. New in this round: `fabric`, and `all` now also pins `sqllocks-shape-databases`. |
| `scripts/check_plugin_skeletons.py` | `EXPECTED` is the union: eleven names (`databases` added to round 1's ten). |
| `tests/plugins/test_plugin_kit_install.py` | Expected set is the union (eleven); built-wheel count 11; the behavior outside-install tests kept. |

The `shape-databases` plugin is therefore the eleventh first-party distribution: `tests/plugins/test_extras.py`
now expects eleven, and `docs/plugins/authoring.md` names it and says "eleven `pyproject.toml` files".

## Round 1 gaps

1. **`fabric` extra: built.** T-08 lists `[fabric]` and the extras are this lane's scope (round 1 decision (2)).
   `fabric=["sqllocks-shape-fabric==0.9.0"]`; `all` already held it. New test
   `test_every_plugin_has_its_own_extra` requires each plugin to appear in an extra named after it (the three
   healthcare distributions in `healthcare`). `python scripts/offline_lock.py generate` then `check`: 25 sets
   OK, so the new extras (`fabric`, `dbt`, `healthcare`, `all`) are handled by INT-15's lock tooling with no change
   to it.
2. **Windows: audited, and two real portability defects fixed; the Windows leg itself still cannot run here.**
   Grep of the four plugins' `src` and `tests` for POSIX-only calls (`fork`, `getuid`, `chmod`, `symlink`,
   `fcntl`, fixed `/tmp`, `shell=True`, hard-coded `bin/`): none. Found and fixed: (a) `Path.write_text` without
   `newline` writes CRLF on Windows, so the dbt files, the behavior `run.json` and module JSON, and the
   healthcare-codes asset metadata were not byte-identical across platforms; they now pass `newline="\n"` (the
   FHIR, X12 and NCPDP writers already did); (b) `plugins/shape-dbt/tests/test_dbt_build.py::dbt_exe` fell back to
   `Scripts/dbt`, which does not exist on Windows (`dbt.exe`). The diff below keeps `stream-plugins` on
   `[ubuntu-latest, windows-latest]`, so the new plugins' fast tests will run on Windows in CI; that run is the
   verification and **has not happened**. The dbt-build tests are nightly on Linux only.
3. **dbt hub: out of scope to verify here; owner is the nightly `dbt-build` job (P6-07b / O-07 for the Fabric
   dbt activity).** `dbt deps` from `hub.getdbt.com` fails in this sandbox again (`tarfile.ReadError: not a gzip
   file`: the proxy does not pass the package tarballs), and the build tests ran with `SHAPE_DBT_PACKAGES_FILE`
   pointing at local clones. A fix would be a workflow or network-policy change, not code in this lane. First
   nightly run is the check; if it fails there, the failing step names the package.

## Workflow change for the lead (exact diff against `origin/int/INT-15`'s `ci.yml`)

Apply with `git apply` (checked with `patch --dry-run` against that file). It extends `stream-plugins`
(Linux and Windows) with the four plugins' fast tests, and makes the Fabric demo job install `shape-dbt` (the demo's
dbt gate imports it). INT-15's `database-plugins` job is unchanged.

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -125,6 +125,8 @@
       - run: python -m pytest -q tests/plugins/test_kit.py tests/plugins/test_plugin_kit_install.py
   stream-plugins:
     # P3-04, P5-02, P6-07a, P6-08: the Kafka, Event Hubs, SQL Server and Fabric plugins' contract tests.
+    # PLUG-INT: also the dbt, behavior, healthcare-codes and healthcare-standards plugins' fast tests.
+    # Their slow suites (the dbt build against DuckDB, the official HL7 validator) run in nightly.yml.
     runs-on: ${{ matrix.os }}
     strategy:
       matrix:
@@ -134,9 +136,9 @@
       - uses: actions/setup-python@v5
         with: {python-version: '3.11'}
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric
-      # The plugins' contract tests (no external service); emulator and live runs are nightly.
-      - run: python -m pytest -q -m "not emulator and not live" plugins/shape-kafka/tests plugins/shape-eventhubs/tests plugins/shape-sqlserver/tests plugins/shape-fabric/tests
+      - run: pip install -e '.[dev]' -e plugins/shape-domains -e plugins/shape-kafka -e plugins/shape-eventhubs -e plugins/shape-sqlserver -e plugins/shape-fabric -e plugins/shape-dbt -e plugins/shape-behavior -e plugins/shape-healthcare-codes -e 'plugins/shape-healthcare-standards[test]'
+      # The plugins' contract tests (no external service); emulator, live and dbt-build runs are nightly.
+      - run: python -m pytest -q -m "not emulator and not live and not dbt" plugins/shape-kafka/tests plugins/shape-eventhubs/tests plugins/shape-sqlserver/tests plugins/shape-fabric/tests plugins/shape-dbt/tests plugins/shape-behavior/tests plugins/shape-healthcare-codes/tests plugins/shape-healthcare-standards/tests
   database-plugins:
     # ISS2-sinks: the PostgreSQL and MySQL sinks, contract tests against an in-memory server
     # (no driver is installed: the sinks load psycopg / PyMySQL only when they connect).
@@ -181,7 +183,7 @@
         with: {distribution: temurin, java-version: '17'}
       - run: sudo apt-get update -q && sudo apt-get install -y -q unixodbc
       - run: python -m pip install -U pip
-      - run: pip install -e '.[dev]' -r tests/demo/fabric/requirements.txt
+      - run: pip install -e '.[dev]' -e plugins/shape-dbt -r tests/demo/fabric/requirements.txt
       - run: python -m pytest -q tests/demo/fabric tests/demo/content
   pure-wheel:
     # PF-03: the T-29 pure wheel is py3-none-any and under 28.6 MB, and the UDF helpers and
```

## Round 2 commands and results

Python 3.11, `~/.venvs/shape` (editable core and all eleven plugins, `dbt-duckdb`, `tests/demo/fabric/requirements.txt`,
unixODBC from apt). Final tree = merge commit plus the round-2 status commits.

| Check | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_spindle` | All checks passed |
| `ruff format --check` (same paths) | all files already formatted |
| `mypy` | no issues |
| `python scripts/check_plugin_skeletons.py` / `--build OUT` | OK, 11 distributions, 11 wheels |
| `pytest tests/plugins` | 122 passed |
| `pytest -m "not emulator and not live and not dbt"` on dbt, behavior, healthcare-codes (after the LF/`dbt.exe` edits) | 294 passed |
| `python scripts/offline_lock.py generate` then `check` | OK, 25 sets |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` (whole suite incl. `tests/demo/fabric`) | 7120 passed, 6 failed, 13 deselected (2318 s) |
| `SHAPE_KERNEL=python`, same | 7121 passed, 5 failed, 13 deselected (7729 s) |

(The two full runs ran at the same time on one machine.) Failures, each checked on a worktree of
`origin/int/INT-15` (merge not applied), same venv, `PYTHONPATH=src`:

| Test | On INT-15 alone | Verdict |
|---|---|---|
| `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]` | fails (`Expected np.float16 instance`) | pre-existing, not fixed here |
| `tests/kernel/test_hashing.py::test_one_and_one_point_zero_hash_equal` | fails | pre-existing |
| `tests/streaming/emit/test_faults.py::test_emit_to_two_files` | fails (`assert 2 == 0`) | pre-existing |
| `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date` | fails (extra `ingest_date` dictionary column) | pre-existing |
| `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references` | fails when `tests/demo/fabric/test_udf.py` is collected first (`azure.functions` already imported) | pre-existing order dependence; fails in both kernels |
| `tests/cli/test_generate_to.py::test_to_postgresql_routes_to_the_database_sink` | not run under load on INT-15 | failed once in the rust run only ("dictionary changed size during iteration" in the in-memory server); passes alone, in the python run, and in `tests/cli tests/security` (392 passed). Likely load-sensitive; **not proven pre-existing**. |

Not run: emulator and live tests, the Windows and macOS CI legs, `dbt deps` from the hub (see gaps above), the dbt-build
tests (the hub is unreachable from this sandbox, so they error at setup here; round 1 ran them with local packages),
a CI run of the `ci.yml` diff. Housekeeping: the lock generator wrote `/lock` at the filesystem root (empty `TMPDIR`);
the sandbox blocked its removal, so the lead can delete it.
