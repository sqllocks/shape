# Promotion plan for 0.9.3

Status: plan. Not a user-facing page. Written for the lead (session `PROMO-093`); the Owner decides the items in section 5.

Base: `int/0.9.2` at `c10923be9278bc63f2713ac0ed32a548b59a7afb`. This plan builds no code and changes no label.

## 0. Summary

- Today 24 commands are stable (`src/shape/cli/stability.py`). 60 core commands and 22 plugin-added commands are not. 93 docs pages say `Status: experimental`; 71 start with `Status: available`.
- Nothing checks a docs label. Of the 71 available pages, 15 mention an experimental command. Two are clear mislabels: `docs/DICTIONARY.md` (`shape dictionary`) and `docs/PROJECT.md` (`shape project`, `shape init`).
- No plugin command can be stable today. `cli_surface.py` snapshots the core parser only (section 5, decision 2).
- Every recorded Fabric run predates the Lakehouse-kind lookup and the abfss fix. The three open owner notes (`docs/SINKS.md:66`, `docs/plugins/fabric-commands.md:59` and `:95`) all failed in run 38337418136.
- Recommendation: promote 9 items in 0.9.3, keep 45 for later, keep 14 experimental, and mark 2 commands as candidates to remove. The 9 are: REPRODUCIBILITY, DETERMINISM, QUICKSTART, `canary`, `gameday`, SIGNING, VERIFY, `mask`, and the Synapse write through `generate --to synapse://`. Build the P-BAR check first (work package W0, size M); without it "available" stays a hand-written word.
- Promote nothing that needs a new live run on a branch other than the release head. Re-record Fabric runs on the release head.

Size scale: **S** up to 1 day, **M** 2 to 4 days, **L** more than 1 week. Sizes are for one person who knows the code.

## 1. P-BAR

### 1.1 The bar, as the Owner approved it

A feature is "available" only when:

1. its command (if any) is in STABLE;
2. every key claim on its page maps to a named test or CI job;
3. anything touching an outside service (Fabric, SQL Server, Synapse, Kafka, Event Hubs, cloud storage) has a real recorded run (a transcript or receipt under docs/plans/evidence or docs/evidence), not a mock;
4. its page has no open owner notes (scripts/check_docs_owners.py);
5. it has a release note in CHANGELOG.

### 1.2 Terms the check needs (proposed; the Owner confirms in decision 1 and 3)

- **Key claim.** A sentence on the page that a reader could act on: a behaviour, an exit code, a flag, a number, a guarantee. Examples and tables of options are claims. Marked in the page by an anchor `<!-- claim: AV-<SLUG>-NN -->`. A human reviewer decides whether the marked set is complete; the check cannot.
- **Named test.** A pytest node id that `pytest --collect-only` finds (a parametrized test is named without its parameters, as in `scripts/check_v1_done.py`). A docs example counts through `tests/docs/test_reference_examples.py::test_reference_page[<PAGE>]`, which runs the page's `local` examples.
- **CI job.** `ci:<workflow>/<job>`, as in V1_DONE.md.
- **Outside service.** A claim that needs a service Shape does not ship. A service started from a container by the test itself (PostgreSQL, MySQL, SQL Server, Kafka) is real. An emulator of a cloud service (Azurite, the Event Hubs emulator) is not.
- **Real recorded run.** A file under `docs/evidence/` or `docs/plans/evidence/` that records the command, the exit code, the Shape version and the date. For a CI transcript: the JSON fields `run_id`, `commit`, `exit_code`, `status` (`passed`). For an Owner receipt: a machine line `<!-- receipt: shape=0.9.2 service=synapse exit=0 date=2026-10-20 -->`.
- **Fresh.** The run names a Shape version of the same minor line as the release, or the source files of that path are unchanged since the run (`git diff --quiet <commit> HEAD -- <paths>`). Proposed in decision 3.
- **Release note.** A line under `### Promoted` in the CHANGELOG section of the release that names the page file and the command.

### 1.3 Proposed check (design only)

Name: `scripts/check_available.py`. Same style as `scripts/check_v1_done.py`: stdlib only, exit 0 clean, 1 a rule fails, 2 usage or unreadable input.

**What it parses**

| Input | Used for |
|---|---|
| `docs/AVAILABLE.md` | The claim-to-proof tables (below). |
| Every `docs/**/*.md` outside `plans` and `talks` | The `Status:` line, `<!-- claim: ... -->` anchors, `<!-- owner: ... -->` notes, `Covers:` line. |
| `src/shape/cli/stability.py` | `STABLE` (imported). |
| `pytest --collect-only -q` over the files named | Node ids. |
| `.github/workflows/*.yml` | CI job keys. |
| `docs/evidence/**`, `docs/plans/evidence/**` | Evidence files. |
| `scripts/check_docs_owners.py` | `inventory()` (imported) for rule 4. |
| `CHANGELOG.md` | Section of the release in `pyproject.toml`, `### Promoted`. |
| `tests/docs/available_legacy.txt` | The ratchet (below). |

**Where the claim-to-proof tables live.** One new page, `docs/AVAILABLE.md`, modelled on `docs/V1_DONE.md`. It is a public page: readers see what is proven. One section per available page. Row format:

`| AV-CANARIES-01 | claim (short) | proof | evidence | status |`

- `proof`: node ids and `ci:` jobs in backticks, as in V1_DONE.
- `evidence`: paths in backticks, or `none` when no service is touched.
- `status`: `done`, or `open <work package>` as in V1_DONE.

A page declares what it covers with a line under `Status:`:
`Covers: shape canary, shape gameday`. A page that has a mixed audience marks the experimental parts with `<!-- experimental:start -->` and `<!-- experimental:end -->`. The docs hook renders them as a warning box. The check skips them.

**Rules and failure messages.** Each failure prints `AVAILABLE: <page>: P-BAR <n>: <message>` and the run exits 1.

1. P-BAR 1. For a page with `Status: available` and a row set in AVAILABLE.md: every `shape <cmd>` in the page text outside experimental blocks, and every name in `Covers:`, must be in `STABLE`. Undeclared mentions fail ("page mentions `shape mask`; add it to Covers or fence it").
2. P-BAR 2. Each anchor has a row; each row has an anchor; each row is `done` with at least one existing node id or CI job. An `open` row fails for an available page.
3. P-BAR 3. A row that touches a service (a `service:` tag in the claim cell) needs an evidence path that exists, whose record shows exit 0 and a fresh version. A proof test marked `emulator` alone does not satisfy it.
4. P-BAR 4. `inventory()` returns no owner note for the page.
5. P-BAR 5. The release section of `CHANGELOG.md` has a `### Promoted` line naming the page, for each page whose row set carries `since: <this release>`.

**Ratchet for the 71 existing available pages.** `tests/docs/available_legacy.txt` lists the pages that say `available` today. They are exempt. The list may only shrink: a page not on it that says `available` must pass rules 1 to 5. A page that leaves the list needs the full rules. A page in AVAILABLE.md that later says `experimental` fails ("a page once available is not demoted"), the page-level twin of the CLI rule.

**How it joins `make check`.** Add one line after `scripts/check_v1_done.py`:
`$(PYTHON) scripts/check_available.py`
and the same step to the `test` job in `.github/workflows/ci.yml`. Today `check_docs_owners.py --release` runs only in `docs.yml`, so rule 4 would call its `inventory()` directly instead of waiting for that job. A test file `tests/release/test_check_available.py` covers each rule with a small fixture tree (clean, missing proof, unknown job, experimental command in an available page, open owner note, no release note, stale evidence).

Two limits to state on the page. The check proves that proof exists, not that it passes (CI proves that), as V1_DONE already says. It cannot judge whether the marked claims are all the key claims.

Generated pages need a change too: `scripts/gen_failure_modes.py` and `scripts/gen_plugin_api_docs.py` write `Status: experimental` into their output, so a promoted generated page must change the generator.

## 2. Inventory

### 2.1 How it was generated

