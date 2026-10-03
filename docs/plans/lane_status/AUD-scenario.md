# AUD-scenario: audit and fix of scenario packs, GSL and `shape demo` (lane/AUD-scenario)

Status: **phase 1 (hunt) done; filing and fixing in progress.**

Area: `src/shape/scenario/**`, `src/shape/demo/**`, `demo/**`, `tests/scenario/**`, `tests/demo/**`,
`tests/demo_cmd/**`, `docs/DEMO.md`. Branch started from `origin/build/main-plan` 5c91ea5.

## Baseline (before any change)

- Environment: §1 venv with `.[dev,streaming,advanced]`, every plugin under `plugins/`,
  `tests/demo/fabric/requirements.txt`, unixODBC, and the pinned Spindle (§1.2,
  `benchmarks/vs_spindle/setup_spindle.sh`, "spindle ok").
- `pytest tests/scenario tests/demo tests/demo_cmd -m "not emulator and not live"
  --cov=shape.scenario --cov=shape.demo`: **561 passed**, total coverage 93%. Lowest:
  `demo/services.py` 40% (the plugin calls; covered by the shape-fabric tests), `demo/__init__.py`
  60%, `demo/modes/streaming.py` 84%, `scenario/validator.py` 89%.

## Findings

Severity: critical / high / medium / low. Line numbers are those of 5c91ea5.

1. **medium: the run id is a file name built from unchecked text (path traversal).**
   `src/shape/scenario/manifest.py:128`, `src/shape/scenario/runner.py:184`. The run id is
   `{time}_{domain}_{scale}_s{seed}` and the manifest is written to `<out>/<run_id>_manifest.json`.
   The scale is only checked against the domain's presets when it has some (`runner.py:128`), and the
   domain name of a schema file is free text.
   Repro: a schema file with no `scales`, `PackRunner().run(pack, schema, scale="../../evil",
   base_path=root/"a"/"b")` writes `root/a/b/evil_s42_manifest.json` through a created directory
   `root/a/b/<time>_retail_..`; more `../` leaves the output directory. Expected: the manifest is
   always written inside the output directory under a plain file name. Actual: it is written where
   the scale (or domain) points.

2. **medium: the `chaos` section is not validated.** `src/shape/scenario/validator.py:204-206,
   235-257`, `src/shape/scenario/runner.py:299-317`, `src/shape/scenario/resolve.py:342-344`.
   - A value of the wrong type crashes validation and the run with a bare `ValueError`/`TypeError`
     that names no key: `chaos: {enabled: true, seed: abc}` gives `invalid literal for int() with
     base 10: 'abc'`; `warmup_days: [1]` gives `int() argument must be ... not 'list'`; `day: x`
     passes validation and crashes the run. `docs/SCENARIO_PACKS.md` promises "a value of the wrong
     type is an error that names the key".
   - `enabled: "false"` (quoted text) is truthy, so chaos is applied (`chaos_applied` true).
   - A spec's chaos (`PackRunner.run(..., spec=...)`) is never validated by the run: only the pack's
     is. `validate_spec` crashes the same way on a spec `chaos: {enabled: true, seed: abc}`.
   - An unknown chaos key (`intensty: stormy`) is silently ignored, although "keys no field takes are
     warned about, so a mistyped key does not silently change nothing".
   Expected: each problem is a validation error (wrong type) or warning (unknown key) naming
   `chaos.<key>`; a run with an invalid chaos section fails validation. Actual: as above.

3. **medium: an interrupted demo run leaves its artifacts unrecorded.**
   `src/shape/demo/orchestrator.py:67-68`. `KeyboardInterrupt` is re-raised before the session is
   saved, so the tables a seeding run already wrote (a session folder, Warehouse or Eventhouse
   tables) are in no session record and `shape demo cleanup` cannot find them.
   Repro: a seeding run to a local profile whose `ScaleRouter.run` writes the first table, then raises
   `KeyboardInterrupt`: no `demo-*.json` exists afterwards, and the folder holds the table.
   Expected: the session is saved (failed, "interrupted") with what was written, so cleanup can
   remove it, then the interrupt propagates. Actual: nothing is saved.

