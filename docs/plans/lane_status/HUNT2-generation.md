# HUNT2-generation: second bug hunt of the generation area (lane/HUNT2-generation)

Status: **done; awaiting lead verification.** Branch `lane/HUNT2-generation`, from `int/INT-17`
(8683435d). Area: `src/shape/generation/**`, `src/shape/builtins/strategies/**`, `src/shape/spec/**`,
`src/shape/packs/**`, `plugins/shape-domains/**` and their tests. The first audit of the area
(`lane/AUD-gen`, findings 1 to 73) is **not** in `int/INT-17`; none of its findings was re-filed.

Every fix has its regression test committed first, failing, with the failing output in the commit
message (negative and boundary cases included). All regression tests are in
`tests/generation/test_hunt2_generation.py`. No gate, tolerance, D-xx/T-xx decision, §11 or §2.3
row, `.github/workflows` file or `$REFENGINE_ROOT` file was changed; no test was skipped, xfailed or
weakened; no existing test's expectation was changed.

## Findings

Severity: high / medium / low. "Fix" is the commit on this branch; the failing test is the commit
before it (named "failing test(s) for #n").

| # | Sev | Where | Defect | Issue | Fix |
|---|---|---|---|---|---|
| 1 | high | `schemas/generation-schema-v1.json` | `shape migrate` of a generation schema adds `migrated_from`/`source_content_id`, which the loader and the spec schema refuse: the migrated file cannot be generated or validated | #651 | ba22e8e0 |
| 2 | medium | `generation/evolution.py:19` | `interpolate` lists columns as a set union: `ShapeTimeline.generate_at` gives different columns order and values per process (`PYTHONHASHSEED`) | #652 | 1f093ba1 |
| 3 | medium | `builtins/strategies/linked.py:116` | `conditional` `{"fixed": "02134"}` becomes `"2134"`; `"007"` a double 7.0; `"nan"` NaN | #653 | de7f7a4d |
| 4 | medium | `generation/fit.py` | `generate --from` a merged profile ignores the merge's exact top values; text columns get name-guessed values (`state` WA/OR/CA → US state names); also for merged datasets | #682 | 0d633b9c, f300e28d |
| 5 | medium | `builtins/strategies/joint_table.py:88` | `conditional_table` ignores `output_type: "string"`: profile-fitted ZIP labels lose leading zeros | #693 | 5399d0b0 |
| 6 | medium | `generation/engine.py:540` | an unknown scale preset (or any scale on a schema without presets) silently becomes 100 rows a table (`shape.generate("retail", scale="mediun")`) | #717 | e05d584c |
| 7 | medium | `generation/schema.py`, `engine.py:184,595` | a foreign-key cycle passes `validate`/`shape validate`; `--dry-run` raises (exit 2, no JSON) instead of reporting; the message prints a set in hash order | #733 | 24ba5657 |
| 8 | medium | `generation/drift_plan.py:320` | `generate-drift` writes the days before a failing event, then stops with no `ground_truth.json`; the `distribution` message does not say what is needed | #737 | 05deef0d |
| 9 | medium | `plugins/shape-domains/.../_packaged.py:46` | a changed domain definition changes every later load in the process (open issue from an earlier lane) | #343 | bf166e8e |
| 10 | low | `generation/spec_edit.py` | `SpecDocument.save` leaves the spec with mode 0600 (new or existing); nesting ~500 deep raises `RecursionError` instead of `SpecError` | #654 | 85f35baf |
| 11 | low | `builtins/strategies/locale_pack.py:105`, docs | the install hint names `sqllocations-shape-domains`, which does not exist | #655 | aff34f34 |
| 12 | low | `generation/schema.py:464` | `validate()` accepts a NaN `null_rate` and a negative `max_length` (which cuts characters off the end) | #656 | daebd5e1 |
| 13 | low | `generation/drift_plan.py` | drift plans cannot declare `format`/`version` (refused as unknown keys; a newer plan is not called newer); `ground_truth.json` has no `format` | #703 | 80f1ad5b |
| 14 | low | `builtins/strategies/keys.py:109,287` | `foreign_key` / `composite_foreign_key` fail on an empty child of an empty parent (open issue from the first audit) | #220 | 0b638570 |
| 15 | low | `packs/domains.py` | domain definition files declare no `format`; their `version` is the domain's own release, so the unified key cannot be added without a decision | #711 | open (below) |

### Out of area (filed only)

| Where | Defect | Issue |
|---|---|---|
| `streaming/emit/sinks.py` + `shape-healthcare-standards` | the FHIR emitter claims the `file` scheme and sorts first, so with the plugin installed every `emit --to file://` fails (`tests/streaming/emit/test_faults.py::test_emit_to_two_files` fails on `int/INT-17` too) | #732 |