1. Commands: `shape --help` lists 85 commands. `tests/cli/cli_surface_v1.json` holds 84 core commands; the 22 commands absent from it are plugin-added (`(plugin <name>)` in `--help`). `STABLE` has 24. So 60 core and 22 plugin commands are experimental (60 + 24 = 84). `compare` is an alias of `fidelity`.
2. Pages: `grep -rlE '^Status: experimental' docs --include=*.md` outside `docs/plans` and `docs/talks`: 93 pages. `QUICKSTART.md` is counted because line 48 says `Status: experimental` for one section; its page line says available.
3. Tests: `pytest --collect-only -q -p no:cacheprovider --continue-on-collection-errors tests plugins` after `pip install -e ".[dev]"`, the plugins, and `confluent-kafka azure-eventhub azure-identity fhir.resources pyx12 ms-fabric-cli nbformat`: 21,995 node ids. Six modules did not collect here: `tests/demo/fabric/test_udf.py` and five in `plugins/shape-healthcare-codes/tests/`. No id is cited from them. Every id in this plan was matched exactly against that list. A node id shown without `[...]` names a parametrized family.
4. Docs examples: `tests/docs/reference_examples.json` gives the example kinds per page; `tests/docs/test_reference_examples.py::test_reference_page[<PAGE>]` runs the `local` and `bootstrap` ones. It is listed as proof for the page, not for a claim.
5. Owner notes: `scripts/check_docs_owners.py` (3 open, all Fabric).
6. Evidence: `docs/evidence/`, `docs/evidence/transcripts/` and `docs/plans/evidence/owner-notes/`. Transcript status and exit code were read from each JSON.
7. Rows group commands and pages that share a feature. A check script confirmed that every one of the 82 commands and 93 pages is in exactly one row (70 rows).

Not exhaustive by design: "existing proof" lists up to three representative ids plus the test count in the named paths. The full claim-to-proof table is work package W-claims for each promoted item, not this table.

### 2.2 Legend

- Evidence paths: `E/` = `docs/evidence/`; `O/` = `docs/plans/evidence/owner-notes/`; `T/<example>` = `docs/evidence/transcripts/docs-transcripts-<run>-<sha>-ubuntu-latest-<example>.json`, with the run id and status in brackets. `O/…<run>…-<example>.json` is a failed attempt kept in `owner-notes`.
- Most Owner receipts ran Shape 0.9.1 from PyPI on 2026-10-11 or 12.
- "Missing" always includes: add the command to STABLE (if it has one and it is core), write the claim-to-proof table, add the CHANGELOG line. The cell lists what is more than that.
- Recommendation: *promote in 0.9.3*, *later*, *keep experimental*, *candidate to remove*.

### 2.3 Table

