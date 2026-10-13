# Promotion plan for 0.9.4 (written as 0.9.3)

Status: plan. Not a user-facing page. Written for the lead (session `PROMO-093`). Revised 2026-10-13 for the Owner's decisions.

**Retargeted to 0.9.4 (Owner, 2026-10-13).** The release that was 0.9.2 ships as 0.9.3 (0.9.2 went to TestPyPI only), so every "0.9.3" below means the next release, 0.9.4. The file name is kept so existing links work.

Base: `int/0.9.2` at `c10923be9278bc63f2713ac0ed32a548b59a7afb`. Fabric evidence read from `origin/docs/fabric-transcripts` at `10c4cd633f282d7e5c3a0a5cf44f205c9d926b80`. This plan builds no code and changes no label.

## Decisions (Owner, 2026-10-13)

The Owner's words, relayed by the lead: "Recommendations except #2. We want to get those in .9.3", then "6–16 yes".

| # | Decision | Owner answer | Effect on this plan |
|---|---|---|---|
| 1 | Legacy available pages | Accepted | Shrink-only ratchet list (`tests/docs/available_legacy.txt`). No silent relabels in 0.9.3. |
| 2 | Plugin commands | **Reversed.** Plugin commands get in 0.9.3. | New work package W-plug (section 6). The 22 plugin commands are re-run against P-BAR (section 7). Nine are promoted. |
| 3 | Evidence freshness | Accepted | A receipt counts if it is from the same release line, or the source of that path is unchanged since. Fabric is re-recorded on the release head. See 1.2 for how "same release line" is read. |
| 4 | What is a real run | Accepted | Self-hosted containers count. Cloud emulators do not. |
| 5 | Mixed pages | Accepted | In-page experimental fences; one real split (`CLI.md`). |
| 6 to 12 | Python API stance, surface edits before the freeze, `rules` mutant kinds, `### Promoted` heading, remove `privacy-k` and `quality`, live-run batch, claim-table cost | Owner-confirmed ("6–16 yes", 13 Oct, about 14:20Z) | Section 5 marks each. |
| 13 | `profile-db --client-secret` | Accepted | Takes `env://`, `file://` or `kv://` references only and refuses a literal, before the freeze. Folded into the `profile-db` work package (4.11). |
| 14 | Two spellings of each Fabric command | Accepted | Freeze both (`shape publish` and `shape fabric publish`, and so on). |
| 15 | "Same minor version" | Accepted | Means the same release or unchanged source (1.2). |
| 16 | Plugin and core version coupling | Accepted | A stable plugin command is promised only with a plugin of the same release as the core, stated in `CLI_STABILITY.md`. |

Correction to the Fabric evidence (relayed by the lead). The three Fabric owner notes are resolved by real runs on `docs/fabric-transcripts` at `10c4cd633f282d7e5c3a0a5cf44f205c9d926b80`. That branch lands on `int/0.9.2` shortly. I checked each file with `git cat-file -e` on that commit:

| Note | Example | Run | Result | File on that branch |
|---|---|---|---|---|
| `docs/SINKS.md:66` | `sinks-onelake` | 38398883944 (commit `5fb3694b`, has the abfss fix `4f819a01`) | passed, exit 0 | `docs/evidence/transcripts/docs-transcripts-38398883944-5fb3694b5da5-ubuntu-latest-sinks-onelake.json` |
| `docs/plugins/fabric-commands.md:59` | `publish-targets` | 38400931371 (commit `c37ff1b1`, after the pre-clean) | passed, exit 0; all four targets ran (lakehouse, sql-database, warehouse, eventhouse) | `docs/evidence/transcripts/docs-transcripts-38400931371-c37ff1b1dadb-ubuntu-latest-publish-targets.json` |
| `docs/plugins/fabric-commands.md:95` | `notebook-deploy` | 38400931371 | passed, exit 0 | `docs/evidence/transcripts/docs-transcripts-38400931371-c37ff1b1dadb-ubuntu-latest-notebook-deploy.json` |

The pre-clean record is `docs/plans/evidence/owner-notes/preclean-38400931371.txt`. On that branch `check_docs_owners.py` finds none of the three notes; the other notes it lists are older text on a stale base, not open on `int/0.9.2`. These files are not on this plan's branch yet, so in the table they carry the prefix `B/` (on `docs/fabric-transcripts`). The two runs' commits contain the Warehouse key fix, the Synapse fix and the abfss fix. They do **not** contain the Lakehouse-kind lookup (`3dd1d47`, `76800ef`, in `int/0.9.2`), so Lakehouse schema-enabled claims still have no run. The transcripts print `Shape v0.9.1` in their banners.

## 0. Summary

- Today 24 commands are stable (`src/shape/cli/stability.py`). 60 core commands and 22 plugin-added commands are not. 93 docs pages say `Status: experimental`; 71 start with `Status: available`.
- Nothing checks a docs label. Of the 71 available pages, 15 mention an experimental command. Two are clear mislabels: `docs/DICTIONARY.md` (`shape dictionary`) and `docs/PROJECT.md` (`shape project`, `shape init`).
- No plugin command can be stable today. `cli_surface.py` snapshots the core parser only. The Owner reversed my advice: plugin commands go into 0.9.3. Section 6 designs the mechanism (size M).
- With the three Fabric notes resolved, Fabric has passing runs for `setup-fabric`, `deploy-notebook`, `publish` (four targets), `profile-db` on a Fabric Warehouse, and Synapse write. Nothing recorded covers the Lakehouse-kind lookup.
- Recommendation: promote **11 rows** in 0.9.3 (12 commands, 11 pages), keep 44 for later, keep 14 experimental, mark 2 commands as candidates to remove. Total work about 45 person-days (range 35 to 60), about 4 weeks elapsed with three people (section 8).
- Build the P-BAR check (W0, M) and the plugin mechanism (W-plug, M) first, in parallel. Everything else waits on one or both.
- Re-record Fabric runs on the release head. Do not promote what has no run on a commit whose source matches the release.

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
- **Fresh.** Decision 3. The receipt names the release's own version, or the source files of that path are unchanged since the run (`git diff --quiet <commit> HEAD -- <paths>`). For a 0.x release I read "same minor version" as "same release", because a 0.9.x patch release changes code (0.9.2 changed the Warehouse, Synapse and Lakehouse writers). The check records `shape=<version>` and `paths=<list>` in the receipt line. If the lead means 0.9.x as one line, the `paths=` test still catches changed code, so only the first clause changes.
- **Release note.** A line under `### Promoted` in the CHANGELOG section of the release that names the page file and the command.

