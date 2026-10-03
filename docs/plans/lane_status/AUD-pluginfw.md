# AUD-pluginfw — audit of the plugin framework, integrations, packs and location

Branch: `lane/AUD-pluginfw` (from `origin/build/main-plan`). Area: `src/shape/plugins/**`,
`src/shape/integrations/**`, `src/shape/packs/**`, `src/shape/location/**`, `integrations/**`,
their tests, `docs/plugins/**`, `docs/SCENARIO_PACKS.md`, `docs/LOCATION_AS_CODE.md`.
No gate, tolerance or D-xx/T-xx decision was changed; no test was skipped, xfailed or weakened.

## Phase 1 — baseline

`pytest -m "not emulator and not live" tests/plugins tests/integrations tests/packs tests/location`
with coverage of the four packages: 224 passed. Coverage before the audit: `plugins/schemes.py`
36%, `location/reference.py` 52%, `integrations/fabric/spark.py` 71% (the executor body needs a
Spark session), `location/core.py` 81%, `plugins/kit.py` 81%. `plugins/cli.py` reads 0% only
because its tests drive it through a subprocess.

## Findings

Severity: critical / high / medium / low. "Repro" is a minimal reproduction run in this session.

1. **high** — `integrations/adf/batch/run_gate.py:179,197` and
   `integrations/adf/batch/run_generate_gate.py:140-160`: any error that is not a `GateError`
   (a summary or check file that was not written, invalid JSON from the CLI, a `shape` command that
   is not on `PATH`, a profiling error in the generate gate) escapes as a traceback. Python exits 1,
   which the gate's documented contract defines as "contract violation", and `gate.json` is never
   uploaded, so the Lookup activity fails instead of branching. Repro: `run_gate.py --activity
   act.json --shape true` (a command that exits 0 and writes nothing) → traceback
   `FileNotFoundError: .../summary.json`, exit 1, no `gate.json`. Expected: exit 2 and a
   `gate.json` whose `error` names the failure.
2. **medium** — `src/shape/integrations/fabric/udf.py:263-273` (`profile_lakehouse_table`): the
   30,000,000-cell guard runs after `cursor.fetchall()`, so a wide table is read into memory in
   full (up to 5,000,001 rows × every column) before it is refused. Repro: a fake cursor with 100
   columns and `maxRows=1_000_000` → `fetchall` is called, then the cell error is raised.
   Expected: refused from `cursor.description` before any row is fetched.
3. **medium** — `src/shape/location/reference.py:96-103` (`load_census_gazetteer`): county and
   place records get no `state` although every Census county and place gazetteer has a `USPS`
   column, so `Location.county_scope("Franklin County", "OH")` cannot resolve against them and the
   same county name in two states is indistinguishable. Expected: `state` from `USPS`.
4. **medium** — `src/shape/packs/domains.py:239` (`test_domain`): rows that are slotted
   dataclasses (what `AddressPack.generate` returns, `GeneratedAddress`) raise
   `TypeError: vars() argument must have __dict__ attribute`. Repro:
   `test_domain(US_ADDRESS, AddressPack(...).generate(2, scope))`. Expected: the fields are read.
5. **medium** — `src/shape/location/core.py:58-62` (`WeightedLocation`): a NaN or infinite weight
   is accepted (`nan <= 0` is false); `normalized_weights` is then `(nan,)` or `(nan, 0.0)`, and
   generation silently draws from a broken distribution. Expected: `ValueError` naming the weight.
6. **medium** — `src/shape/plugins/schemes.py:69-79` (`redact`): only a password in the user
   information is hidden. Credentials in the query string (`?sig=` of a SAS URL,
   `password=`, `AccountKey=`, `SharedAccessKey=`) are printed verbatim in sink error messages and
   in `shape generate --to` reports. Repro: `redact("https://a.blob.core.windows.net/c?sv=1&sig=S")`
   returns `S` unchanged.
7. **low** — `src/shape/packs/domains.py:34-40` (`_version_key`): `1.0.0-rc1` sorts after
   `1.0.0`, so `DomainRegistry.get(name)` returns the pre-release as the latest. The docstring says
   a non-numeric part sorts before a number in the same position; a release has no part there.
8. **low** — `src/shape/packs/domains.py:221-229` (`load_domain`): a malformed file raises a bare
   `KeyError: 'version'`, `TypeError: DomainField.__init__() got an unexpected keyword argument`,
   or a `JSONDecodeError` without the path. Expected: `ValueError` naming the file and the key.