| Feature | Command(s) | Docs page(s) | Existing proof (verified node ids) | Live evidence | Open owner notes | Missing against P-BAR | Size | Recommendation |
|---|---|---|---|---|---|---|---|---|
| Canary batches | `canary` | `CANARIES.md` | `tests/scenario/test_canary.py::test_make_writes_the_marked_batch_and_the_canary_document`<br>`tests/scenario/test_canary.py::test_removing_one_result_exits_one_and_names_the_blind_spot`<br>`tests/scenario/test_canary.py::test_make_json_is_a_shape_result_with_the_plan`<br>`tests/docs/test_reference_examples.py::test_reference_page[CANARIES]`<br>(54 tests in the named paths) | none | none | STABLE; pin the `--json` key sets in a test and add a JSON schema; CHANGELOG line for the promoting release. Every claim already has a test. | S | promote in 0.9.3 |
| Game days | `gameday` | `GAMEDAY.md` | `tests/scenario/test_gameday.py::test_the_source_data_is_byte_identical_after_a_run`<br>`tests/scenario/test_gameday.py::test_a_round_whose_checks_cannot_detect_its_injection_exits_one_and_is_marked_missed`<br>`tests/scenario/test_gameday.py::test_the_report_declares_its_format_and_keeps_its_keys`<br>`tests/docs/test_reference_examples.py::test_reference_page[GAMEDAY]`<br>(72 tests in the named paths) | none | none | STABLE; decide the `--dry-run --json` document; tests for the `.md` report and the `--json` auto-add; CHANGELOG line. | S | promote in 0.9.3 |
| Rules testing (mutation, backtest) | `rules` | `RULES_TESTING.md` | `tests/rules/test_mutation.py::test_the_same_seed_gives_the_same_report`<br>`tests/rules/test_rules_cli.py::test_min_score_decides_between_exit_0_and_1`<br>`tests/rules/test_backtest.py::test_the_report_lists_every_version_oldest_first_with_the_failed_rules`<br>`tests/docs/test_reference_examples.py::test_reference_page[RULES_TESTING]`<br>(81 tests in the named paths) | none | none | STABLE; worked example contradicts its fixtures (prose says 13 faults missed, fixture already has `unique`); no backtest transcript; mutant kinds come from experimental `chaos`; `--json` help text wrong; CHANGELOG. | M | later |
| Share bundle | `share-bundle` | `SHARE_BUNDLE.md` | `tests/w5_10/test_share_bundle.py::test_create_writes_a_valid_bundle_for_independent_data`<br>`tests/w5_10/test_share_bundle.py::test_an_edited_data_file_fails_verify`<br>`tests/w5_10/test_share_bundle_compat.py::test_golden_bundle_verifies_with_this_release`<br>(60 tests in the named paths) | none | none | STABLE; `exitcodes.py` omits create exit 1; `--json` has only a text `output` key; `verify --public-key` vs stable `--key`; no runnable page example (no `test_reference_page[SHARE_BUNDLE]`); CHANGELOG. | M | later |
| Masking | `mask` | `MASK.md` | `tests/builtins/test_mask.py::test_no_original_value_remains`<br>`tests/masking/test_mask_keyed_cli.py::test_keyed_runs_are_identical_and_other_key_differs`<br>`tests/masking/test_masking_compat.py::test_public_surface_is_exactly_the_documented_one`<br>`tests/docs/test_reference_examples.py::test_reference_page[MASK]`<br>(58 tests in the named paths) | none | none | STABLE; fix the wrong `--string-columns` hint; decide `--seed` with a key and non-empty `-o`; CLI golden test for unkeyed output; page fixes (`--dry-run`); CHANGELOG. | M | promote in 0.9.3 |
| Value vault | `vault` | `VAULT.md` | `tests/vault/test_build.py::test_save_writes_the_safe_capture_and_the_vault`<br>(161 tests in the named paths) | none | none | STABLE; subcommands keygen/inspect/verify/rekey not reviewed; claim table; CHANGELOG. | M | later |
| Chaos (planted faults) | `chaos` | `CHAOS.md`, `EXCEL.md` | `tests/chaos/test_audit_anomalies.py::test_chaos_config_validate_lists_bad_settings`<br>`tests/excel/test_workbook_sink.py::test_readme_lists_the_planted_chaos`<br>`tests/docs/test_reference_examples.py::test_reference_page[CHAOS]`<br>+1 more page runs<br>(233 tests in the named paths) | none | none | STABLE; flag surface not reviewed; `EXCEL.md` mixes stable `profile` with `chaos`; claim table; CHANGELOG. | L | later |
| Incremental batches and time travel | `continue`, `time-travel` | `INCREMENTAL.md`, `LANDING.md` | `tests/generation/test_incremental.py::test_new_keys_continue_above_existing_and_are_not_reissued`<br>`tests/regressions/test_hunt2_cli.py::test_continue_refuses_to_write_over_its_input`<br>`tests/docs/test_reference_examples.py::test_reference_page[INCREMENTAL]`<br>+1 more page runs<br>(97 tests in the named paths) | none | none | STABLE x2; claim table; landing-layout claims not yet mapped to tests; CHANGELOG. | L | later |
| Fabric open-mirroring landing zone | none | `FABRIC_MIRROR.md` | `tests/io/test_fabric_mirror.py::test_registered_as_a_sink_plugin`<br>(44 tests in the named paths) | none | none | Contract tests only; the live test needs `SHAPE_FABRIC_MIRROR_LANDING_ZONE` and has no receipt. Do not promise. | L | keep experimental |
| History tools | `bisect`, `timelapse` | `HISTORY.md`, `REGISTRY.md` | `tests/versions/test_cli.py::test_bisect_prints_json_equal_to_the_api`<br>`tests/docs/test_reference_examples.py::test_reference_page[HISTORY]`<br>+1 more page runs<br>(15 tests in the named paths) | none | none | STABLE; REGISTRY.md also documents stable `registry` (split page). | M | later |
| Bridge protocol | `bridge` | `BRIDGE.md` | `tests/bridge/test_cli.py::test_shape_bridge_once_through_the_cli`<br>`tests/bridge/test_protocol.py::test_importing_the_bridge_loads_no_heavy_module`<br>`tests/bridge/test_schemas.py::test_the_envelope_schemas_accept_what_the_bridge_sends`<br>`tests/docs/test_reference_examples.py::test_reference_page[BRIDGE]`<br>(191 tests in the named paths) | none | none | Own versioned protocol (api_version 1.0 to 1.2); needs its own compatibility promise, not the CLI one; 1,464-line page. | L | keep experimental |
| Project file and planned changes | `project`, `changes` | `PLANNED_CHANGES.md` | `tests/project/test_changes.py::test_cli_changes_key_in_shape_yml`<br>`tests/project/test_cli.py::test_without_a_project_the_default_threshold_applies`<br>`tests/docs/test_reference_examples.py::test_reference_page[PLANNED_CHANGES]`<br>(139 tests in the named paths) | none | none | STABLE; shape.yml schema freeze; docs/PROJECT.md already says available while `project` is experimental. | M | later |
| Project scaffold and CI helpers | `init`, `ci`, `badge` | `PR_BOT.md`, `CI.md` | `tests/project/test_init.py::test_init_scaffolds_a_valid_project`<br>`tests/prbot/test_action.py::test_the_scaffold_workflow_of_shape_init_uses_pull_request`<br>`tests/docs/test_reference_examples.py::test_reference_page[CI]`<br>(169 tests in the named paths) | `E/github-action-pass.md`<br>`E/github-action-drift.md`<br>`E/github-action-ruleset.md` | none | STABLE x3; hosted GitHub Action runs are recorded (see evidence) but `init` generates workflow text only; claim table. | M | later |
| Webhook notifications | `notify` | `NOTIFICATIONS.md` | `tests/notify/test_notify.py::test_the_commands_list_limits_which_commands_notify`<br>`tests/docs/test_reference_examples.py::test_reference_page[NOTIFICATIONS]`<br>(76 tests in the named paths) | none | none | STABLE; claim table; docs examples use a loopback receiver; CHANGELOG. | M | later |
| Proposals and decision files | `proposals`, `types`, `key` | `PROPOSALS.md` | `tests/proposals/test_apply.py::test_pending_and_deferred_proposals_change_nothing`<br>`tests/cli/test_types_command.py::test_declared_types_that_differ_from_the_values_are_reported`<br>`tests/docs/test_reference_examples.py::test_reference_page[PROPOSALS]`<br>(267 tests in the named paths) | none | none | Three commands on one page; decision-file format has its own version; STABLE; claim table. | L | later |
| Report card | `report-card` | `REPORT_CARD.md` | `tests/bridge/test_report_card_1_2.py::test_the_card_is_the_one_the_cli_prints`<br>`tests/docs/test_reference_examples.py::test_reference_page[REPORT_CARD]`<br>(19 tests in the named paths) | none | none | STABLE; claim table; CHANGELOG. | M | later |
| Fidelity report and tiers | `fidelity`, `compare`, `certify-shapes` | `FIDELITY.md`, `FIDELITY_TIERS.md` | `tests/cli/test_fidelity_cli.py::test_compare_is_an_alias`<br>`tests/fidelity/test_cli_tiers.py::test_tier_3_compares_the_trees_and_can_gate`<br>`tests/docs/test_reference_examples.py::test_reference_page[FIDELITY]`<br>+1 more page runs<br>(35 tests in the named paths) | none | none | `compare` is an alias of `fidelity`; 4,301-line tiers page; STABLE; claim table. `certify-shapes` has no page. | L | later |
| Scorecard, skew and skew rehearsal | `scorecard`, `skew`, `skew-rehearsal` | `SCORECARD.md`, `FAIRNESS_AND_SKEW.md` | `tests/quality/test_scorecard_cli.py::test_json_output`<br>`tests/quality/test_skew_cli.py::test_exit_0_when_nothing_is_flagged`<br>`tests/docs/test_reference_examples.py::test_reference_page[SCORECARD]`<br>+1 more page runs<br>(39 tests in the named paths) | none | none | Three commands; fairness claims need careful wording; STABLE; claim table. | L | later |
| Contract emit (DDL, JSON Schema, pandera, GX) | `contract` | `CONTRACT_EMIT.md` | `tests/contracts/emit/test_emit_api.py::test_an_empty_contract_emits`<br>`tests/docs/test_reference_examples.py::test_reference_page[CONTRACT_EMIT]`<br>(242 tests in the named paths) | none | none | STABLE; claim table; CHANGELOG. | M | later |
| Consumer contracts | `contracts` | `CONSUMER_CONTRACTS.md` | `tests/consumers/test_check.py::test_contracts_of_other_sources_do_not_run`<br>`tests/docs/test_reference_examples.py::test_reference_page[CONSUMER_CONTRACTS]`<br>(67 tests in the named paths) | none | none | STABLE; claim table; producer-CI claims need a hosted-Action run (receipts exist only for `ci`/drift pages). | M | later |
| Detective, failure modes and suites | `detective`, `failure-modes`, `suite` | `DETECTIVE.md`, `FAILURE_MODES.md`, `SCENARIO_LIBRARY.md`, `SCENARIO_PACKS.md` | `tests/scenario/test_detective.py::test_a_malformed_pack_is_refused_with_a_message`<br>`tests/scenario/test_library.py::test_every_scenario_meets_its_own_answer_key`<br>`tests/cli/test_suite_cli.py::test_the_smoke_suite_exits_zero_and_says_so`<br>`tests/docs/test_reference_examples.py::test_reference_page[DETECTIVE]`<br>+2 more page runs<br>(232 tests in the named paths) | none | none | STABLE x3; FAILURE_MODES.md is generated (scripts/gen_failure_modes.py hard-codes 'experimental'); `pack` is already stable. | L | later |
| Public dataset library | `library` | `DATASET_LIBRARY.md` | `tests/library/test_dataset_library.py::test_the_library_adds_under_two_megabytes_to_the_wheel`<br>`tests/docs/test_reference_examples.py::test_reference_page[DATASET_LIBRARY]`<br>(70 tests in the named paths) | none | none | STABLE; claim table; licence of each shipped dataset to confirm. | M | keep experimental |
| Schema design and dependencies | `design`, `fd` | `DESIGN.md`, `JOINT.md` | `tests/design/test_design_cli.py::test_design_writes_ddl_to_a_file`<br>`tests/joint/test_diff_contract_joint.py::test_the_example_is_drift_that_names_the_dependency_and_the_value`<br>`tests/docs/test_reference_examples.py::test_reference_page[DESIGN]`<br>(35 tests in the named paths) | none | none | STABLE; JOINT.md is mostly Python API and profile content, not the command. | L | later |
| Schema importers | `import-schema`, `from-ddl` | `IMPORTERS.md` | `tests/importers/test_w5_06_cli.py::test_every_format_is_inferred_and_gives_a_valid_spec`<br>`tests/generation/test_ddl.py::TestScales::test_the_engine_plans_the_import`<br>`tests/docs/test_reference_examples.py::test_reference_page[IMPORTERS]`<br>(77 tests in the named paths) | none | none | STABLE x2; claim table (tests exist per format in `tests/importers`). | L | later |
| Data dictionary | `dictionary` | none (page is available) | `tests/dictionary/test_dictionary.py::test_json_document_declares_format_and_version`<br>(27 tests in the named paths) | none | none | STABLE; docs/DICTIONARY.md already says available while the command is experimental. | S | later |
| Reference packs | `reference` | `REFERENCE_PACKS.md` | `tests/reference/test_packs.py::test_pack_datasets_work_unchanged_with_reference_pairs`<br>`tests/docs/test_reference_examples.py::test_reference_page[REFERENCE_PACKS]`<br>(39 tests in the named paths) | none | none | STABLE; claim table; licence of shipped packs to confirm. | M | later |
| Transforms (star schema, CDM) | `transform` | `TRANSFORMS.md` | `tests/cli/test_transform_cli.py::test_builtin_transforms_through_the_plugin_host`<br>`tests/docs/test_reference_examples.py::test_reference_page[TRANSFORMS]`<br>(9 tests in the named paths) | none | none | STABLE; claim table. | M | later |
| Learn and composite generation | `learn`, `composite` | `GENERATION_ENGINE.md` | `tests/generation/test_learn.py::test_learned_schema_validates_and_generates`<br>`tests/generation/test_composite_p601e.py::test_the_fixtures_cover_the_six_presets_and_three_ad_hoc_composites`<br>`tests/docs/test_reference_examples.py::test_reference_page[GENERATION_ENGINE]`<br>(86 tests in the named paths) | none | none | 1,031-line page mixes stable `generate` with experimental commands; STABLE x2; claim table. | L | later |
| Generator pinning and file migration | `pin`, `migrate` | `GENERATION_SPEC.md`, `specs/STATE_AND_COMPATIBILITY.md` | `tests/generation/test_w1_15_pin_cli.py::test_pin_writes_the_current_version_of_everything_the_spec_uses`<br>`tests/state/test_migrate_files.py::test_the_migrated_file_reads_like_the_source_read_through_the_registry`<br>(53 tests in the named paths) | none | none | STABLE x2; claim table; macOS byte-identity (W8-04) was held in COMPLETION_PLAN: confirm its state before freezing `pin`. | L | later |
| Generation reference pages (strategies, kernel, calendars, locales) | none | `GENERATION_STRATEGIES.md`, `GENERATION_KERNEL.md`, `GENERATION_CALENDARS.md`, `LOCALES.md` | `tests/generation/test_strategies.py::test_plan_deterministic_and_relational`<br>`tests/generation/test_calendars.py::test_rules_give_the_known_dates`<br>`tests/kernel/test_dense_kernels.py::test_dense_rows_known_answer`<br>(470 tests in the named paths) | none | none | No command; claim tables per strategy; page content is Python/spec API (see API_STABILITY.md). | L | later |
| Event emission and streaming | `emit`, `stream` | `EMIT.md`, `SIMULATION_FILES_EVENTS.md` | `tests/streaming/emit/test_stream.py::test_stream_file_is_the_emit_table_in_time_order`<br>`tests/streaming/test_cli.py::test_a_checkpoint_of_another_stream_or_profiler_is_refused`<br>`tests/docs/test_reference_examples.py::test_reference_page[EMIT]`<br>+1 more page runs<br>(79 tests in the named paths) | `O/kafka-live.md`<br>`O/eventhubs-live.md`<br>`O/fabric-eventhouse-live.md`<br>`O/fabric-eventstream-live.md`<br>`T/kafka-avro` (38266883768 passed; 38269228192 output-mismatch; 38273696299 passed)<br>`T/kafka-drift` (38266883768 passed; 38269228192 passed; 38273696299 passed)<br>`T/emit-overhead` (38266883768 passed; 38269228192 passed; 38273696299 passed) | none | 1,792-line EMIT page; Kafka/Event Hubs claims have live receipts (kafka-live.md, eventhubs-live.md) but cover few of the sinks; STABLE x2. | L | later |
| Stream profiling (Kafka, Event Hubs) | `stream-profile` | `plugins/streaming.md` | `tests/streaming/test_cli.py::test_the_global_profile_equals_batch_bounded_profiling`<br>`tests/streaming/test_hunt2_regressions.py::test_697_one_decodable_event_among_bad_lines_is_profiled`<br>`tests/docs/test_reference_examples.py::test_reference_page[streaming]`<br>(83 tests in the named paths) | `O/kafka-live.md`<br>`O/eventhubs-live.md`<br>`T/kafka-profile` (38266883768 passed; 38269228192 passed; 38273696299 passed)<br>`T/kafka-doctor` (38266883768 passed; 38269228192 passed; 38273696299 passed) | none | STABLE; Kafka transcripts come from a Kafka container in CI; Event Hubs only an Owner receipt on 0.9.1 (no windows, no `--follow`). | M | later |
| Test-database seeding | `seed` | `TESTING_WITH_SHAPE.md` | `tests/profile/test_sampling.py::test_make_spec_rejects_method_and_seed_without_sample`<br>`tests/scenario/test_reproducibility.py::test_the_dataset_id_follows_the_seed_not_the_clock`<br>`tests/docs/test_reference_examples.py::test_reference_page[TESTING_WITH_SHAPE]`<br>(170 tests in the named paths) | `E/postgresql-live.md`<br>`E/mysql-live.md`<br>`T/postgres-seed` (38266883768 passed; 38269228192 passed; 38273696299 passed) | none | STABLE; Postgres/MySQL live receipts exist; SQL Server/Snowflake/Databricks receipts cover write only; page also covers the pytest plugin. | L | later |
| Scale runs and job control | `jobs` | `SCALE.md` | `tests/bridge/test_jobs.py::test_a_job_is_a_versioned_file_under_the_jobs_directory`<br>`tests/scale/test_jobs.py::test_default_jobs_dir_follows_the_environment`<br>`tests/docs/test_reference_examples.py::test_reference_page[SCALE]`<br>(319 tests in the named paths) | `T/scale-fabric-spark` (38332340250 passed)<br>`T/scale-jobs` (38332340250 passed) | none | STABLE; `jobs status/cancel/resume` transcript passed (run 38332340250); `fabric_spark` shows submission only; the live completion test has no receipt. | L | later |
| Demo commands | `demo` | `DEMO.md` | `tests/demo_cmd/test_run_local.py::test_seeding_writes_every_table_with_its_own_row_count`<br>`tests/demo_cmd/test_connections.py::test_init_saves_a_profile_that_only_its_owner_can_read`<br>`tests/docs/test_reference_examples.py::test_reference_page[DEMO]`<br>(36 tests in the named paths) | none | none | 23 uses on one page; targets Fabric/Spark/SQL; for talks, not for pipelines; STABLE would freeze talk flags. | L | keep experimental |
| Environment parity | `parity` | `PARITY.md` | `tests/parity/test_parity.py::test_identical_environments_have_parity`<br>`tests/parity/test_compat.py::test_the_frozen_parity_report_still_validates_and_renders`<br>`tests/docs/test_reference_examples.py::test_reference_page[PARITY]`<br>(44 tests in the named paths) | none | none | STABLE; claim table; no two-environment run recorded. | M | later |
| Entity resolution | `resolve` | `RESOLVE.md` | `tests/resolve/test_cli.py::test_synth_then_run_scores_against_the_written_truth`<br>`tests/docs/test_reference_examples.py::test_reference_page[RESOLVE]`<br>(4 tests in the named paths) | none | none | STABLE; claim table; only 4 tests found. | M | later |
| Parquet and Delta fingerprint | `fingerprint` | `FINGERPRINT.md` | `tests/w5_10/test_fingerprint_preservation.py::test_a_fingerprint_written_by_shape_is_what_every_scenario_starts_from`<br>(4 tests in the named paths) | none | none | STABLE; Delta read path relies on the delta extension (see delta-fallback receipt); claim table. | M | later |
| Explain a report | `explain` | `EXPLAIN.md` | `tests/report/test_explain_display.py::test_explain_is_deterministic_and_structured`<br>(18 tests in the named paths) | none | none | Wording is generated prose; freezing it is not useful; keep experimental. | S | keep experimental |
| Query a capture | `query` | none (page is available) | `tests/cli/test_five_cli.py::test_query_check_compatibility_plan_registry`<br>(1 tests in the named paths) | none | none | STABLE; expression language has no spec page; only 1 test found; used by QUICKSTART example 2. | M | later |
| Equivalence-group size | `privacy-k` | none (page is available) | `tests/regressions/test_hunt2_cli.py::test_init_writes_a_valid_file_for_a_number_like_name`<br>(53 tests in the named paths) | none | none | No page; overlaps `profile` k-anonymity; no dedicated tests found. | S | candidate to remove |
| Rule check from a capture | `quality` | none (page is available) | `tests/quality/test_bugs_cli_type_mismatch.py::test_wrong_type_is_a_violation_not_a_crash`<br>(17 tests in the named paths) | none | none | No page; overlaps `check` and `verify`. | S | candidate to remove |
| Built-in conformance suite | `conformance` | none (page is available) | `tests/conformance/test_repository_constitution.py::test_requirements_registry`<br>`tests/cli/test_cli_e2e.py::test_conformance_cli`<br>(4 tests in the named paths) | none | none | STABLE; conformance catalog is its own versioned list; tied to the SHAPE_2 draft. | M | later |
| Generate drift | `generate-drift` | none (page is available) | `tests/generation/test_drift_plan.py::test_a_boolean_rate_can_drift_too`<br>(29 tests in the named paths) | none | none | No experimental page (DRIFT.md is available); STABLE; claim table. | M | later |
| Sinks: where generated data goes | none | `SINKS.md` | `tests/generation/test_sink_formats_and_schemes.py::test_every_registered_sink_is_an_accepted_format`<br>`plugins/shape-fabric/tests/test_live_onelake_sink.py::test_the_abfss_sink_publishes_files_into_a_lakehouse_files_folder`<br>`tests/docs/test_reference_examples.py::test_reference_page[SINKS]`<br>(27 tests in the named paths) | `E/postgresql-export-profile.md`<br>`E/mysql-export-profile.md`<br>`E/snowflake-write.md`<br>`E/databricks-write.md`<br>`O/adls-gen2-write.md`<br>`T/postgres-sink` (38266883768 passed; 38269228192 passed; 38273696299 passed)<br>`O/…38337418136…-sinks-onelake.json` (failed, exit 2) | `SINKS.md`: Fabric maintainer — supply the transcript for docs/SINKS.md example 0. | Owner note open (example 0, abfss): last run failed (run 38337418136, exit 2) on a probe removed by a later fix; never re-run. Example 0's third command fails at HEAD (name form needs `schema_enabled`). Postgres, MySQL, Snowflake, Databricks, ADLS receipts are 0.9.1 and predate `--table-prefix`. | M | later |
| Container image | none | `CONTAINER.md` | `tests/release/test_baseline_diff_report.py::test_reports_changed_and_unchanged_domains`<br>`tests/docs/test_reference_examples.py::test_reference_page[CONTAINER]`<br>(389 tests in the named paths) | none | none | Image build is a release job; docs example runs in a container. | M | later |
| dbt integration | `dbt-seeds`, `dbt-report`, `from-dbt`, `to-dbt-tests` | `DBT.md` | `plugins/shape-dbt/tests/test_dbt_build.py::test_the_manifest_dbt_wrote_gives_the_same_schema_as_the_yaml`<br>`tests/docs/test_reference_examples.py::test_reference_page[DBT]`<br>(115 tests in the named paths) | none | none | Plugin commands; need the plugin-command stability mechanism; DuckDB adapter only. | L | later |
| Drift report publishing | `publish-report` | `DRIFT_REPORT.md` | `tests/docs/test_reference_examples.py::test_reference_page[DRIFT_REPORT]` | none | none | Plugin command in the Fabric plugin; no live run recorded. | M | later |
| Release checklist | none | `RELEASE_CHECKLIST.md` | `tests/release/test_baseline_diff_report.py::test_reports_changed_and_unchanged_domains`<br>`tests/docs/test_reference_examples.py::test_reference_page[RELEASE_CHECKLIST]`<br>(389 tests in the named paths) | none | none | Maintainer process page; no promise to users. | S | keep experimental |
| Quickstart page | none | `QUICKSTART.md` | `tests/docs/test_reference_examples.py::test_reference_page`<br>`tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]`<br>(68 tests in the named paths) | none | none | Page is already labelled available; one experimental line (`shape query`, example 2) and a section marked experimental. Drop the line, claim table, CHANGELOG. | S | promote in 0.9.3 |
| CLI reference page | none | `CLI.md` | `tests/docs_site/test_docs_site.py::test_config_is_strict_and_excludes_internal_material`<br>`tests/docs/test_reference_examples.py::test_reference_page[CLI]`<br>(20 tests in the named paths) | none | none | Documents 44 experimental commands; split into a stable page and `CLI_EXPERIMENTAL.md` in 0.9.3 (M); promote the stable half later: it says `--json`/`--dry-run` are on every command while V1-17 is open. | M | later |
| Verify page | none | `VERIFY.md` | `tests/quality/test_verify.py::test_load_tables_formats_and_errors`<br>`tests/docs/test_reference_examples.py::test_reference_page[VERIFY]`<br>(19 tests in the named paths) | none | none | Move the scorecard/skew/report-card section and the `privacy` row out; claim table; mark Python API sections; CHANGELOG. | M | promote in 0.9.3 |
| Determinism contract | none | `DETERMINISM.md` | `tests/scenario/test_reproducibility.py::test_the_id_is_a_prefixed_sha256_and_is_stable`<br>(33 tests in the named paths) | none | none | 12-line page: claim table; cut or test the scan-cost sentence and the idempotent-sink advice; move the streaming paragraph; CHANGELOG. | S | promote in 0.9.3 |
| Reproducibility | none | `REPRODUCIBILITY.md` | `tests/scenario/test_reproducibility.py::test_the_id_is_a_prefixed_sha256_and_is_stable`<br>`tests/docs/test_reference_examples.py::test_reference_page[REPRODUCIBILITY]`<br>(33 tests in the named paths) | none | none | All claims map to tests; claim table, Python API marker (`shape.repro`), CHANGELOG. | S | promote in 0.9.3 |
| Signing | none | `SIGNING.md` | `tests/artifact/test_artifact.py::test_roundtrip`<br>`tests/docs/test_reference_examples.py::test_reference_page[SIGNING]`<br>(94 tests in the named paths) | none | none | Move the vault and `migrate` text and example 4; drop `query` and the Azure Key Vault sentence (fake resolver only); claim table; CHANGELOG. | M | promote in 0.9.3 |
| Specs (drafts) | none | `specs/SHAPE_1_0.md`, `specs/ONE_ZERO_CONTRACT.md`, `specs/SHAPE_2.md`, `specs/SHAPE_MODEL_V2.md`, `specs/SHAPE_ARTIFACT_SPEC.md`, `specs/PACK_SPEC.md`, `specs/LOCATION_SPEC.md`, `specs/STREAMING_SEMANTICS.md` | `tests/spec/test_capabilities.py::test_caps`<br>(43 tests in the named paths) | none | none | Drafts by design; a spec is 'available' only when frozen. | S | keep experimental |
| Architecture and policy pages | none | `PRODUCT_ARCHITECTURE.md`, `SHAPE_MANIFESTO.md`, `EXTERNAL_REFERENCE_ASSETS.md`, `LOCATION_AS_CODE.md`, `THREAT_MODEL.md` | none found | none | none | Not features. A label means nothing here; consider a different label word. | S | keep experimental |
| Python API stability and plugin framework | none | `plugins/api-v1.md`, `plugins/authoring.md`, `plugins/host.md`, `plugins/trust-model.md`, `plugins/builtins.md` | `tests/plugins/test_api_v1.py::test_api_version`<br>`tests/docs/test_reference_examples.py::test_reference_page[authoring]`<br>(488 tests in the named paths) | none | none | `plugins/api-v1.md` is generated (`scripts/gen_plugin_api_docs.py` writes 'experimental'); plugin promise is in `plugins/stability.md`. | M | later |
| SQL Server, Azure SQL, Fabric SQL | `profile-db` | `plugins/sqlserver.md` | `plugins/shape-sqlserver/tests/test_auth.py::test_missing_pyodbc_is_a_clear_error`<br>`tests/docs/test_reference_examples.py::test_reference_page[sqlserver]`<br>(185 tests in the named paths) | `O/sqlserver-live.md`<br>`E/kerberos-live.md`<br>`T/profile-db-warehouse` (38337418136 passed)<br>`T/sql-sampling-measurement` (38266883768 passed; 38269228192 passed; 38273696299 passed) | none | Plugin command `profile-db` cannot be STABLE today; printed `shape_content_id` is stale; no Azure SQL run; SQL Server and Fabric Warehouse runs exist. | M | later |
| Cloud sources (OneLake, ADLS Gen2, Delta) | none | `plugins/cloud-sources.md` | `tests/connectors/test_adapters.py::test_adapter_contracts`<br>(13 tests in the named paths) | `E/semantic-model-notebook.md`<br>`O/adls-gen2-write.md` | none | `semantic-model://` has a notebook receipt (0.9.1); OneLake/ADLS reads use contract tests and Azurite only; ADLS read failed on 0.9.1, fixed, not re-run. | M | later |
| Fabric sign-in (--auth) | none | `plugins/fabric-auth.md` | `plugins/shape-fabric/tests/security/test_exact_checks.py::test_a_copy_location_with_a_trailing_newline_is_refused`<br>(1303 tests in the named paths) | `E/kerberos-live.md` | none | `--auth` values on stable `generate`; kerberos has a receipt; spn, msi, device-code have no recorded run; page text 'owner action' is stale. | M | later |
| Fabric writers (Warehouse, Synapse, SQL database, Lakehouse) | none | `plugins/fabric-writers.md` | `plugins/shape-fabric/tests/security/test_exact_checks.py::test_a_copy_location_with_a_trailing_newline_is_refused`<br>(1303 tests in the named paths) | `T/synapse-write` (38338859503 passed) | none | Narrow to Synapse via `generate --to synapse://`: run 38338859503 passed, Synapse source unchanged since; re-record on release head. Warehouse, SQL database and Lakehouse sections need new runs: mark them experimental in-page. Cite run 38312820840 or drop the NaN claim. | M | promote in 0.9.3 |
| Fabric commands | `setup-fabric`, `publish`, `notebook`, `deploy-notebook`, `fabric`, `export-model`, `profile-model`, `known-answer`, `check-answers` | `plugins/fabric-commands.md` | `plugins/shape-fabric/tests/test_auth.py::test_unknown_mode_is_an_error`<br>`tests/docs/test_reference_examples.py::test_reference_page[fabric-commands]`<br>(1303 tests in the named paths) | `T/setup-fabric` (38337418136 passed)<br>`O/…38337418136…-publish-targets.json` (failed, exit 1)<br>`O/…38337418136…-notebook-deploy.json` (failed, exit 1)<br>`E/semantic-model-notebook.md` | `plugins/fabric-commands.md`: Fabric maintainer — supply the transcript for docs/plugins/fabric-commands.md example 0.<br>`plugins/fabric-commands.md`: Fabric maintainer — supply the transcript for docs/plugins/fabric-commands.md example 1. | Plugin commands cannot be STABLE today. Owner notes open at lines 59 and 95: `publish-targets` (stopped at sql-database: table exists) and `notebook-deploy` (409 name in use) both failed in run 38337418136. `setup-fabric` passed; page text is stale. | L | later |
| Fabric platform inventory | none | `FABRIC_PLATFORM.md` | `tests/docs/test_fabric_platform.py::test_the_inventory_parses_and_every_row_is_complete`<br>(42 tests in the named paths) | `T/git-sync-live` (38320894194 passed) | none | Git sync passed once (38320894194) and failed once (38327274076); both predate a git-sync fix; re-run. Page says 'not run yet'. | S | later |
| Behavior models | `behave` | `plugins/behavior.md` | `plugins/shape-behavior/tests/test_behaviors_group.py::test_builtin_is_registered_loads_and_conforms`<br>`tests/docs/test_reference_examples.py::test_reference_page[behavior]`<br>(187 tests in the named paths) | none | none | Plugin command; needs the plugin-command mechanism; claim table. | L | keep experimental |
| Healthcare code sets | `healthcare-codes` | `plugins/healthcare-codes.md` | `plugins/shape-healthcare-codes/tests/test_assets.py::test_codes_on_a_date_are_all_valid_and_billable_that_day`<br>`tests/docs/test_reference_examples.py::test_reference_page[healthcare-codes]`<br>(58 tests in the named paths) | none | none | Plugin command; licence of code sets to confirm; 5 test modules did not collect in this environment. | L | keep experimental |
| Healthcare standards (FHIR, X12) | none | `plugins/healthcare-standards.md` | `plugins/shape-healthcare-standards/tests/fhir/test_fhir_resources.py::test_every_sample_resource_validates_as_r4b`<br>(400 tests in the named paths) | none | none | No command; claim table; conformance claims. | L | keep experimental |
| Integrations (MLflow, lineage, evaluate) | `evaluate`, `lineage`, `mlflow` | `plugins/integrations.md` | `plugins/shape-integrations/tests/test_evaluate_unit.py::test_every_real_table_is_evaluated_by_default`<br>`tests/docs/test_reference_examples.py::test_reference_page[integrations]`<br>(168 tests in the named paths) | none | none | Plugin commands; no MLflow or OpenLineage run recorded. | L | keep experimental |
| Simulation patterns | `simulate` | `plugins/simulation.md` | `plugins/shape-simulation/tests/test_financial.py::test_a_far_future_time_or_huge_window_is_refused_not_simulated`<br>`tests/docs/test_reference_examples.py::test_reference_page[simulation]`<br>(218 tests in the named paths) | none | none | Plugin command; no outside service. | M | keep experimental |
| CTGAN model | `ctgan` | none (page is available) | `tests/fidelity/test_ctgan.py::test_fit_and_sample_through_sdv`<br>(7 tests in the named paths) | none | none | Plugin command from the core distribution; no page of its own. | M | keep experimental |

