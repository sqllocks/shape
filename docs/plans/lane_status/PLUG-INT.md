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