### 1.3 Proposed check (design only)

Name: `scripts/check_available.py`. Same style as `scripts/check_v1_done.py`: stdlib only, exit 0 clean, 1 a rule fails, 2 usage or unreadable input.

**What it parses**

| Input | Used for |
|---|---|
| `docs/AVAILABLE.md` | The claim-to-proof tables (below). |
| Every `docs/**/*.md` outside `plans` and `talks` | The `Status:` line, `<!-- claim: ... -->` anchors, `<!-- owner: ... -->` notes, `Covers:` line. |
| `src/shape/cli/stability.py` | `STABLE` and, after W-plug, `STABLE_PLUGIN_COMMANDS` (imported). |
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

1. P-BAR 1. For a page with `Status: available` and a row set in AVAILABLE.md: every `shape <cmd>` in the page text outside experimental blocks, and every name in `Covers:`, must be in `STABLE` or `STABLE_PLUGIN_COMMANDS`. Undeclared mentions fail ("page mentions `shape mask`; add it to Covers or fence it").
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

1. Commands: `shape --help` lists 85 commands (with the plugins in this environment installed). `tests/cli/cli_surface_v1.json` holds 84 core commands; the 22 commands absent from it are plugin-added (`(plugin <name>)` in `--help`). `STABLE` has 24. So 60 core and 22 plugin commands are experimental (60 + 24 = 84). `compare` is an alias of `fidelity`.
2. Pages: `grep -rlE '^Status: experimental' docs --include=*.md` outside `docs/plans` and `docs/talks`: 93 pages. `QUICKSTART.md` is counted because line 48 says `Status: experimental` for one section; its page line says available.
3. Tests: `pytest --collect-only -q -p no:cacheprovider --continue-on-collection-errors tests plugins` after `pip install -e ".[dev]"`, the plugins, and `confluent-kafka azure-eventhub azure-identity fhir.resources pyx12 ms-fabric-cli nbformat`: 21,995 node ids. Six modules did not collect here: `tests/demo/fabric/test_udf.py` and five in `plugins/shape-healthcare-codes/tests/`. No id is cited from them. Every id in this plan was matched exactly against that list. A node id shown without `[...]` names a parametrized family.
4. Docs examples: `tests/docs/reference_examples.json` gives the example kinds per page; `tests/docs/test_reference_examples.py::test_reference_page[<PAGE>]` runs the `local` and `bootstrap` ones. It is listed as proof for the page, not for a claim.
5. Owner notes: `scripts/check_docs_owners.py` (3 open, all Fabric).
6. Evidence: `docs/evidence/`, `docs/evidence/transcripts/` and `docs/plans/evidence/owner-notes/`. Transcript status and exit code were read from each JSON.
7. Rows group commands and pages that share a feature. A check script confirmed that every one of the 82 commands and 93 pages is in exactly one row (70 rows).

Not exhaustive by design: "existing proof" lists up to three representative ids plus the test count in the named paths. The full claim-to-proof table is work package W-claims for each promoted item, not this table.

### 2.2 Legend