### 2.4 Counts

| Recommendation | Rows | Core commands | Plugin commands | Pages |
|---|---|---|---|---|
| promote in 0.9.3 | 9 | 3 (`canary`, `gameday`, `mask`) | 0 | 9 |
| later | 45 | see table | see table | 60 |
| keep experimental | 14 | see table | see table | 24 |
| candidate to remove | 2 | 2 (`privacy-k`, `quality`) | 0 | 0 |
| **Total** | **70** | **60** | **22** | **93** |

Commands per bucket, from the table: promote 3, later 66, keep experimental 11, remove 2 (sum 82). The `Signing`, `Quickstart`, `Verify`, `Determinism`, `Reproducibility` and `Fabric writers` rows promote pages whose commands are already stable (or, for Fabric, reached through stable `generate`).

## 3. Recommended 0.9.3 promotion set

Rule used: promote only what already has a test for every claim, touches no service or has a fresh passing run, and has a surface that is small enough to freeze. A narrower promise is safer: promotion adds, demotion breaks.

Do first (W0, M): the P-BAR check and `docs/AVAILABLE.md`. Without them the labels below are by hand. W0 also holds the ratchet list for the 71 existing available pages.

### Priority order

| # | Item | Kind | Why now | Size |
|---|---|---|---|---|
| 1 | `docs/REPRODUCIBILITY.md` | page | Uses only stable `shape pack`. Every claim maps to a test (V1-02, V1-18 to V1-22 in `V1_DONE.md`). No service. Needs a claim table and a CHANGELOG line. | S |
| 2 | `docs/DETERMINISM.md` | page | 12 lines. Backed by random-access and kernel-parity tests. Two sentences have no test (scan cost, idempotent sinks): cut or test them. | S |
| 3 | `docs/QUICKSTART.md` | page | Already labelled available. One line uses experimental `shape query`. Remove that line, keep the label, make it earned. | S |
| 4 | `shape canary` + `docs/CANARIES.md` | command | Small surface (`make`, `check`). Every claim has a named test. Local only. No owner note. | S |
| 5 | `shape gameday` + `docs/GAMEDAY.md` | command | Small surface (`run`). Tests cover source immutability, allowed commands, local-only data, exit codes. One open design point (dry-run JSON). | S |
| 6 | `docs/SIGNING.md` | page | `keygen`, `sign`, `verify` are stable and well tested. Move the vault and `migrate` text out, drop `query` and the Key Vault sentence (only a fake resolver is tested). | M |
| 7 | `docs/VERIFY.md` | page | `verify` and `check` are stable (V1-11 to V1-15). Move the scorecard, skew and report-card section out. | M |
| 8 | `shape mask` + `docs/MASK.md` | command | Best-tested of the audit five. No service. Needs three small fixes before the freeze (section 4). | M |
| 9 | Synapse write: `shape generate --to synapse://` on `docs/plugins/fabric-writers.md` | page section | `generate` is stable, so P-BAR 1 holds by flag. One passing live run (38338859503, exit 0) on a commit whose Synapse source is unchanged since. Re-record on the release head. Other sections of the page are fenced as experimental. | M |

