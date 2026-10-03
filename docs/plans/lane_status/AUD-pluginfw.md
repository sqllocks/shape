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

## Phase 2 — issues

(filled in below as filed)

## Phase 3 — fixes

(filled in below)