- Evidence paths: `B/` = on `docs/fabric-transcripts` at `10c4cd63`, not yet on `int/0.9.2`; `E/` = `docs/evidence/`; `O/` = `docs/plans/evidence/owner-notes/`; `T/<example>` = `docs/evidence/transcripts/docs-transcripts-<run>-<sha>-ubuntu-latest-<example>.json`, with the run id and status in brackets. `O/…<run>…-<example>.json` is a failed attempt kept in `owner-notes`.
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
| Data dictionary | `dictionary` | none (no experimental page) | `tests/dictionary/test_dictionary.py::test_json_document_declares_format_and_version`<br>(27 tests in the named paths) | none | none | STABLE; docs/DICTIONARY.md already says available while the command is experimental. | S | later |
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
| Query a capture | `query` | none (no experimental page) | `tests/cli/test_five_cli.py::test_query_check_compatibility_plan_registry`<br>(1 tests in the named paths) | none | none | STABLE; expression language has no spec page; only 1 test found; used by QUICKSTART example 2. | M | later |
| Equivalence-group size | `privacy-k` | none (no experimental page) | `tests/regressions/test_hunt2_cli.py::test_init_writes_a_valid_file_for_a_number_like_name`<br>(53 tests in the named paths) | none | none | No page; overlaps `profile` k-anonymity; no dedicated tests found. | S | candidate to remove |
| Rule check from a capture | `quality` | none (no experimental page) | `tests/quality/test_bugs_cli_type_mismatch.py::test_wrong_type_is_a_violation_not_a_crash`<br>(17 tests in the named paths) | none | none | No page; overlaps `check` and `verify`. | S | candidate to remove |
| Built-in conformance suite | `conformance` | none (no experimental page) | `tests/conformance/test_repository_constitution.py::test_requirements_registry`<br>`tests/cli/test_cli_e2e.py::test_conformance_cli`<br>(4 tests in the named paths) | none | none | STABLE; conformance catalog is its own versioned list; tied to the SHAPE_2 draft. | M | later |
| Generate drift | `generate-drift` | none (no experimental page) | `tests/generation/test_drift_plan.py::test_a_boolean_rate_can_drift_too`<br>(29 tests in the named paths) | none | none | No experimental page (DRIFT.md is available); STABLE; claim table. | M | later |
| Sinks: where generated data goes | none | `SINKS.md` | `tests/generation/test_sink_formats_and_schemes.py::test_every_registered_sink_is_an_accepted_format`<br>`plugins/shape-fabric/tests/test_live_onelake_sink.py::test_the_abfss_sink_publishes_files_into_a_lakehouse_files_folder`<br>`tests/docs/test_reference_examples.py::test_reference_page[SINKS]`<br>(27 tests in the named paths) | `E/postgresql-export-profile.md`<br>`E/mysql-export-profile.md`<br>`E/snowflake-write.md`<br>`E/databricks-write.md`<br>`O/adls-gen2-write.md`<br>`T/postgres-sink` (38266883768 passed; 38269228192 passed; 38273696299 passed)<br>`B/T/sinks-onelake` (38398883944 passed, exit 0) | `SINKS.md`: Fabric maintainer — supply the transcript for docs/SINKS.md … (resolved on `docs/fabric-transcripts` @ `10c4cd63`) | Owner note resolved on `docs/fabric-transcripts` (run 38398883944 passed, with the abfss fix; it lacks the Lakehouse-kind lookup now on int/0.9.2). Re-run on the release head; Postgres, MySQL, Snowflake, Databricks, ADLS receipts are 0.9.1 and predate `--table-prefix`. First page to add if the release-head run passes. | M | later |
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
| SQL Server, Azure SQL, Fabric SQL | `profile-db` | `plugins/sqlserver.md` | `plugins/shape-sqlserver/tests/test_profiler.py::test_a_connection_or_a_string_is_required`<br>`plugins/shape-sqlserver/tests/test_profiler.py::test_a_table_filter_keeps_only_those_tables`<br>`tests/docs/test_reference_examples.py::test_reference_page[sqlserver]`<br>(185 tests in the named paths) | `O/sqlserver-live.md`<br>`E/kerberos-live.md`<br>`T/profile-db-warehouse` (38337418136 passed)<br>`T/sql-sampling-measurement` (38266883768 passed; 38269228192 passed; 38273696299 passed) | none | Needs W-plug. `profile-db` has `--json`; its `--client-secret` is a plain value (take a reference before the freeze); printed `shape_content_id` is stale (re-record); no Azure SQL run: fence that claim. SQL Server (0.9.1) and Fabric Warehouse runs exist. | M | promote in 0.9.3 |
| Cloud sources (OneLake, ADLS Gen2, Delta) | none | `plugins/cloud-sources.md` | `tests/connectors/test_adapters.py::test_adapter_contracts`<br>(13 tests in the named paths) | `E/semantic-model-notebook.md`<br>`O/adls-gen2-write.md` | none | `semantic-model://` has a notebook receipt (0.9.1); OneLake/ADLS reads use contract tests and Azurite only; ADLS read failed on 0.9.1, fixed, not re-run. | M | later |
| Fabric sign-in (--auth) | none | `plugins/fabric-auth.md` | `plugins/shape-fabric/tests/security/test_exact_checks.py::test_a_copy_location_with_a_trailing_newline_is_refused`<br>(1303 tests in the named paths) | `E/kerberos-live.md` | none | `--auth` values on stable `generate`; kerberos has a receipt; spn, msi, device-code have no recorded run; page text 'owner action' is stale. | M | later |
| Fabric writers (Warehouse, Synapse, SQL database, Lakehouse) | none | `plugins/fabric-writers.md` | `plugins/shape-fabric/tests/test_live_synapse.py::test_synapse_round_trip`<br>`plugins/shape-fabric/tests/test_live_owner_matrix.py::test_publish_warehouse_keys_copy_and_cleanup`<br>(1303 tests in the named paths) | `T/synapse-write` (38338859503 passed) | none | Narrow to Synapse via `generate --to synapse://`: run 38338859503 passed, Synapse source unchanged since; re-record on release head. Warehouse, SQL database and Lakehouse sections need new runs: mark them experimental in-page. Cite run 38312820840 or drop the NaN claim. | M | promote in 0.9.3 |
| Fabric commands | `setup-fabric`, `publish`, `notebook`, `deploy-notebook`, `fabric`, `export-model`, `known-answer`, `check-answers` | `plugins/fabric-commands.md` | `plugins/shape-fabric/tests/test_live_commands.py::test_setup_creates_an_environment_and_a_second_run_finds_it`<br>`plugins/shape-fabric/tests/test_live_commands.py::test_deploy_notebook_creates_a_notebook_item_and_it_can_be_removed`<br>`plugins/shape-fabric/tests/test_live_commands.py::test_publish_to_a_lakehouse_writes_the_landing_zone_and_the_manifest`<br>`tests/docs/test_reference_examples.py::test_reference_page[fabric-commands]`<br>(1303 tests in the named paths) | `T/setup-fabric` (38337418136 passed)<br>`B/T/publish-targets` (38400931371 passed, exit 0)<br>`B/T/notebook-deploy` (38400931371 passed, exit 0)<br>`B/O/preclean-38400931371.txt` (Owner-approved pre-clean) | `plugins/fabric-commands.md`: Fabric maintainer — supply the transcript for docs/plugins/f… (resolved on `docs/fabric-transcripts` @ `10c4cd63`)<br>`plugins/fabric-commands.md`: Fabric maintainer — supply the transcript for docs/plugins/f… (resolved on `docs/fabric-transcripts` @ `10c4cd63`) | Needs the plugin mechanism (W-plug). All four `publish` targets, `deploy-notebook` and `setup-fabric` have passing runs (setup-fabric on an older head: re-record). Owner notes resolved on `docs/fabric-transcripts`. Review surface first (aliases `shape setup-fabric`/`shape fabric setup`, `-t sql-database` vs `--source-type sql_database`); fence the XMLA-deploy claim; refresh stale page text (`Shape v0.9.1` banners, example 2). | L | promote in 0.9.3 |
| Semantic model profiling | `profile-model` | none (no experimental page) | `plugins/shape-fabric/tests/test_live_semantic_model.py::test_a_real_model_profiles_with_its_relationships_and_every_table_reads`<br>(1 tests in the named paths) | `E/semantic-model-notebook.md` | none | Runs only in a Fabric notebook; receipt (0.9.1) shows one table and no relationships. Needs a relationship run (`test_live_semantic_model.py`) and a plugin-mechanism entry; no page of its own (a section of `fabric-commands.md`, fenced). | M | later |
| Fabric platform inventory | none | `FABRIC_PLATFORM.md` | `tests/docs/test_fabric_platform.py::test_the_inventory_parses_and_every_row_is_complete`<br>`plugins/shape-fabric/tests/test_live_git_sync.py::test_shape_files_survive_a_git_sync_in_both_directions`<br>(42 tests in the named paths) | `T/git-sync-live` (38320894194 passed) | none | Git sync passed once (38320894194) and failed once (38327274076); both predate a git-sync fix; re-run. Page says 'not run yet'. | S | later |
| Behavior models | `behave` | `plugins/behavior.md` | `plugins/shape-behavior/tests/test_behaviors_group.py::test_builtin_is_registered_loads_and_conforms`<br>`tests/docs/test_reference_examples.py::test_reference_page[behavior]`<br>(187 tests in the named paths) | none | none | Plugin command; needs the plugin-command mechanism; claim table. | L | keep experimental |
| Healthcare code sets | `healthcare-codes` | `plugins/healthcare-codes.md` | `plugins/shape-healthcare-codes/tests/test_assets.py::test_codes_on_a_date_are_all_valid_and_billable_that_day`<br>`tests/docs/test_reference_examples.py::test_reference_page[healthcare-codes]`<br>(58 tests in the named paths) | none | none | Plugin command; licence of code sets to confirm; 5 test modules did not collect in this environment. | L | keep experimental |
| Healthcare standards (FHIR, X12) | none | `plugins/healthcare-standards.md` | `plugins/shape-healthcare-standards/tests/fhir/test_fhir_resources.py::test_every_sample_resource_validates_as_r4b`<br>(400 tests in the named paths) | none | none | No command; claim table; conformance claims. | L | keep experimental |
| Integrations (MLflow, lineage, evaluate) | `evaluate`, `lineage`, `mlflow` | `plugins/integrations.md` | `plugins/shape-integrations/tests/test_evaluate_unit.py::test_every_real_table_is_evaluated_by_default`<br>`tests/docs/test_reference_examples.py::test_reference_page[integrations]`<br>(168 tests in the named paths) | none | none | Plugin commands; no MLflow or OpenLineage run recorded. | L | keep experimental |
| Simulation patterns | `simulate` | `plugins/simulation.md` | `plugins/shape-simulation/tests/test_financial.py::test_a_far_future_time_or_huge_window_is_refused_not_simulated`<br>`tests/docs/test_reference_examples.py::test_reference_page[simulation]`<br>(218 tests in the named paths) | none | none | Plugin command; no outside service. | M | keep experimental |
| CTGAN model | `ctgan` | none (no experimental page) | `tests/fidelity/test_ctgan.py::test_fit_and_sample_through_sdv`<br>(7 tests in the named paths) | none | none | Plugin command from the core distribution; no page of its own. | M | keep experimental |