Why the audit five are split. `canary`, `gameday` and `mask` go. `rules` and `share-bundle` wait:

- `rules`: the engine is well tested, but the page's worked example contradicts its fixtures, `backtest` has no transcript, and mutant kinds come from experimental `chaos`. Freezing `rules mutate` would freeze those names.
- `share-bundle`: `exitcodes.py` omits create exit 1, the `--json` output has only a text `output` key, `verify --public-key` differs from stable `--key`, and the page has no runnable example. Each of those is a frozen mistake if promoted now.

### Not in 0.9.3, and why

- `docs/CLI.md`: split it (W-cli, M) so that the stable half can be promoted in 0.9.4. 44 experimental commands are documented on it. The page also says `--json` and `--dry-run` work on every command while V1-17 is open.
- `docs/SINKS.md` and the Fabric Lakehouse/Warehouse/SQL database/Delta/abfss claims: no recorded run contains the Lakehouse-kind lookup or the abfss fix. If the live runs listed in section 4.9 pass on the release head, `abfss://` into a Lakehouse Files folder can be added to item 9 as a stretch. `SINKS.md` itself stays experimental: it also covers Postgres, MySQL, Snowflake, Databricks and ADLS, whose receipts are 0.9.1 and predate `--table-prefix`.
- Plugin commands (`setup-fabric`, `profile-db`, `publish`, ...): none can be stable until a mechanism exists (decision 2).
- `emit`, `stream`, `stream-profile`: large pages, Kafka and Event Hubs receipts are 0.9.1 or CI containers, no Event Hubs windows/`--follow` run.
- `fidelity`, `report-card`, `scorecard`, `skew`, `proposals`, `bridge`, `demo`, `vault`, `chaos`: large flag surfaces, none reviewed for a freeze.
- Candidates for 0.9.4: `rules`, `share-bundle`, `vault`, `dictionary`, `report-card`, `contracts`, `project`/`changes`, `CLI.md` (stable half).