Already filed by other lanes and seen again here (not re-filed): JSON Lines sink writes `NaN` (#630);
`from-ddl` keeps only the last of two same-named tables (#615, in `generation/ddl.py`: left for the
lead because `lane/AUD-gen`, not yet integrated, rewrites the same code for #200).

## Left open, and why (for the lead)

| Issue | What | Why |
|---|---|---|
| #711 | `shape.packs` domain files have no `format`, and `version` is the domain's semantic version | Adding the unified `version` key needs a rename of the domain's own version field: an owner decision on the file layout. |
| #703 (rest) | `shape-drift-plan` and `shape-drift-ground-truth` are not in `shape.compat`'s kind table or `docs/specs/STATE_AND_COMPATIBILITY.md` | `src/shape/compat.py` is outside this lane's paths; the fix declares and checks the keys locally in `drift_plan.py`. |
| #219 | seasonal temporal almost never draws the end day when a profile names months outside the range | `_day_weights` spreads empty buckets "over every day but the last" on purpose (the docstring says so) and retail's seasonal columns depend on it: changing it changes bytes the equivalence verifier compares, so it needs a decision. |
| #615 | `from-ddl` silently keeps the last of two `CREATE TABLE` of one table / two equal column names | `generation/ddl.py` is rewritten by `lane/AUD-gen` (#200, #197 and others), which is not integrated yet; a fix here would conflict. |
| #717 note | `cli/generation.py::_check_scale` still has its own check | Redundant now that the engine refuses; left as is (CLI paths are another lane's). |

## Changes outside the five area paths

* `src/shape/schemas/generation-schema-v1.json` and the generated `generation-spec-v1.schema.json`
  (rebuilt with `python -m shape.generation.spec_schema`), and the copy the VS Code extension bundles
  (`editors/vscode/schemas/generation-spec-v1.schema.json`, kept byte-equal by
  `tests/editors/test_vscode_extension.py`): #651.
* Docs: `docs/GENERATION_SPEC.md` (#651, #654), `docs/GENERATION_STRATEGIES.md` (#655, #693),
  `docs/LOCALES.md` (#655), `docs/PROFILE_MERGE.md` (#682), `docs/DRIFT.md` (#703),
  `docs/GENERATION_ENGINE.md` (#717; also the stale "`generate --from` ... exits 2 for now" line).
* `CHANGELOG.md` (Fixed).

## Determinism and equivalence

* Cross-process: every `tests/generation/spec_compat/*.json` spec (all built-in strategies) hashes the
  same under `PYTHONHASHSEED` 1 and 77; retail, healthcare and financial hash the same under three
  hash seeds and three `TZ` values; ten domain/seed jobs run on 8 threads at once match the
  sequential run (both kernels).
* Chunk layout: every spec_compat spec gives the same columns for `chunk_rows` 100000, 101 and 1, in
  both kernels.
* Kernels: rust and python agree on every spec_compat spec and on retail and healthcare; `iot` and
  `financial` differ only in double columns at 1 ulp (`normal`/distribution draws), the documented
  difference AUD-gen also recorded.
* Byte identity: retail `small`, seed 1042, hashes `41d04ca11a9f61d9` in both kernels before and after
  every fix that touches strategies, the engine or the domains (#653, #693, #717, #343, #220).
* Verifiers: see "Commands and results".

## Commands and results

Environment: Python 3.11.15, Rust 1.97, `$SHAPE_VENV` with `.[dev,streaming,advanced]` and every
first-party plugin (editable); the pinned baseline set up with `benchmarks/vs_refengine/setup_refengine.sh`
(`$REFENGINE_ROOT` only read). `tests/demo/fabric` is not run: its requirements pin pyarrow 19 (the
documented conflict in `lane/AUD-gen`'s status); CI runs it in its own job.

Final runs, this session, on `0752a2e9` (fresh container; plugins installed editable):

| Run | Result |
|---|---|
| `make check` lint, format, mypy, compileall, vulture, import contracts, `check_requirements`, `check_secrets`, `check_user_facing`, `check_shipped_data`, `check_plugin_skeletons`, `check_conformance_coverage` | all pass |
| `make check` main pytest (coverage) | 9198 passed, 18 skipped, **1 failed** (`tests/streaming/emit/test_faults.py::test_emit_to_two_files`, #732 below); coverage 92.96% (gate 86%) |
| `make check` rest (`-m heavy` kernel/profile/streaming; `SHAPE_KERNEL=python tests/kernel`; `cargo fmt --check`, `clippy -D warnings`, `cargo test`) | 42 passed; 265 passed; clean; clean; 34 passed |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric` | 9278 passed, 18 skipped, **1 failed** (#732) |
| `SHAPE_KERNEL=python` same scope (three shards: generation+profile; streaming+demo+cli; the rest) | 2675 + 816 + 5787 = 9278 passed, 18 skipped, **1 failed** (#732) |
| `benchmarks/vs_refengine/run.py --quick --only generate` | exit 0; every verifier `VERDICT: PASS` (retail small and medium, 60/60 columns equivalent). Timings not recorded: the run was not under the §1.4 lock/load check on the baseline machine, so `results.json` was left unchanged. |

The only failure is #732 (out of area, filed; fails on `int/INT-17` too). The 18 skips are
`great_expectations`/`pandera` not installed (optional extras), as on the base branch.

### Test-fake race fixed on the way (out of area: `plugins/shape-databases`)

The first `make check` run here also failed
`tests/cli/test_generate_to.py::test_to_postgresql_routes_to_the_database_sink` with "dictionary
changed size during iteration" (also seen once by PLUG-INT, never root-caused). Cause: `generate
--to` writes each table on its own thread into one `shape_databases.testing.FakeServer`, whose
`begin()` deep-copies the table dict while another thread's `CREATE TABLE` adds to it. The product
sink is not affected; the in-memory test server was not thread-safe. Failing test 93fc6061
(`plugins/shape-databases/tests/test_sinks.py::test_tables_written_on_several_threads_into_one_server`,
fails every run, both dialects), fix 0752a2e9 (one re-entrant lock per server around every
statement, copy, commit and rollback; transaction model unchanged). The second run above has no
such failure.