### 2.4 Counts

| Recommendation | Rows | Commands | Pages |
|---|---|---|---|
| promote in 0.9.3 | 11 | 12 (3 core: `canary`, `gameday`, `mask`; 9 plugin) | 11 |
| later | 44 | 57 | 58 |
| keep experimental | 14 | 11 | 24 |
| candidate to remove | 2 | 2 (`privacy-k`, `quality`) | 0 |
| **Total** | **71** | **82** (60 core + 22 plugin) | **93** |

The `Signing`, `Quickstart`, `Verify`, `Determinism` and `Reproducibility` rows promote pages whose commands are already stable. The `Fabric writers` row is reached through stable `generate`. The nine plugin commands are `setup-fabric`, `deploy-notebook`, `publish`, `notebook`, `export-model`, `known-answer`, `check-answers`, the `fabric` group (an alias form of the first seven plus `publish-report`, see 7.2) and `profile-db`.

## 3. Recommended 0.9.3 promotion set

Rule used: promote only what has a test for every claim, touches no service or has a fresh passing run, and has a surface small enough to freeze. A narrower promise is safer: promotion adds, demotion breaks.

Long poles first. W0 (the P-BAR check) and W-plug (the plugin mechanism) take about a week each and block everything else; start both on day 1, in parallel. The Fabric live batch (4.9) needs the Owner's workspace; ask for it on day 1 so it is ready when the code lands.

### Priority order

Order follows the Owner's "Fabric first" for plugin commands, then the cheap pages and the audit commands, which can run in parallel with the Fabric work.

| # | Item | Kind | Why now | Needs | Size |
|---|---|---|---|---|---|
| 1 | `shape setup-fabric` (and `shape fabric setup`) | plugin command | Passing run 38337418136 (`docs/evidence/transcripts`). Small surface. Page text stale. | W-plug; re-record on release head | S |
| 2 | `shape deploy-notebook` | plugin command | Passing run 38400931371 (`B/`). Small surface. | W-plug; unique notebook name or the pre-clean step; re-record | S |
| 3 | `shape notebook`, `shape known-answer`, `shape check-answers`, `shape export-model` | plugin commands | Local output only; no service. Claims are about generated files. Fence the "deploy through XMLA" claim: no live run. | W-plug; add the hyphen alias on `export-model --source-type`; claim table | S each (M for `export-model`) |
| 4 | `shape publish` (and `shape fabric publish`) | plugin command | Passing run 38400931371 ran all four targets. Largest surface (about 25 flags); the one most worth reviewing before the freeze. | W-plug; surface review (4.11); split example 0; re-record | M |
| 5 | `docs/plugins/fabric-commands.md` | page | Becomes available when 1 to 4 are proven. `profile-model` and `publish-report` sections are fenced. | claim table; stale text | M |
| 6 | Synapse write: `shape generate --to synapse://` on `docs/plugins/fabric-writers.md` | page section | `generate` is stable. Run 38338859503 passed; Synapse source unchanged since. | re-record; fence the other sections | M |
| 7 | `shape profile-db` + `docs/plugins/sqlserver.md` | plugin command | Passing run 38337418136 on a Fabric Warehouse; SQL Server receipt (0.9.1) and Kerberos receipt. Has `--json`. | W-plug; secret flags take references (4.11); re-record; fence the Azure SQL claim | M |
| 8 | `docs/REPRODUCIBILITY.md` | page | Only stable `shape pack`. Every claim maps to a test. | claim table | S |
| 9 | `docs/DETERMINISM.md` | page | 12 lines; backed by random-access and kernel-parity tests. | claim table; cut two sentences | S |
| 10 | `docs/QUICKSTART.md` | page | Already labelled available; one line uses `shape query`. | remove the line; claim table | S |
| 11 | `shape canary` + `docs/CANARIES.md` | command | Small surface; every claim tested; local only. | tests pin `--json` keys | S |
| 12 | `shape gameday` + `docs/GAMEDAY.md` | command | Small surface; tests cover immutability, allowed commands, local-only data. | dry-run document | S |
| 13 | `docs/SIGNING.md` | page | `keygen`, `sign`, `verify` stable and well tested. | move vault and `migrate` text out | M |
| 14 | `docs/VERIFY.md` | page | `verify`, `check` stable (V1-11 to V1-15). | move the scorecard section out | M |
| 15 | `shape mask` + `docs/MASK.md` | command | Best-tested of the audit five; no service. | three small fixes (4.3) | M |