## 4. Per item: surface, pre-freeze changes, work packages

Surfaces were read from `tests/cli/cli_surface_v1.json` and `shape <cmd> --help`, and the `--json` keys from the code and by running each command on a tiny local input. `--json` is the `shape-result` envelope (`format, version, command, exit_code`, plus `error` on failure); payload keys are flat next to it. Exit classes are 0 ok, 1 a check failed, 2 bad input.

### 4.1 `shape canary`

**Frozen surface**
- `canary make library:NAME|ID` (also a bare failure-mode id): `-o/--output DIR` (required), `--rows N` (1000), `--seed SEED`, `--marker COLUMN=VALUE` (`shape_canary=1`), `--format {csv,parquet,jsonl}` (csv), `--dry-run`, `--json`.
- `canary check canary.json`: `--result RESULT.json` (required, repeatable), `--json`.
- Exits: make 0 / 2; check 0 / 1 (blind spot) / 2.
- `--json` keys. make: `format, version, id, scenario, seed, marker, expected, directory, files, rows, dry_run, command, exit_code, payload`. check: `canary, ok, present, blind_spots, fired` plus envelope.
- Also frozen in practice: the `shape-canary` v1 document.

**Change before freezing:** none required. Document that the positional also takes a bare id.

**Work packages**
- W-canary-1 (S): test that pins both `--json` key sets; JSON schema and a frozen sample for `shape-canary`.
- W-canary-2 (S): claim table in `docs/AVAILABLE.md` (claims map to `tests/scenario/test_canary.py`, 54 tests; page runs through `test_reference_page[CANARIES]`).
- W-canary-3 (S): add to `STABLE`; `python scripts/cli_surface.py --write`; `### Promoted` line; `Status: available`.