9. **low** — `src/shape/location/core.py:127-160` (`location_from_spec`): (a) ZIP+4 written with a
   dash (`"43215-0001"`) is refused although the nine-digit form is accepted; (b)
   `{"zip": None, "postal_code": "43215"}` becomes postal code `"None"`; (c) `" , "` becomes a city
   and a state of `""`, which the resolver treats as wildcards (ambiguity instead of an error);
   (d) non-ASCII digits (`"٤٣٢١٥"`) pass `str.isdigit` and become a postal code.
10. **low** — `src/shape/plugins/cli.py:46-58` (`describe`): `shape plugins info "<discovery>"`
    after a metadata failure raises an uncaught `KeyError` (traceback). `cli.py:36-43`
    (`find_records`): with a duplicate registration, the "ambiguous" message lists the same
    `group:name` twice.
11. **low** — `src/shape/plugins/cli.py:135-136` (`run_command`): a plugin command that calls
    `sys.exit("message")` exits 1 with nothing on stderr; Python prints that message, and
    `docs/plugins/host.md` promises one error line.
12. **low** — `src/shape/plugins/kit.py:603-606` (`main`): `--samples MODULE:ATTR` naming a module
    or attribute that does not exist ends in a traceback instead of the documented exit 2.
13. **low** — `src/shape/integrations/fabric/run_folder.py:37-46` (`parse_run_folder`): a name of
    the right shape with an impossible date or time (`20261340T000000Z`, `20260230T000000Z`) raises
    `ValueError` instead of returning `None` ("not a run folder").
14. **low** — `src/shape/location/reference.py:72-74`: the delimiter sniff reads the whole file
    (`read_text()[:4096]`) to look at 4 KB; a large gazetteer is held in memory twice. And an
    unknown `kind` is accepted silently, producing records with only an id.
15. **low** — `src/shape/packs/person.py:12` (`generate_person`): `random.Random` seeds with the
    absolute value of an int, so seed `-1` and seed `1` give the same person for row 0.
16. **low** — `src/shape/location/core.py:163-174` (`scope_from_specs`): a length mismatch raises
    `ValueError("weights")`, which does not say what is wrong; the scope is built twice.

Not defects, recorded only:

- `packs/domains.py` `save_domain`/`load_domain`: tuple constraints (`US_ADDRESS`'s
  `latitude_range`) come back as lists, so the round trip is not `==`. JSON has no tuple; changing
  the persisted shape is not an audit fix. The domain JSON has no `format`/integer `version` key;
  adding one is a format change for the lead to decide.
- `host.check_api` accepts `"1"` (no minor); rejecting it would break loading plugins that load
  today.
- `docs/SCENARIO_PACKS.md` describes `shape.scenario`, which is outside this area.

## Phase 2 — issues and Phase 3 — fixes

Every defect above was filed in `sqllocks/shape` and fixed on this branch: a regression test that
failed first (its failing output is in the test commit's message), then the fix.

| # | sev | issue | test commit | fix commit |
|---|---|---|---|---|
| 1 | high | #366 ADF gate scripts exit 1 / no gate.json on an unexpected error | c9331c4 | ab0fb1f |
| 2 | medium | #367 profileLakehouseTable reads every row before the cell guard | 272235e | ba4b6a0 |
| 3 | medium | #368 Census county/place records have no state | 2c7e709 | 0f3b202 |
| 4 | medium | #369 test_domain on slotted dataclass rows | 6b65502 | a701c2d |
| 5 | medium | #370 NaN / infinite location weights | 12f31fe | 93cbd7d |
| 6 | medium | #371 redact keeps query-string credentials | eabf30c | cc46978 |
| 7 | low | #372 pre-release newer than its release | 85a9154 | db90a06 |
| 8 | low | #373 load_domain bare KeyError/TypeError | 8b2c779 | 5372af2 |
| 9 | low | #374 location_from_spec edge cases | 7fff873 | 29129ab |
| 10 | low | #375 plugins info: discovery record, duplicates | 915fa4d | cdb6cd8 |
| 11 | low | #376 sys.exit("message") lost | 915fa4d | 98fd4c4 |
| 12 | low | #378 kit --samples traceback | 915fa4d | cabcd39 |
| 13 | low | #379 parse_run_folder raises on an impossible date | 8763fb9 | 7385750 |
| 14 | low | #380 gazetteer whole-file sniff, unknown kind | 3cd563b | 929b735 |
| 15 | low | #381 person seeds of opposite sign collide | 633d23e | 3777675 |
| 16 | low | #382 scope_from_specs "weights" error | 19a0c4e | f525228 |

Notes on the fixes:

- #366: both scripts keep `GateError` messages as they were; any other exception becomes an error
  gate `"<Type>: <message>"` with exit 2, and `main` maps a crash to exit 2. `load_settings` also
  refuses a `typeProperties` that is not an object. The PF-04/PF-06 tests in `tests/demo/fabric`
  (`test_adf.py`, `test_generate_adf.py`) still pass (48 passed).
- #367: rows are read with `fetchmany(10_000)` when the cursor has it (DB-API), and the read stops
  as soon as the first `maxRows` rows pass the limit; the row after `maxRows` (which only tells
  that the table is larger) never counts against it. A cursor without `fetchmany` is read as before.
- #381: outputs for a non-negative seed and a row in `[0, 2**64)` are unchanged (same seed
  expression); only the colliding inputs take a text seed.