Stretch, only if the release-head Fabric run passes: add `docs/SINKS.md` (abfss Files and Delta Tables, Postgres via CI container) with the Warehouse and schema-enabled Lakehouse sections fenced. It stays in "later" until then.

### Why the audit five are split

`canary`, `gameday` and `mask` go. `rules` and `share-bundle` wait:

- `rules`: the engine is well tested, but the page's worked example contradicts its fixtures, `backtest` has no transcript, and mutant kinds come from experimental `chaos`. Freezing `rules mutate` would freeze those names.
- `share-bundle`: `exitcodes.py` omits create exit 1, the `--json` output has only a text `output` key, `verify --public-key` differs from stable `--key`, and the page has no runnable example. Each of those is a frozen mistake if promoted now.

### Not in 0.9.3, and why

- `docs/CLI.md`: split it (W-cli, M) so that the stable half can be promoted in 0.9.4. 44 experimental commands are documented on it. The page also says `--json` and `--dry-run` work on every command while V1-17 is open.
- `profile-model`: runs only in a Fabric notebook; the receipt shows no relationships.
- `publish-report`: no live run recorded.
- `dbt-seeds`, `dbt-report`, `from-dbt`, `to-dbt-tests`: DuckDB-adapter examples run in CI, but `DBT.md` is 720 lines with six examples; a claim table is L work (7.1).
- `behave`, `simulate`, `ctgan`, `evaluate`, `lineage`, `mlflow`, `healthcare-codes`: see 7.1.
- `emit`, `stream`, `stream-profile`: large pages; Kafka and Event Hubs receipts are 0.9.1 or CI containers; no Event Hubs windows or `--follow` run.
- `fidelity`, `report-card`, `scorecard`, `skew`, `proposals`, `bridge`, `demo`, `vault`, `chaos`: large flag surfaces, none reviewed for a freeze.
- Candidates for 0.9.4: `rules`, `share-bundle`, `vault`, `dictionary`, `report-card`, `contracts`, `project`/`changes`, `CLI.md` (stable half), the dbt commands, `profile-model`, `publish-report`.

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
3. Reading a CSV can print a pandas warning that advises `--string-columns`, a flag `mask` does not have. Suppress or reword the hint, and fix the page transcript.
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

The three runs below passed on `docs/fabric-transcripts` and close the owner notes. Decision 3 still asks for a re-record on the release head, because the release head also holds the Lakehouse-kind lookup. All need the Owner's workspace and secrets; `fabric-live.yml` runs them by dispatch, after the pre-clean step.

| Run (on the release head) | Last passing run | Size |
|---|---|---|
| `examples=setup-fabric,deploy-notebook,publish-targets` | `setup-fabric` 38337418136; the other two 38400931371 (`B/`) | S |
| `examples=synapse-write` | 38338859503 | S |
| `examples=profile-db-warehouse` | 38337418136 | S |
| `examples=sinks-onelake` (abfss Files, stream, Delta Tables) | 38398883944 (`B/`) | S |
| Delta to Lakehouse Tables, classic and schema-enabled, prefix, `--lakehouse-schema` (`test_live_lakehouse_visibility.py`, `test_live_owner_matrix.py`) | none contains the Lakehouse-kind lookup | M |
| Warehouse COPY INTO and the 13840 fallback, Fabric SQL database, `fabric_spark` to completion, git sync, ADLS/OneLake read-back | partial or none | M |

Stale text to fix at the same time: `FABRIC_PLATFORM.md` says "not run yet"; `fabric-auth.md` says a Kerberos run is "an owner action" (a receipt exists: `docs/evidence/kerberos-live.md`); `fabric-commands.md` example 2 still prints `shape notebook` and `Shape v0.9.1`; `sqlserver.md` example 1 prints a `shape_content_id` that changed when `profile/sampling.py` changed.

### 4.10 Order of work and dependencies

W0 and W-plug first. Pages 8 to 10 need only W0 and text. Items 11 to 15 can run in parallel. Fabric items 1 to 7 need W-plug (to be listed) and the Owner's batch. Every promoted core command needs `python scripts/cli_surface.py --write` in the same commit as its `STABLE` entry, and every promoted plugin command needs `--plugins --write` (section 6). `docs/CLI_STABILITY.md` is updated in the same commit (the table of stable commands).

### 4.11 Plugin commands promoted in 0.9.3: surface, pre-freeze changes, work packages

Surfaces are from `shape <cmd> --help` with the first-party plugins installed. A freeze covers flag names, value kinds, choices, positionals and exit classes. It does not cover banner text (`Shape v0.9.1 — ...`) or output layout, as in `CLI_STABILITY.md`. Among the plugin commands that take flags directly, only `profile-db`, `profile-model` and `dbt-report` have `--json`, and only `publish` has `--dry-run` (the commands with subcommands were not checked). The promise therefore covers only what exists. Exit classes: 0 ok, 1 a check failed, 2 bad input.

**Both spellings are documented.** The commands exist at top level (`shape publish`) and under `shape fabric` (`shape fabric publish`, `... setup`, `... notebook`, `... deploy-notebook`, `... export-model`, `... known-answer`, `... check-answers`, `... publish-report`). `setup-fabric` is `fabric setup`. Decision 14: freeze both, because both are documented and removing either later would be a break.