### 4.2 `shape gameday`

**Frozen surface**
- `gameday run PLAN.json`: `-o/--output DIR` (required), `--seed SEED` (42), `--dry-run`, `--json`.
- Exits: 0 (always for `--dry-run`), 1 an expectation missed, 2 bad plan, disallowed command, non-local data, unreadable table.
- `--json` keys: `format, version, plan, data, seed, detected, seconds, rounds, command, exit_code, payload`. Round: `name, inject, scenario, tables, injected, checks, fired, expectations, detected, seconds`.
- Also frozen in practice: plan format `shape-gameday` v1; the allowed check words (`profile, diff, check, verify, fidelity`); placeholders `{data}`, `{round}`, `{plan}`.

**Change before freezing**
- `--dry-run --json` returns a full report with a meaningless `detected`. Return the generic `shape-dry-run` document instead (additive for scripts; do it before the freeze).
- Document `seconds` as volatile and the Markdown report as not a contract.

**Work packages:** W-gameday-1 (S) dry-run document and its test; W-gameday-2 (S) tests for the Markdown report and the `--json` auto-add (the page says Shape adds `--json` to your checks; no test); W-gameday-3 (S) claim table, schemas for plan and report; W-gameday-4 (S) STABLE, surface snapshot, CHANGELOG.

### 4.3 `shape mask`

**Frozen surface**
- Positional `PATH` (file or directory). `-o/--output DIR` (required), `--format {csv,parquet}`, `--seed SEED` (42), `--key-file FILE` / `--key-env VAR` (mutually exclusive), `--exclude COLUMN` (repeatable), `--pii COLUMN=TYPE` (repeatable), `--json`, `--dry-run`.
- Exits: 0 and 2 only. 2 covers bad input, an unknown column in `--exclude`/`--pii`, an unset key variable, a group-readable key file, a key file inside the output folder, output that would overwrite the input.
- `--json` keys: `files, keyed, columns_masked, column_types, stats, format, version, command, exit_code`; `stats` is `{table: {total_cols, masked_cols, rows}}`.
- The `--pii` type names are part of the surface: `email, phone, first_name, last_name, name, address, city, state, zip, ssn, credit_card, ip_address, username, date_of_birth, iban`.

**Changes before freezing**
1. Output folder: canary and gameday refuse a non-empty `-o`; mask creates it and silently overwrites same-named files. Pick one rule and test it. Recommend: refuse a non-empty folder, as the other two do.
2. `--seed` with a key is accepted and ignored. Reject it (exit 2) or document it. Recommend reject.
3. Reading a CSV can print a warning that advises `--string-columns`, a flag `mask` does not have. Suppress or reword the hint, and fix the page transcript.
4. Optional, permanent once frozen: one `--key` taking `env://` and `file://` as `sign --key` does, with `--key-file`/`--key-env` kept as aliases. Recommend not doing it now.

**Work packages:** W-mask-1 (S) items 1 to 3 with tests; W-mask-2 (S) page: add `--dry-run`, the scope of `--exclude`/`--pii`, THREAT_MODEL rows for key handling; W-mask-3 (S) CLI golden test for unkeyed output, and a test that pins the free-text limit; W-mask-4 (S) claim table, STABLE, snapshot, CHANGELOG (no separate note for the e-mail-domain change was found).

### 4.4 `docs/REPRODUCIBILITY.md`, `docs/DETERMINISM.md`, `docs/QUICKSTART.md`

No new command surface: they use stable commands already in the snapshot. What the label then promises is the page's behaviour claims.

- REPRODUCIBILITY: W-repro-1 (S) claim table from `tests/scenario/test_reproducibility.py` and `tests/generation/test_w1_15_pinned_fixtures.py`; W-repro-2 (S) mark the Python API parts (`shape.repro`, `shape.compat`) as experimental, since they are not in `API_STABILITY.md`'s stable list; W-repro-3 (S) CHANGELOG line.
- DETERMINISM: W-det-1 (S) claim table from `tests/generation/test_random_access.py`, `tests/generation/test_first_per_parent_g2.py`, `tests/scenario/test_reproducibility.py`; W-det-2 (S) cut or test the scan-cost and idempotent-sink sentences; move the streaming paragraph to `EMIT.md`; W-det-3 (S) decide the link to experimental `GENERATION_ENGINE.md`.
- QUICKSTART: W-qs-1 (S) delete the `shape query` line from example 2, its expected output and its manifest entry together; W-qs-2 (S) rename the "Models examples" section and delete its `Status: experimental` line; W-qs-3 (S) claim table (examples run through `test_reference_page[QUICKSTART]`; tutorials through `tests/docs/test_launch_tutorials.py`); CHANGELOG line.

### 4.5 `docs/SIGNING.md`

- Surface: stable `keygen`, `sign`, `verify`, `inspect`, `show`, `cat`, `check`, `diff`, `plan` (`--verify`), `profile --sign`, `capture --sign`. Already in the snapshot.
- Change before labelling: move the "Signed artifacts and migration" text, example 4 and the vault paragraph to `VAULT.md` or a migration page (experimental); drop `query` from the `--verify` list; reword `kv://` to "a secret store, through a resolver the host registers", or record a real Azure Key Vault run.
- Work packages: W-sign-1 (M) move text, example and its manifest entry (`scripts/docs_example_setup.py` too); W-sign-2 (S/M) claim table from `tests/artifact/test_signing.py`, `tests/artifact/test_signing_keys.py`, `tests/cli/test_bugs_cli_cat_verify.py`; W-sign-3 (S) Python API stance (`shape.artifact`); CHANGELOG line.

### 4.6 `docs/VERIFY.md`

- Surface: stable `verify` (`--schema --config --source --statistical --strict -o --key`), `check`, `profile`. Already in the snapshot.
- Change before labelling: move "Scores by dimension and failing rows" to `SCORECARD.md`; reword the `privacy` row of the config table (it names `report-card`); mark the `shape.quality` Python parts.
- Work packages: W-verify-1 (S) move the section and row; W-verify-2 (M) claim table (about 40 rows from `tests/quality/test_verify.py`, `test_gates.py`, `test_verify_config.py`, `test_memorization.py`, `test_timeseries.py`, `test_reconcile.py`); the utility-gate tests did not collect here (no scikit-learn) — cite the file until CI ids are read; W-verify-3 (S) Python API marker; CHANGELOG.