- #374, #370: inputs that were valid give the same `Location`s and weights, so the address
  strategy's output for valid scopes is unchanged.

Improvements (behaviour-preserving, one commit each): tests for `uri_scheme`, `local_path`,
`sinks_by_scheme` and the `require_scheme` messages (706ea59; `plugins/schemes.py`
coverage 36% → 94%); unused `tempfile`/`Path` imports removed from the Synapse generate notebook and its
builder (e3b3ab5; `tests/demo/fabric/test_generate_synapse.py` and `test_synapse.py`: 46 passed).

## Left open, and why

- `ruff check integrations` still reports 14 E501 lines in `integrations/**` builders and two
  generated notebooks. `integrations/` is outside the T-27 lint scope, and the long lines are inside
  generated notebook sources: shortening them changes the committed notebooks, which their
  staleness tests compare with the builders. Not a defect; left for whoever next regenerates them.
- The "not defects" list in the findings above (tuple constraints, no `format` key in the domain
  JSON, `SHAPE_API = "1"`) needs a lead decision, not an audit fix.
- No workflow change is needed for this lane.

## Commands and results

Environment: §1 setup (`pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains`,
`tests/demo/fabric/requirements.txt`, unixODBC, the pinned Spindle at 422e78d). All run in this
session on the final branch head.

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_spindle` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_spindle` | 1090 files already formatted |
| `mypy` | no issues in 436 source files |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `lint-imports` | 1 kept, 0 broken |
| area tests with coverage (`tests/plugins tests/integrations tests/packs tests/location`) | 280 passed; total coverage of the four packages 80% → 89% |
| `pytest tests/demo/fabric/test_adf.py tests/demo/fabric/test_generate_adf.py` | 48 passed |
| `pytest tests/demo/fabric/test_generate_synapse.py tests/demo/fabric/test_synapse.py` | 46 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 7134 passed, 2 skipped, 6 failed |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 7134 passed, 2 skipped, 6 failed (the same 6) |

The 6 failures are not from this lane: each one fails the same way on `origin/build/main-plan`
(a worktree of it, same venv, run in this session), and none is in this area:

- `tests/demo_cmd/test_notebook_and_outputs.py` (2): the semantic model needs the `shape-fabric`
  plugin, which this venv does not install (CI's test job does not either; the job that does is
  the plugins job).
- `tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`:
  reading a hive-partitioned folder back adds an `ingest_date` dictionary column with this pyarrow.
- `tests/kernel/test_hashing.py` (2): float16 with this pyarrow/numpy (`if_else` has no halffloat
  kernel; `Expected np.float16 instance`).
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`:
  passes alone (41 passed); in a full run an earlier, pre-existing test imports the Fabric SDK from
  `tests/demo/fabric/requirements.txt`, which pulls in `azure.*`. The same ordering fails on the
  base branch (`test_fabric_udf_helpers.py` then this file: 1 failed). This lane's new tests do not
  import `azure` (102 passed with this file last).

No equivalence verifier compares an output this lane changed: the verifiers under
`benchmarks/vs_spindle` do not use `shape.location`, `shape.packs`, `redact` or the changed
integration code paths, and the address strategy's output for valid scopes is unchanged.
`$SPINDLE_ROOT` was not modified. No workflow file was edited. No PR was opened.