| Command | Frozen surface | Change before the freeze | Work packages | Size |
|---|---|---|---|---|
| `setup-fabric` | `--workspace --create-lakehouse --env-name --lakehouse-name --snippet` and the auth block | none beyond the alias decision | W-sf-1 re-record on the release head, refresh the next-step text (S); claim table rows | S |
| `deploy-notebook` | `DOMAIN`, `--workspace -s --seed --notebook-name` and the auth block | none | W-dn-1 re-record; use a unique `--notebook-name` in the example or keep the pre-clean (S) | S |
| `notebook` | `DOMAIN`, `-s --seed -o --target {lakehouse,display,csv}` | none | claim table (local output only) | S |
| `known-answer` | `-s --seed --measures --plant -o` (required); files `shape-dax-answers` v1 and `shape-dax-measures` v1 | none | claim table; confirm the two file formats are versioned and pinned by a test | S |
| `check-answers` | `ANSWERS RESULTS`, `--places` | none | claim table | S |
| `export-model` | `DOMAIN`, `-s --source-type {lakehouse,warehouse,sql_database} --source-name -o --include-measures --no-measures --schema-name -m {3nf,star}` | `--source-type sql_database` uses an underscore; `publish -t` uses `sql-database`. Accept the hyphen as an alias now; both then freeze | W-em-1 alias and test (S); claim table; fence the XMLA-deploy claim (no live run) | M |
| `publish` | `DOMAIN\|SCHEMA.json`, `-s --seed -m {3nf,star} -t {lakehouse,eventhouse,sql-database,warehouse} --workspace-id --lakehouse-id --base-path --staging-path --database --format {parquet,csv,jsonl,delta} --credential --write-mode {create,append,truncate,replace} --batch-size --schema-name --dry-run --connection-string` and the auth block. Run manifest keys. | (a) `--base-path` and `--staging-path` overlap for a warehouse ("instead of"): keep both, document precedence. (b) `--format delta` works for local folders only: say so in the choices help or refuse early. (c) `--write-mode create` default makes example 0 not re-runnable: put re-runnable flags in the examples. (d) auth values `--client-secret --sql-password --keytab` take references (good). | W-pub-1 split example 0 into four examples (S); W-pub-2 claim table for four targets, about 30 rows (M); W-pub-3 surface edits (S); re-record | M |
| `profile-db` | `--connection-string --server --database --auth {sql,cli,msi,spn,fabric} --tenant-id --client-id --client-secret --schema --tables --sample-rows -o --json`; `--json` keys | (a) `--client-secret` takes a plain value; decision 13: it takes `env://`, `file://` or `kv://` references only and refuses a literal, like `publish`. (b) `--auth` choices differ from `publish` (no `device-code`, no `kerberos`): decide whether to add them (additive) or document the difference. (c) list the `--json` keys in a test (the transcript shows `written` and `shape_content_id`; I did not list the rest). | W-pdb-1 reference-only `--client-secret`: refuse a literal, with a test and a changelog line (S); W-pdb-2 pin the `--json` keys (S); re-record (S); fence the Azure SQL claim | M |

Exit classes of each plugin command must be one of 0, 1, 2 or a documented verdict. W-plug adds a test that runs each promoted command on a bad input and checks for exit 2 and a one-line `shape: error:` message, as `tests/regressions/test_iss_cli_errors.py` does for core commands.

## 5. Risks and open questions for the Owner

Status after the Owner's answers of 2026-10-13: **Accepted** means as recommended. **Reversed** means the Owner chose the other way. Decisions 6 to 16 are Owner-confirmed ("6–16 yes").

1. **Legacy labels (ratchet or relabel).** *Accepted.* 71 pages say available with no proof; 15 mention experimental commands; `DICTIONARY.md` and `PROJECT.md` are plain mislabels. Freeze the list in `tests/docs/available_legacy.txt`, let it only shrink, relabel nothing silently in 0.9.3. Move `DICTIONARY.md` and `PROJECT.md` to the front of the 0.9.4 queue.
2. **Plugin commands cannot be stable.** *Reversed: they get in 0.9.3.* My advice was to promote none and to reach Fabric through `generate`. The risk I named stays: the plugin's release cycle is now inside a core promise. Section 6 limits it: only first-party plugins can be listed, and the promise holds when the plugin is installed at the same release as the core. Nine plugin commands are promoted (section 7).
3. **Evidence freshness.** *Accepted.* A receipt counts when it is from the same release line or the source is unchanged. Re-record Fabric on the release head (4.9). See 1.2 for how I read "same minor" for a 0.x release.
4. **What is a "real" run.** *Accepted.* Self-hosted containers (PostgreSQL, MySQL, SQL Server, Kafka) count; cloud emulators do not.
5. **Mixed pages: fence or split.** *Accepted.* In-page fences for VERIFY, SIGNING, fabric-writers, fabric-commands and sqlserver; one real split, `CLI.md`. The fence markers must render as a visible warning box.
6. **Python API stance.** *Accepted.* "Available" on a page covers its CLI and documented behaviour. Python parts stay fenced as experimental until `API_STABILITY.md` names them.
7. **Surface edits before the freeze.** *Accepted.* `gameday` dry-run document; `mask` output-folder rule and `--seed` with a key; `share-bundle` key flag and `--json` (0.9.4); and now the plugin edits in 4.11.
8. **`rules` mutant kinds.** *Accepted.* Declare the list extensible in the page and the schema; `rules` stays in 0.9.4.
9. **Release note convention.** *Accepted.* A `### Promoted` heading in each release section of `CHANGELOG.md`; one line per page or command.
10. **Remove or keep `privacy-k` and `quality`.** *Accepted.* Remove in 0.10, with a note in 0.9.3 `### Changed` (work package W-rm, S).
11. **Cost and access of live runs.** *Accepted.* The lead dispatches one batch on the release head, after the Owner confirms the workspace is clean (the pre-clean step records what it deleted).
12. **Size of the claim tables.** *Accepted.* About 25 to 40 rows per page, by hand.
13. **Secrets on the command line.** *Accepted.* `profile-db --client-secret` takes a plain value. Once frozen, a plain-value flag stays for all of 1.x. Decided: before the freeze `profile-db --client-secret` accepts `env://`, `file://` and `kv://` references only and refuses a literal, as `--source-config` does. `generate`, `publish`, `deploy-notebook` and `setup-fabric` already take `--client-secret REF`, so only `profile-db` differs.
14. **Two spellings of each Fabric command.** *Accepted.* `shape publish` and `shape fabric publish` both work. Recommend freezing both; list them as one row each in `CLI_STABILITY.md`.
15. **Reading of "same minor version".** *Accepted.* See 1.2. Recommend "same release, or unchanged source", because 0.9.2 changed the writers.
16. **Plugin version coupling.** *Accepted.* `scripts/check_versions.py` already enforces lockstep: every first-party plugin has the core's version and pins first-party requirements with `==`. Recommend stating in `CLI_STABILITY.md` that a stable plugin command is promised with a plugin of the same release as the core. A user who forces a mismatch with `pip --no-deps` is outside the promise.