### 4.7 Synapse write through `generate`

- Frozen by flag: `generate --to --auth --yes --table-prefix --sink-config --write-mode --lakehouse-schema ...` are already in the snapshot. Not frozen by the snapshot: the `synapse://` scheme and the `--sink-config` keys (`staging_path` is required).
- Change before labelling: say on the page that the pool must be Online and `staging_path` is required; cite run 38312820840 for the NaN-reads-as-NULL claim or drop it (no file of that run is in the repo).
- Work packages: W-syn-1 (S) dispatch `fabric-live.yml` on the release head with `transcripts=true examples=synapse-write`, commit the receipt; W-syn-2 (S) link `plugins/shape-fabric/tests/test_live_synapse.py::test_synapse_round_trip` as the live test; W-syn-3 (S) fence the Warehouse, SQL database, Lakehouse and Delta sections with experimental markers; W-syn-4 (S) a test that pins the documented `--sink-config` keys; CHANGELOG (0.9.2 already has Fixed lines for Synapse).

### 4.8 Items that wait (what stops them)

- `rules`: freeze `mutate DATA CONTRACT.json` (`--plan --seed --rate --diff --min-score -o --json --dry-run`) and `backtest REGISTRY NAME CONTRACT.json` (`--since --until --window {day,week,month} --incidents --fail-on-miss --compare -o --json --dry-run`). Fix first: the `--json` help text, the worked example, a backtest transcript, and declare the mutant kinds extensible or pin them. Also used by the Bridge as `rules_mutate` and `rules_backtest`. About 1 week.
- `share-bundle`: freeze `create DATA_DIR --source --classifications -o [--key --top-k --passphrase-env --passphrase-stdin --json --dry-run]` and `verify BUNDLE.zip [--public-key --json]`. Fix first: the exit-code registry entry (S), structured `--json` keys such as `dataset_id, tables, checks, signed, problems` (M), one key flag spelling shared with `fingerprint verify` (S), a runnable page example (S/M), a `--require-signature` option for signed bundles checked without a key (S). About 1 week.

### 4.9 Fabric live runs, one list

Record these on the release head, in this order, then decide what joins the 0.9.3 set. All need the Owner's workspace and secrets; `fabric-live.yml` runs them by dispatch.

| Run | Closes | Size |
|---|---|---|
| `examples=synapse-write` | item 9 | S |
| `abfss://` into a Lakehouse Files folder: split `SINKS.md` example 0 into one example per command, fix its Delta command (name form fails at HEAD with "cannot tell whether Lakehouse ... is schema-enabled"), run `test_live_onelake_sink.py` | `SINKS.md:66` | S (Files only), M (all) |
| Delta to Lakehouse Tables, classic and schema-enabled, prefix, `--lakehouse-schema` (`test_live_lakehouse_visibility.py`, `test_live_owner_matrix.py`) | Lakehouse claims | M |
| `publish`: split example 0 into four re-runnable examples (use `--write-mode replace` or a unique `--schema-name`) | `fabric-commands.md:59` | S |
| `deploy-notebook`: unique `--notebook-name` or delete the old item (409 in run 38337418136) | `fabric-commands.md:95` | S |
| Warehouse COPY INTO, Fabric SQL database, `fabric_spark` to completion, git sync, ADLS/OneLake read-back | later pages | M |

Stale text to fix at the same time: `FABRIC_PLATFORM.md` says "not run yet"; `fabric-auth.md` says a Kerberos run is "an owner action" (a receipt exists: `docs/evidence/kerberos-live.md`); `fabric-commands.md` example 2 still prints `shape notebook` and `Shape v0.9.1`; `sqlserver.md` example 1 prints a `shape_content_id` that changed when `profile/sampling.py` changed.

### 4.10 Order of work and dependencies

W0 first. Pages 1 to 3 need only W0 and text. Items 4 to 8 can run in parallel. Item 9 needs the Owner to run or approve the live job. Every promoted command needs `python scripts/cli_surface.py --write` in the same commit as its `STABLE` entry, and `docs/CLI_STABILITY.md` updated (the table of stable commands).

## 5. Risks and open questions for the Owner

1. **Legacy labels (ratchet or relabel).** 71 pages say available with no proof; 15 mention experimental commands, and `DICTIONARY.md` and `PROJECT.md` are plain mislabels. *Recommend:* freeze the list in `tests/docs/available_legacy.txt`, let it only shrink, and relabel nothing silently in 0.9.3. Move `DICTIONARY.md` and `PROJECT.md` to the front of the 0.9.4 queue.
2. **Plugin commands cannot be stable.** `STABLE` holds core names; `cli_surface.py` snapshots the core parser; adding a plugin name fails `test_every_stable_command_exists_in_the_parser`. P-BAR 1 therefore blocks all 22 plugin commands for ever. *Recommend:* promote no plugin command in 0.9.3; reach Fabric through stable `generate`. Decide whether to build the mechanism (`STABLE_PLUGIN_COMMANDS`, a second snapshot `cli_surface_plugins_v1.json`, plugin version pinned to the core minor; size M, 2 to 3 days) for 0.9.4. Mixing the plugin's release cycle into a core promise is the risk.
3. **Evidence freshness.** Most receipts ran 0.9.1 and predate `--table-prefix`, the Lakehouse-kind lookup and the abfss fix. *Recommend:* a receipt counts when it names a Shape version of the same minor as the release, or the source of that path is unchanged since. Re-record Fabric runs on the release head. Receipts on 0.9.1 for unchanged code (Synapse) may stand.
4. **What is a "real" run.** *Recommend:* a service started from a container (PostgreSQL, MySQL, SQL Server, Kafka) counts; an emulator of a cloud service does not. Cloud services (Fabric, Synapse, Event Hubs, ADLS) need a cloud run.
5. **Mixed pages: fence or split.** *Recommend:* in-page experimental fences for most pages (VERIFY, SIGNING, fabric-writers); one real split, `CLI.md`. The fence markers must render as a visible warning box.
6. **Python API stance.** The pages show `shape.quality`, `shape.repro`, `shape.artifact`, `shape.check`; `API_STABILITY.md` lists only two stable modules. P-BAR speaks of commands. *Recommend:* "available" on a page covers its CLI and documented behaviour; Python parts stay fenced as experimental until the API list names them.
7. **Surface edits before the freeze.** `gameday` dry-run document; `mask` output folder rule and `--seed` with a key; `share-bundle` key flag and `--json`. *Recommend:* make them now; each is cheaper before the freeze than after, and they are additive or tighten error cases.
8. **`rules` mutant kinds.** They come from experimental `chaos`. *Recommend:* declare the kind list extensible in the page and the schema, so `rules` can freeze without freezing `chaos`; keep `rules` in 0.9.4.
9. **Release note convention.** *Recommend:* a `### Promoted` heading in each release section of `CHANGELOG.md`; one line per page or command; the check reads it.
10. **Remove or keep `privacy-k` and `quality`.** No page; they overlap `profile` and `check`/`verify`; few tests. *Recommend:* remove in 0.10, with a note in 0.9.3 `### Changed`, since experimental commands may go at any minor release. The Owner decides.
11. **Cost and access of live runs.** Fabric runs need the Owner's capacity (the Synapse dedicated pool is resumed and paused by the workflow) and secrets. *Recommend:* the lead dispatches one batch on the release head, after the Owner confirms the workspace is clean of leftovers (`delete_leftover_notebooks`).
12. **Size of the claim tables.** About 25 to 40 rows per page, all by hand. *Recommend:* accept; the rows are what makes "available" mean something. Count it in the sizes above.

Top five for the Owner: decisions 1, 2, 3, 4 and 5.

## Appendix: facts to keep

- `scripts/check_docs_owners.py --release` runs only in `docs.yml`, not in `make check`.
- `tests/docs/test_launch_tutorials.py::test_tutorial_template` is the only test that asserts a `status:` value.
- `FAILURE_MODES.md` and `plugins/api-v1.md` are generated and write `Status: experimental`.
- Mixed labels already exist: `docs/MODELS.md` says `available (shipped model commands); experimental (...)`.
- Live `pytest -m live` results are not committed anywhere. Only the nine ids in `tests/docs/fabric_examples.json` can leave a committed receipt.
- Run ids 38312820840 and 38245654398 are cited in prose with no file in the repo.
