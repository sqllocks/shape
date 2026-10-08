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

## Second pass (after merging int/INT-18)

INT-18 brought W1-18 (`plugins/trust.py`, the allow-list in `plugins/host.py`, `plugins sign`,
`verify` and `allowlist init`), the `Behavior` protocol and kit check, and the dbt notebook and
pipeline. Lane `HUNT2-plugins` had already filed #579-#588 against that code and fixed all but #583
on its own branch (not yet in INT-18), so those were not re-filed or re-fixed here: fixing
#581/#585/#587 a second time would only make the two branches conflict. Baseline after the merge:
`tests/plugins tests/integrations tests/packs tests/location` 480 passed, 3 skipped, 1 failed
(finding 19); `plugins/trust.py` 90%, `plugins/host.py` 99%.

17. **high** — `src/shape/plugins/host.py` `default_host()` (#583, filed by HUNT2-plugins):
    `plugins: {allowlist: PATH}` in `shape.yml` is documented as switching on the allow-list, but
    the process-wide host never read `shape.yml`. An organisation that configures it there has no
    allow-list at all, and nothing tells it so (it fails open). Repro: `shape.yml` with
    `plugins: {allowlist: allow.json}` and an empty `allow.json`; `default_host().record(...)`
    shows `unloaded`. Expected: `blocked`.
18. **medium** — `host.py` `_discover` (#750): under an allow-list, a plugin that is not permitted
    and registers a built-in's `(group, name)` with an entry-point value that sorts first (say
    `shape.sinks: parquet = acme_mod:Det`) wins the duplicate rule and is then blocked, so the
    built-in Parquet sink can no longer load (`PluginBlockedError`). The docs say built-ins are
    always allowed.
19. **medium** — `host.py` `_enforce`, `cli.py` `build_allowlist` (#751): built-ins were recognised
    by the distribution name alone, which a distribution states about itself. A
    `sqllocks_shape-99.0.dist-info` whose entry point names `acme_mod:Det` was exempt from the
    allow-list and was imported.
20. **medium** — `tests/plugins/test_trust_reach.py::test_no_plugin_starts_a_subprocess_or_opens_a_socket`
    fails on `origin/int/INT-18` (#752): W2-10's `plugins/shape-fabric/.../kerberos.py` runs
    `kinit`, but `docs/plugins/trust-model.md` says no first-party plugin starts a subprocess.
    **Left for the lead.** Making it pass means changing an existing test's expectation and the
    statement, or changing shape-fabric (outside this area).

| # | sev | issue | test commit | fix commit |
|---|---|---|---|---|
| 17 | high | #583 (plugin-side part) | f7de2395, 12fb0f20 (JSON-style shape.yml) | 4d4fe03c, 74db11f4 |
| 18 | medium | #750 | f7de2395 | 06098708 |
| 19 | medium | #751 | f7de2395 | e16f91b5 |
| 20 | medium | #752 | — | resolved on int/INT-18 by the lead (688e577a); green after the final merge |
| 21 | high | #758 | existing tests, red on INT-18 | resolved on int/INT-18 (688e577a); `tests/demo/fabric` 234 passed after the final merge |
| 22 | low | #759 | existing test, red on INT-18 | bd4b78d3; INT-18 made the same fix, its wording kept in the final merge |
| 23 | low | #760 | existing test, red on INT-18 | resolved on int/INT-18 (688e577a) |

21. **high** — #758: since W1-01, `integrations/fabric/generation.domain_contract` stamps the
    multi-table contract with `format`, `version`, `shape_version` and `min_shape_version`.
    `contracts.v1.check` refuses any top-level key except `tables` and `drift` beside `tables`, so
    every Fabric, ADF, Synapse and dbt generate-and-gate run fails with exit 2. That is
    `tests/integrations/test_fabric_generation.py` (11 tests) and about 20 in `tests/demo/fabric`,
    all red on `origin/int/INT-18`. The fix belongs in `src/shape/contracts/v1.py` (leave the
    declaration keys out of `beside`). That file is outside this area, and un-stamping the contract
    would undo W1-01.
22. **low** — #759: the shape-kafka row of `trust-model.md` did not name `json-schema.org`, the
    `$schema` identifier in `shape_kafka/formats.py`, so `test_trust_reach.py::test_fixed_hosts_are_listed[shape-kafka]`
    was red. Fixed in the statement.
23. **low** — #760: `test_trust.py::test_list_json_through_the_cli` (W1-18) reads `plugins list
    --json` as a bare list; W1-14 now wraps it in `shape-result` with the rows under `payload`.
    Changing the test is an expectation change, so it is left for the lead. The diff:

    ```diff
    -    rows = json.loads(r.stdout)
    +    rows = json.loads(r.stdout)["payload"]
    ```

Notes: `project_plugins_config()` reads the nearest `shape.yml`, found the way `find_project` finds
it, with safe YAML. A file that never mentions `plugins` changes nothing, even if it is not valid
YAML. A `plugins` section that cannot be read blocks every non-built-in plugin, and the reason
names the file. `SHAPE_PLUGIN_ALLOWLIST` and the `allowlist=` argument still take precedence.
`host.md` and `trust-model.md` document the duplicate rule under an allow-list and the shape.yml
lookup.

Open for the lead (second pass):

- #583: the `plugins` key in `src/shape/schemas/shape-project-v1.schema.json` (project code). Until
  it is added, `shape project validate` refuses the documented key.
- #752: as above.
- `mypy` reports 3 errors on the merged head, all inherited unchanged from `origin/int/INT-18`
  (`src/shape/drift/engine.py:1078,1081` and `src/shape/contracts/v1.py:559`). This branch does not
  touch those files.
- The fixes for #579-#582, #584-#588 are on `lane/HUNT2-plugins`. `tests/plugins/test_trust.py`
  (this lane) and `tests/plugins/test_hunt2_plugins.py` (that lane) do not overlap. `host.py` is
  changed only here.

## Commands and results

Environment: §1 setup (`pip install -e '.[dev,streaming,advanced]' -e plugins/shape-domains`,
`tests/demo/fabric/requirements.txt`, unixODBC, the pinned RefEngine at 422e78d). All run in this
session on the final branch head.

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1090 files already formatted |
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
`benchmarks/vs_refengine` do not use `shape.location`, `shape.packs`, `redact` or the changed
integration code paths, and the address strategy's output for valid scopes is unchanged.
`$REFENGINE_ROOT` was not modified. No workflow file was edited. No PR was opened.

## Commands and results (second pass, merged head 530c31c9 + fixes)

Run in this session in a new venv (§1: `pip install -e '.[dev,streaming,advanced]' -e
plugins/shape-domains`, `tests/demo/fabric/requirements.txt`, `cryptography`, `pyyaml`, unixODBC;
RefEngine cloned at 422e78d), with a private `TMPDIR`.

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 1623 files already formatted |
| `mypy` | 3 errors, all inherited unchanged from `origin/int/INT-18` (`drift/engine.py:1078,1081`, `contracts/v1.py:559`) |
| `lint-imports` | 1 kept, 0 broken |
| `python scripts/check_user_facing.py` (D-13) | clean |
| `pytest tests/plugins/test_trust.py` | 81 passed (4 new regression tests failed first: f7de2395, 12fb0f20) |
| `pytest tests/plugins/test_trust_reach.py` after #759 | 24 passed, 1 failed (#752, for the lead) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 12628 passed, 24 skipped, 161 failed |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 12628 passed, 24 skipped, 161 failed (the same 161) |

Every one of the 161 failures also fails on `origin/int/INT-18` (a worktree of it, the same venv,
run in this session: 161 failed of the 161 listed, none only on this branch). They are integration
conflicts between merged lanes. The ones in this area are #758 (stamped contract refused: 11 in
`tests/integrations`, about 20 in `tests/demo/fabric`), #759 (fixed afterwards), #760 and #752. The
rest are outside the area, mostly W1-14's `shape-result` envelope against tests that read the old
`--json` output (`tests/bridge`, `tests/cli`, `tests/quality`, `tests/rules`, `tests/history`,
`tests/streaming/emit`). The full runs started before the #759 fix and before `plugins/shape-dbt`
was installed; 7 of the `tests/demo/fabric/test_dbt.py` failures were `No module named 'shape_dbt'`
in this venv, and they fail on #758 once it is installed.

No equivalence verifier compares an output this pass changed. Only plugin discovery and
allow-list code (`plugins/host.py`, `plugins/trust.py`, `plugins/cli.py`) and docs changed.
`$REFENGINE_ROOT` was not modified, no workflow file was edited, and no PR was opened.

### Final run on the last merge of int/INT-18 (1c70c951 + 1a29a7e4)

| Command | Result |
|---|---|
| `ruff check` / `ruff format --check` on `src tests plugins benchmarks/vs_refengine` | All checks passed / 1714 files already formatted |
| `mypy` | Success: no issues in 637 source files |
| `python scripts/check_user_facing.py` | clean |
| area tests (`tests/plugins tests/integrations tests/packs tests/location`) | 590 passed, 3 skipped (`shape_behavior` is not installed in this venv) |
| `pytest tests/demo/fabric` | 234 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live"` | 14233 passed, 24 skipped, 6 failed |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live"` | 6 failed, the same as rust. The run went over the 2-hour session limit at 83%, so the last 2,338 tests (50 of them overlapping) were run as a second invocation: 83% with 5 failed, then 2336 passed, 1 skipped, 1 failed |

The 6 failures fail the same way on `origin/int/INT-18` (a worktree, the same venv, run in this
session), and none is in this area: `tests/bridge/test_vectors.py` `[report_card]` and
`[report_card_read]`, `tests/cli/test_dry_run.py::test_a_sink_is_a_send_with_the_secret_redacted`,
`tests/demo_cmd/test_notebook_and_outputs.py` (2; the semantic model needs the `shape-fabric`
plugin, which this venv does not install), and
`tests/test_removed_modules.py::test_removed_module_is_not_importable[history]`.

Left open by this lane: the project schema key of #583 (outside the area). Everything else in the
area is fixed here or was resolved on int/INT-18.

### After the last merge of int/INT-18 (bbde91a9)

The merge was clean. `ruff check` passed, `ruff format --check` (1715 files) passed, `mypy` reports
no issues in 637 files, and D-13 is clean.

- `SHAPE_KERNEL=rust pytest -m "not emulator and not live"`: 14240 passed, 24 skipped, 6 failed.
- `SHAPE_KERNEL=python`, in two invocations to fit the 2-hour session limit:
  - first half of the test files: 7119 passed, 20 skipped, 5 failed;
  - second half: 7130 passed, 4 skipped, 1 failed;
  - the same 6 failures as rust.

The 6 also fail on `origin/int/INT-18` at bbde91a9 (a worktree, the same venv). The two
`report_card` bridge vectors pass alone. In file order up to `tests/bridge/test_vectors.py` they fail
on the base too (2 failed, 1454 passed): a fidelity `mixture_fit_columns` difference that depends on
test order. None of the 6 is in this area.