## 6. Plugin command stability (W-plug)

Goal: let a first-party plugin command be listed as stable, freeze its surface the way a core command is frozen, and mark every other plugin command experimental in `--help`. Size **M** (3 to 4 days), plus the per-command review in 4.11.

### 6.1 What exists now

- `src/shape/cli/stability.py`: `STABLE` holds core names; `level()` reads it; `annotate()` prefixes `(experimental)` on core commands only.
- `src/shape/cli/main.py`: a plugin command shows as a stub, `(plugin <distribution>)`, in `shape --help`. Its flags exist only after the plugin loads, in `shape.plugins.cli.run_command`.
- `scripts/cli_surface.py`: `snapshot()` builds the core parser only, so plugin commands are outside `tests/cli/cli_surface_v1.json` (stated in its docstring). Adding a plugin name to `STABLE` fails `tests/cli/test_cli_surface.py::test_every_stable_command_exists_in_the_parser`.
- `docs/plugins/stability.md` freezes the `Command` Protocol (`name`, `help`, `configure`, `run`), the entry-point group `shape.commands` and the loader. It does not freeze the flags `configure` adds, exit codes or output. `docs/CLI_STABILITY.md` sends plugin commands to that page.

### 6.2 Design

1. **The list.** In `stability.py` add `STABLE_PLUGIN_COMMANDS: dict[str, str]`, command name to owning distribution (`"publish": "sqllocks-shape-fabric"`). `level(name)` returns `stable` for a name in either set. The list lives in core, so a plugin cannot promote itself, and a third-party plugin cannot be listed (the loader refuses a listed name from another distribution).
2. **The snapshot, per distribution.** In `cli_surface.py` add `snapshot_plugins()`. For each listed command it loads the entry point through `PluginHost` (group `shape.commands`), builds a throw-away `argparse` sub-parser, calls `configure(parser)` and runs the existing `_node()`. The result goes to a second baseline, `tests/cli/cli_surface_plugins_v1.json`, with the same shape as the core baseline plus a top-level key per distribution. `--check` and `--write` get a `--plugins` option, and `make check` runs both. `compare()` is reused, so removing or renaming a flag, a choice or a positional of a listed command fails. In CI the first-party plugins are installed from source; a missing plugin is a failure, not a skip.
3. **The help marker.** `annotate()` is extended: a plugin stub in `shape --help` reads `(plugin <distribution>, experimental)` unless listed. Inside `run_command`, the plugin parser's description gets the `(experimental)` prefix unless the name is listed. A listed command shows no marker, as a stable core command shows none. A test mirrors `test_every_experimental_command_says_so_in_its_help`.
4. **Exit codes.** A listed command returns 0, 1, 2 or a documented verdict class. W-plug adds a test per listed command (a bad input gives exit 2 and one `shape: error:` line). `scripts/gen_exit_codes.py` gets rows for listed plugin commands, so `docs/EXIT_CODES.md` covers them.
5. **`--json` keys.** For listed commands that have `--json` (`profile-db` only, in 0.9.3), a test pins the key set, and `CLI_STABILITY.md` states that other output is not covered.
6. **Version coupling.** Decision 16: the promise holds when the plugin is the same release as the core. `scripts/check_versions.py` already enforces lockstep and `==` pins; W-plug adds only a test that each listed distribution is covered by it. If the plugin is missing, `shape publish` prints a one-line error naming the distribution to install (exit 2); a test covers it.
7. **P-BAR check.** Rule 1 reads `STABLE | set(STABLE_PLUGIN_COMMANDS)`; `Covers:` accepts plugin names.

### 6.3 How the documents cover it

- `docs/CLI_STABILITY.md`: add a table "Stable plugin commands" (name, distribution) next to the core table; state that the promise holds when the plugin and core are the same release; state that banners and output layout are not covered; add `STABLE_PLUGIN_COMMANDS` to the "promoted by" sentence; keep "a stable command is never demoted in 1.x" for both tables.
- `docs/plugins/stability.md`: add a section "Stable plugin commands". The plugin-API promise (the Protocol members) does not change, so no API version bump and no change to `tests/plugins/api_v1_baseline.json`. The flags of a **listed first-party** command are frozen by the CLI snapshot, not by the Protocol. A third-party plugin's commands stay under the plugin promise only. The `check_command` kit check is **not** tightened for all plugins (tightening the kit so a passing 1.0 plugin fails is breaking, per that page); the exit-class test applies to listed commands only.
- `docs/plugins/api-v1.md` is generated and needs no change; `gen_plugin_api_docs.py` writes `Status: experimental` there and stays so.
- `tests/plugins/test_stability_promise.py`: add a test that the promise page names `STABLE_PLUGIN_COMMANDS`.

### 6.4 Work packages

| WP | Content | Size |
|---|---|---|
| W-plug-1 | `STABLE_PLUGIN_COMMANDS`, `level()`, loader refusal for unlisted distributions | S |
| W-plug-2 | `snapshot_plugins()`, second baseline, `--plugins`, `make check` and CI wiring | M |
| W-plug-3 | Help markers (stubs and `run_command`), tests | S |
| W-plug-4 | Exit-class test per listed command, `gen_exit_codes.py` rows | S |
| W-plug-5 | Test that listed distributions are inside the lockstep check; missing-plugin error | S |
| W-plug-6 | Docs: `CLI_STABILITY.md`, `plugins/stability.md`, test of the promise page | S |
| Total | | M to L (about 5 days if done as listed; the S items overlap) |

I count W-plug at 5 days in the totals, above the 3 to 4 I first gave, because the exit-class tests (W-plug-4) and the help-marker tests are real work.

## 7. The 22 plugin commands against P-BAR