4. **medium: an unknown file format is written as CSV without a word.**
   `src/shape/scenario/runner.py:229,260` (`_FILE_FORMATS.get(..., "csv")`); the validator does not
   check `formats`. Repro: `file_drop: {formats: [delta]}` (or `Parquet`, `avro`, `xlsx`) validates
   with no error or warning and writes `customer.csv`. Expected (docs/SCENARIO_PACKS.md: "the first
   one is written: parquet, csv, jsonl (json = jsonl)"): an unknown format is a validation error.
   Actual: silently CSV.

5. **low: the same-second suffix truncates the run id when it contains `_x`.**
   `src/shape/scenario/runner.py:188`: `run_id.rsplit('_x', 1)[0]` cuts at the last `_x`, which is in
   the scale for `xlarge`, `xxl` and `xxxl` (retail presets) or in a domain name. Repro: two runs
   `retail` at `xlarge` in the same second: the second id is `<time>_retail_x2` (scale and seed
   lost). Expected `<time>_retail_xlarge_s42_x2`.

6. **low: two topics with the same name and event type are counted twice and overwrite each
   other.** `src/shape/scenario/runner.py:405-415`. Repro: a stream pack with the topic
   `{name: order, event_type: e}` twice: `events_emitted` is 10,000 for a 5,000-row table, the file
   is listed twice in `files_written` and in the manifest's `file_paths`. Expected: a validation error
   (the second topic writes the same file). Actual: double count.

7. **low: a NaN rate passes validation; a hybrid stream rate is never checked.**
   `src/shape/scenario/validator.py:165` (`nan <= 0` is false), `validator.py:169-174`. Repro:
   `streaming.cadence.rate_per_sec: .nan` and `hybrid.stream.rate_per_sec: -1` validate clean.
   Expected: "rate_per_sec must be positive".

8. **low: a name with a trailing line break passes `check_name`.** `src/shape/demo/home.py:115,135`:
   `re.match` with `$` accepts `"abc\n"`, so a session id or profile name can end in a line break
   (a file `demo-abc\n.json`). Expected: refused as not a plain name.

9. **low: a session record that is not a JSON object crashes `load`.**
   `src/shape/demo/manifest.py:95-98`. Repro: `demo-abc.json` holding `null`, `5` or `"s"`:
   `AttributeError: 'NoneType' object has no attribute 'pop'` from `shape demo status abc`.
   Expected: `DemoError: ... is not a demo session record`.

10. **low: `ManifestBuilder.from_file` accepts a non-integer or impossible version and gives no
    path for bad JSON.** `src/shape/scenario/manifest.py:187-200`. Repro: `{"version": true}` and
    `{"version": 0}` load; `{bad` raises a `JSONDecodeError` that does not say which file.
    Expected: `version` must be an integer ≥ 1 (a bool is not one); a parse error names the file.

11. **low: `params_from` turns the text `"false"` into True and crashes on a non-list.**
    `src/shape/demo/api.py:42-45,63-65`. Repro: `params_from({"dry_run": "false"}).dry_run` is True;
    `params_from({"domains": 5})` raises `TypeError: 'int' object is not iterable` (not a
    `DemoError`). Expected: a flag must be true or false (`DemoError` otherwise), a list setting
    must be text or a list.

12. **low: the comparison page and the semantic model overwrite a file of the same name, and the
    cleanup of the older session deletes the newer one's.** `src/shape/demo/charts.py:100-101,130`,
    `src/shape/demo/cleanup.py:318-321`. Repro: two inference runs with `--output charts` into one
    `--output-dir`; `shape demo cleanup FIRST` removes `retail_charts.html` written by the second
    (and a file of that name that was there before the first run is overwritten). docs/DEMO.md: "a
    demo never overwrites your data". Expected: a run does not overwrite an existing file.

13. **low (pinned by a test, not fixed: for the lead): an inference run that compares nothing
    scores 100%.** `src/shape/demo/fidelity.py:100-101` returns 1.0 when no column was compared.
    `tests/demo_cmd/test_inference_streaming.py:145` asserts exactly that
    (`FidelityReport(real, profile_from_dict({"tables": {}})).overall_score() == 1.0`), so changing it
    changes an existing test's expectation. Suggested: `None` (no score) or 0.0, with the test
    changed by the lead.

14. **low: streaming mode silently streams only the first of several domains.**
    `src/shape/demo/modes/streaming.py:308-309`. Repro: `demo_run({"mode": "streaming", "domains":
    "a,b"})` streams `a` and says nothing about `b`. Inference refuses more than one domain
    (`inference.py:50-55`). Expected: the same refusal.

15. **low: the session id is not checked for a collision.** `src/shape/demo/manifest.py:33,68`: an
    8-hex-digit random id; a second session with the same id replaces the first record (whose
    artifacts then cannot be cleaned up). Expected: a new session never takes an id that has a record.

16. **low: the landing directory is created before it is checked.**
    `src/shape/scenario/runner.py:213-216`: `mkdir(parents=True)` runs first, so a landing root that
    goes through a symbolic link out of the output directory creates directories outside it before
    the run refuses. Expected: refuse before creating anything.

17. **low: a list of names accepts `true`/`false`.** `src/shape/scenario/loader.py:261`
    (`isinstance(v, (str, int))` is true for a bool): `entities: [true]` becomes `"True"`. Expected:
    "must be a list of names".

18. **low: `demo/build_benchmark_sheet.py` checks with `assert` and reads/writes with the platform
    encoding.** Lines 74, 111, 115: under `python -O` a profile that was not identical across runs is
    published anyway; on Windows the default code page is used. Expected: an explicit check and UTF-8.

19. **low (improvement): dead branch in `validate_spec`.** `src/shape/scenario/resolve.py:358-359`
    (`elif ...: pass`).

20. **low (not fixed, outside a safe local fix): concurrent `shape demo init` can lose a profile.**
    `src/shape/demo/connections.py:131-135` reads, changes and replaces `connections.json` without a
    lock; two processes saving at once keep only one of the new profiles. A fix needs a cross-platform
    file lock; recorded for the lead.

Checked and found sound: YAML size, alias and depth limits (`shape.security.yamlsafe`), pack names in
`load_from_root`, topic and event-type names, `lakehouse_files_root` and the spec landing root
(`unsafe_path`), atomic session and profile writes, owner-only profile file, secret refusal in
profiles, quoted table names and plain-name checks before a drop, the session-folder marker, report
escaping (Markdown cells and HTML), the Spark table prefix (checked by `shape.scale.spark` before
anything is recorded), deterministic notebook cell ids.