Each row applies the five parts of the bar after W-plug: (1) command listed; (2) claims map to tests; (3) outside-service runs; (4) owner notes; (5) release note. Rows (4) and (5) are the same for all: no open notes after the Fabric branch lands; the `### Promoted` line is a task for every promoted command.

### 7.1 Verdicts

| Command | Distribution | Page | Outside service | Recorded run | Verdict | Work to promote (size) |
|---|---|---|---|---|---|---|
| `setup-fabric` | fabric | `plugins/fabric-commands.md` | Fabric | 38337418136 passed | **promote** | re-record, claim rows (S) |
| `deploy-notebook` | fabric | same | Fabric | 38400931371 passed (`B/`) | **promote** | re-record (S) |
| `publish` | fabric | same | Fabric (4 targets) | 38400931371 passed (`B/`) | **promote** | surface review, claim table, re-record (M) |
| `notebook` | fabric | same | none | n/a | **promote** | claim table (S) |
| `export-model` | fabric | same | none (XMLA claim fenced) | n/a | **promote** | alias, claim table (M) |
| `known-answer` | fabric | same | none | n/a | **promote** | claim table, format pin (S) |
| `check-answers` | fabric | same | none | n/a | **promote** | claim table (S) |
| `fabric` (group) | fabric | same | none by itself | n/a | **promote** (alias form of the seven above and `publish-report`) | included |
| `profile-db` | sqlserver | `plugins/sqlserver.md` | SQL Server, Fabric Warehouse | 38337418136 passed; `O/sqlserver-live.md` (0.9.1); `E/kerberos-live.md` | **promote** | secret reference, `--json` pin, re-record (M) |
| `profile-model` | fabric | section of `fabric-commands.md` | Fabric notebook | `E/semantic-model-notebook.md` (0.9.1; one table, no relationships) | later | relationship run (M) |
| `publish-report` | fabric | `DRIFT_REPORT.md` | none stated | none | later | claim table, example, decide the alias form (M) |
| `dbt-seeds` | dbt | `DBT.md` | dbt adapter | CI examples (DuckDB) | later | claim table for a 720-line page (L for the four) |
| `dbt-report` | dbt | `DBT.md` | none | CI examples | later | has `--json`; pin keys (with the four) |
| `from-dbt` | dbt | `DBT.md` | dbt | CI examples | later | with the four |
| `to-dbt-tests` | dbt | `DBT.md` | none | CI examples | later | with the four |
| `behave` | behavior | `plugins/behavior.md` | none | n/a | keep experimental | 631-line page; subcommands `run check import-gmf examples` not reviewed |
| `simulate` | simulation | `plugins/simulation.md` | none | n/a | keep experimental | 10 subcommands, unreviewed |
| `ctgan` | core distribution | none | none | n/a | keep experimental | no page; optional dependency |
| `evaluate` | integrations | `plugins/integrations.md` | none | n/a | keep experimental | subcommands `sdmetrics anonymeter` wrap third-party libraries |
| `lineage` | integrations | same | OpenLineage backend | none | keep experimental | needs a real run |
| `mlflow` | integrations | same | MLflow server | none | keep experimental | needs a real run |
| `healthcare-codes` | healthcare-codes | `plugins/healthcare-codes.md` | code-set downloads | none | keep experimental | licence of code sets to confirm; 5 test modules did not collect here |

Counts: 9 promote, 13 not (2 later for Fabric reasons, 4 later for dbt, 7 keep experimental).

### 7.2 Why these nine and not more

They are the commands for which a run exists on a Fabric or SQL service, or which touch no service at all, and whose page can be fenced to what is proven. `profile-model` has a receipt, but it proves the narrow case; promoting it would freeze a command whose main claim (relationships) is unproven. The dbt group is a page-size problem, not an evidence problem: four commands share one 720-line page, and a page is available only when every claim on it is mapped.

## 8. Effort

Person-days, one person who knows the code. Ranges reflect review findings.

| Block | Items | Days |
|---|---|---|
| W0: P-BAR check, `AVAILABLE.md`, ratchet list, `### Promoted` convention, tests | M | 3 |
| W-plug: mechanism (6.4) | M to L | 5 |
| Fabric commands: `setup-fabric` 1, `deploy-notebook` 1, `notebook` 0.5, `known-answer` and `check-answers` 1.5, `export-model` 2, `publish` 4, page claim table and stale-text fixes 4 | | 14 |
| `profile-db` and `sqlserver.md` | M | 3 |
| Synapse write and `fabric-writers.md` fences | M | 3 |
| Pages: REPRODUCIBILITY 1, DETERMINISM 1, QUICKSTART 1, SIGNING 3, VERIFY 3 | | 9 |
| Audit commands: `canary` 1, `gameday` 1.5, `mask` 3 | | 5.5 |
| `CLI.md` split (prep, no label) | M | 3 |
| W-rm: note in `### Changed` for `privacy-k`, `quality` | S | 0.5 |
| Fabric live batch on the release head (Owner time, mostly waiting) | | 2 |
| **Total** | | **about 48** (range 38 to 62) |

Critical path: W0 and W-plug (5 days, in parallel), then the Fabric block (about 14 days). With three people the elapsed time is about 4 weeks: one on W0, then pages and audit commands; one on W-plug, then Fabric commands; one on the page splits, `profile-db`, Synapse, and the live batch. With one person it is about 10 weeks. The least-risk cut if time runs short: drop `publish` (4 days plus its review) and ship the other eight plugin commands; the page `fabric-commands.md` then fences `publish` and stays unlabelled until 0.9.4.

## Appendix: facts to keep

- `scripts/check_docs_owners.py --release` runs only in `docs.yml`, not in `make check`.
- `tests/docs/test_launch_tutorials.py::test_tutorial_template` is the only test that asserts a `status:` value.
- `FAILURE_MODES.md` and `plugins/api-v1.md` are generated and write `Status: experimental`.
- Mixed labels already exist: `docs/MODELS.md` says `available (shipped model commands); experimental (...)`.
- Live `pytest -m live` results are not committed anywhere. Only the nine ids in `tests/docs/fabric_examples.json` can leave a committed receipt.
- Run ids 38312820840 and 38245654398 are cited in prose with no file in the repo.
- The Fabric receipts print `Shape v0.9.1` banners although the code is newer.
