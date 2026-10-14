# W-claims draft claim-to-proof tables

Internal preparation for 0.9.4; no page is promoted. The public ledger and checker remain unchanged under the Owner's resolution. Each section below preserves the exact public-ledger heading, since line and five-column table format. Execution evidence and remaining gaps are adjacent internal notes and must not be pasted into AVAILABLE.md.

Each claim is a one-sentence statement of documented behavior, with exact pytest node ids; parameter families are intentionally named without parameters, as AVAILABLE.md allows. The proof is limited to what the assertions cover. Passing a transcript proves its examples, not every nearby sentence. `done` means all named proofs passed in this lane; `open` means a proof did not pass or was not executed. Neither status is a page promotion. No outside service is exercised by these mapped claims; fake resolvers are local proof, not a real service receipt.

The page-specific promotion package must add matching claim anchors and Covers, finish fences or text moves, resolve the gaps below, supply its release note, then transfer the table into docs/AVAILABLE.md. The since value is the target release, not the current 0.9.2 base version.

## docs/CANARIES.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-CANARIES-01 | A canary contains a marked synthetic batch and a canary.json document. | `tests/scenario/test_canary.py::test_make_writes_the_marked_batch_and_the_canary_document` | none | done |
| AV-CANARIES-02 | Every row carries the marker in CSV, Parquet and JSONL output. | `tests/scenario/test_canary.py::test_every_row_carries_the_marker_in_every_format` | none | done |
| AV-CANARIES-03 | The rows, marker and seed options control the generated batch. | `tests/scenario/test_canary.py::test_rows_marker_and_seed_are_honoured` | none | done |
| AV-CANARIES-04 | A scenario touching several tables writes one file for each table. | `tests/scenario/test_canary.py::test_a_scenario_that_touches_several_tables_writes_each` | none | done |
| AV-CANARIES-05 | Expected detections come from the catalog for either a failure-mode id or its scenario. | `tests/scenario/test_canary.py::test_the_expected_detections_come_from_the_catalog_for_an_id_and_for_its_scenario` | none | done |
| AV-CANARIES-06 | A scenario without a catalog entry uses its answer key. | `tests/scenario/test_canary.py::test_a_scenario_without_a_catalog_entry_expects_what_its_answer_key_says` | none | done |
| AV-CANARIES-07 | A failure that changes over time is refused with exit 2. | `tests/scenario/test_canary.py::test_a_change_over_time_cannot_be_a_canary` | none | done |
| AV-CANARIES-08 | A failure mode that no check detects is refused with exit 2. | `tests/scenario/test_canary.py::test_a_failure_mode_that_nothing_detects_is_refused_and_says_why` | none | done |
| AV-CANARIES-09 | An expected check that cannot fire at the requested size is refused before files are written. | `tests/scenario/test_canary.py::test_a_check_that_does_not_fire_at_that_size_is_named_and_nothing_is_written` | none | done |
| AV-CANARIES-10 | Dry run lists proposed files and writes nothing. | `tests/scenario/test_canary.py::test_dry_run_lists_the_files_and_writes_nothing` | none | done |
| AV-CANARIES-11 | Canaries can only be written to a local directory. | `tests/scenario/test_canary.py::test_make_writes_only_to_a_local_directory` | none | done |
| AV-CANARIES-12 | Check exits 0 when every expected detection is present. | `tests/scenario/test_canary.py::test_every_expected_detection_present_exits_zero` | none | done |
| AV-CANARIES-13 | Check exits 1 and names a blind spot when an expected detection is missing. | `tests/scenario/test_canary.py::test_removing_one_result_exits_one_and_names_the_blind_spot` | none | done |
| AV-CANARIES-14 | The JSON check report lists present detections and blind spots. | `tests/scenario/test_canary.py::test_check_json_lists_present_and_blind_spots` | none | done |
| AV-CANARIES-15 | Catalog gate names match the names that verify reports. | `tests/scenario/test_canary.py::test_a_gate_counts_under_the_name_shape_verify_gives_it` | none | done |
| AV-CANARIES-16 | Any one alternative detection satisfies an expected alternative. | `tests/scenario/test_canary.py::test_alternatives_are_satisfied_by_any_one` | none | done |
| AV-CANARIES-17 | Malformed canary or result input makes check exit 2. | `tests/scenario/test_canary.py::test_malformed_input_to_check_exits_two` | none | done |
| AV-CANARIES-18 | Check refuses to run without result documents. | `tests/scenario/test_canary.py::test_check_without_any_document_is_refused` | none | done |
| AV-CANARIES-19 | Result documents from commands that do not describe checks are refused. | `tests/scenario/test_canary.py::test_a_result_that_is_not_usable_is_refused` | none | done |
| AV-CANARIES-20 | The documented local command examples run with their recorded exit codes and output. | `tests/docs/test_reference_examples.py::test_reference_page[CANARIES]` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-CANARIES-01 | `tests/scenario/test_canary.py::test_make_writes_the_marked_batch_and_the_canary_document` | 1 passed |
| AV-CANARIES-02 | `tests/scenario/test_canary.py::test_every_row_carries_the_marker_in_every_format` | 3 passed |
| AV-CANARIES-03 | `tests/scenario/test_canary.py::test_rows_marker_and_seed_are_honoured` | 1 passed |
| AV-CANARIES-04 | `tests/scenario/test_canary.py::test_a_scenario_that_touches_several_tables_writes_each` | 1 passed |
| AV-CANARIES-05 | `tests/scenario/test_canary.py::test_the_expected_detections_come_from_the_catalog_for_an_id_and_for_its_scenario` | 1 passed |
| AV-CANARIES-06 | `tests/scenario/test_canary.py::test_a_scenario_without_a_catalog_entry_expects_what_its_answer_key_says` | 1 passed |
| AV-CANARIES-07 | `tests/scenario/test_canary.py::test_a_change_over_time_cannot_be_a_canary` | 2 passed |
| AV-CANARIES-08 | `tests/scenario/test_canary.py::test_a_failure_mode_that_nothing_detects_is_refused_and_says_why` | 1 passed |
| AV-CANARIES-09 | `tests/scenario/test_canary.py::test_a_check_that_does_not_fire_at_that_size_is_named_and_nothing_is_written` | 1 passed |
| AV-CANARIES-10 | `tests/scenario/test_canary.py::test_dry_run_lists_the_files_and_writes_nothing` | 1 passed |
| AV-CANARIES-11 | `tests/scenario/test_canary.py::test_make_writes_only_to_a_local_directory` | 1 passed |
| AV-CANARIES-12 | `tests/scenario/test_canary.py::test_every_expected_detection_present_exits_zero` | 1 passed |
| AV-CANARIES-13 | `tests/scenario/test_canary.py::test_removing_one_result_exits_one_and_names_the_blind_spot` | 1 passed |
| AV-CANARIES-14 | `tests/scenario/test_canary.py::test_check_json_lists_present_and_blind_spots` | 1 passed |
| AV-CANARIES-15 | `tests/scenario/test_canary.py::test_a_gate_counts_under_the_name_shape_verify_gives_it` | 1 passed |
| AV-CANARIES-16 | `tests/scenario/test_canary.py::test_alternatives_are_satisfied_by_any_one` | 1 passed |
| AV-CANARIES-17 | `tests/scenario/test_canary.py::test_malformed_input_to_check_exits_two` | 1 passed |
| AV-CANARIES-18 | `tests/scenario/test_canary.py::test_check_without_any_document_is_refused` | 1 passed |
| AV-CANARIES-19 | `tests/scenario/test_canary.py::test_a_result_that_is_not_usable_is_refused` | 7 passed |
| AV-CANARIES-20 | `tests/docs/test_reference_examples.py::test_reference_page[CANARIES]` | 1 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- If your checks do not flag it, your monitoring is blind: it would not notice the real thing either. — Operational inference; tests prove missing expected detections, not future real-pipeline detection.
- Send the files through the same input as real data, run your usual checks and canary check the results from the same job, and alert on exit 1. — Operational guidance; no real outside-pipeline run is supplied.
- Filter on the marker column everywhere real numbers are produced. — Downstream operational guidance, not behavior Shape enforces.

Transfer with: W-canary-3. Keep Python API examples experimental until API_STABILITY.md lists their surface.

## docs/REPRODUCIBILITY.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-REPRODUCIBILITY-01 | The reproducibility tuple records seven environment facts. | `tests/scenario/test_reproducibility.py::test_the_tuple_has_the_seven_fields` | none | done |
| AV-REPRODUCIBILITY-02 | A run records its reproducibility tuple and dataset id in its manifest. | `tests/scenario/test_reproducibility.py::test_a_run_records_the_tuple_and_the_dataset_id` | none | done |
| AV-REPRODUCIBILITY-03 | The manifest records generator versions and identifier mode alongside the tuple. | `tests/scenario/test_reproducibility.py::test_a_run_records_the_tuple_and_the_dataset_id` | none | done |
| AV-REPRODUCIBILITY-04 | The dataset id is sha256 followed by 64 hexadecimal digits. | `tests/scenario/test_reproducibility.py::test_the_id_is_a_prefixed_sha256_and_is_stable` | none | done |
| AV-REPRODUCIBILITY-05 | The dataset id ignores table, column and row order. | `tests/scenario/test_reproducibility.py::test_the_id_ignores_row_order_column_order_and_table_order` | none | done |
| AV-REPRODUCIBILITY-06 | The dataset id ignores table chunk boundaries. | `tests/scenario/test_reproducibility.py::test_the_id_ignores_how_the_table_is_chunked` | none | done |
| AV-REPRODUCIBILITY-07 | Changing content changes the dataset id. | `tests/scenario/test_reproducibility.py::test_the_id_changes_when_the_content_changes` | none | done |
| AV-REPRODUCIBILITY-08 | Duplicate rows count and null differs from NaN in the dataset id. | `tests/scenario/test_reproducibility.py::test_a_duplicate_row_counts_and_null_differs_from_nan` | none | done |
| AV-REPRODUCIBILITY-09 | Table names and empty tables contribute to the dataset id. | `tests/scenario/test_reproducibility.py::test_table_names_and_empty_tables_count` | none | done |
| AV-REPRODUCIBILITY-10 | Swapping values between columns changes the dataset id. | `tests/scenario/test_reproducibility.py::test_swapping_values_between_columns_changes_the_id` | none | done |
| AV-REPRODUCIBILITY-11 | String and large_string are the same type for the dataset id. | `tests/scenario/test_reproducibility.py::test_string_and_large_string_are_one_type_for_the_id` | none | done |
| AV-REPRODUCIBILITY-12 | Both kernels produce the same dataset id. | `tests/scenario/test_reproducibility.py::test_the_id_is_independent_of_the_kernel` | none | done |
| AV-REPRODUCIBILITY-13 | The dataset id follows the seed rather than the clock. | `tests/scenario/test_reproducibility.py::test_the_dataset_id_follows_the_seed_not_the_clock` | none | done |
| AV-REPRODUCIBILITY-14 | Legacy manifests still load without reproducibility fields or dataset ids. | `tests/scenario/test_reproducibility.py::test_a_manifest_from_before_this_change_still_loads` | none | done |
| AV-REPRODUCIBILITY-15 | Newer unsupported manifest versions are refused with an upgrade message. | `tests/scenario/test_reproducibility.py::test_a_manifest_from_a_newer_shape_is_refused_with_a_clear_message` | none | done |
| AV-REPRODUCIBILITY-16 | Replay regenerates the dataset and checks its recorded id. | `tests/scenario/test_reproducibility.py::test_replay_regenerates_the_dataset_and_checks_the_id` | none | done |
| AV-REPRODUCIBILITY-17 | Replay prints a structured JSON report when requested. | `tests/scenario/test_reproducibility.py::test_replay_json` | none | done |
| AV-REPRODUCIBILITY-18 | Replay exits 1 and names both ids when they differ. | `tests/scenario/test_reproducibility.py::test_replay_of_a_changed_dataset_id_is_exit_1_and_names_both_ids` | none | done |
| AV-REPRODUCIBILITY-19 | Replay uses the seed recorded in the manifest. | `tests/scenario/test_reproducibility.py::test_replay_uses_the_seed_in_the_manifest` | none | done |
| AV-REPRODUCIBILITY-20 | Environment differences do not fail replay when the ids match. | `tests/scenario/test_reproducibility.py::test_replay_reports_environment_differences_without_failing_a_match` | none | done |
| AV-REPRODUCIBILITY-21 | Replay refuses missing ids and mismatched pack or spec inputs with exit 2. | `tests/scenario/test_reproducibility.py::test_replay_refuses_what_it_cannot_check_with_exit_2` | none | done |
| AV-REPRODUCIBILITY-22 | Replay of a spec run checks the spec hash. | `tests/scenario/test_reproducibility.py::test_replay_of_a_run_with_a_spec_checks_the_spec_hash` | none | done |
| AV-REPRODUCIBILITY-23 | The dataset id includes tables after chaos is applied. | `tests/scenario/test_reproducibility.py::test_a_run_with_chaos_replays_to_the_same_id` | none | done |
| AV-REPRODUCIBILITY-24 | Every committed pinned generation spec retains its expected dataset id. | `tests/generation/test_w1_15_pinned_fixtures.py::test_the_dataset_id_of_every_pinned_spec_is_unchanged` | none | done |
| AV-REPRODUCIBILITY-25 | The pinned fixtures give the same ids across both kernels. | `tests/generation/test_w1_15_pinned_fixtures.py::test_the_kernels_agree_on_every_id` | none | done |
| AV-REPRODUCIBILITY-26 | The documented local command examples run with their recorded exit codes and output. | `tests/docs/test_reference_examples.py::test_reference_page[REPRODUCIBILITY]` | none | done |
| AV-REPRODUCIBILITY-27 | Runs record each used generator version. | `tests/scenario/test_w1_15_replay.py::test_a_run_records_the_version_of_every_generator_it_used` | none | done |
| AV-REPRODUCIBILITY-28 | Replay uses recorded generator versions over the spec pins. | `tests/scenario/test_w1_15_replay.py::test_replay_uses_the_recorded_versions_over_the_specs_pins` | none | done |
| AV-REPRODUCIBILITY-29 | A legacy manifest without generator versions still loads. | `tests/scenario/test_w1_15_replay.py::test_a_manifest_without_generators_still_loads` | none | done |
| AV-REPRODUCIBILITY-30 | A legacy manifest without generator versions replays with the latest versions. | `tests/scenario/test_w1_15_replay.py::test_a_manifest_without_generators_replays_with_the_latest_versions` | none | done |
| AV-REPRODUCIBILITY-31 | Replay exits 2 for an unavailable recorded generator version. | `tests/scenario/test_w1_15_replay.py::test_replay_of_a_recorded_version_this_shape_lacks_exits_2` | none | done |
| AV-REPRODUCIBILITY-32 | File hashes report the run files and exclude the run manifest. | `tests/generation/test_w8_04_byte_identity.py::test_file_hashes_of_two_runs_match_and_leave_out_the_manifest` | none | done |
| AV-REPRODUCIBILITY-33 | File hashes use relative paths and SHA256 hashes. | `tests/generation/test_w8_04_byte_identity.py::test_file_hashes_boundaries` | none | done |
| AV-REPRODUCIBILITY-34 | Replay regenerates with the recorded identifier mode. | `tests/cli/test_w8_06_identifiers_cli.py::test_a_replay_regenerates_with_the_recorded_mode` | none | done |
| AV-REPRODUCIBILITY-35 | Legacy manifests without an identifier mode read and replay as reserved. | `tests/cli/test_w8_06_identifiers_cli.py::test_a_manifest_without_the_field_reads_and_replays_as_reserved` | none | done |
| AV-REPRODUCIBILITY-36 | Unknown identifier modes cannot be replayed. | `tests/cli/test_w8_06_identifiers_cli.py::test_a_manifest_with_an_unknown_mode_cannot_be_replayed` | none | done |
| AV-REPRODUCIBILITY-37 | Run manifests record byte-stable writer format versions. | `tests/generation/test_w8_04_byte_identity.py::test_the_run_manifest_records_the_writer_versions` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-REPRODUCIBILITY-01 | `tests/scenario/test_reproducibility.py::test_the_tuple_has_the_seven_fields` | 1 passed |
| AV-REPRODUCIBILITY-02 | `tests/scenario/test_reproducibility.py::test_a_run_records_the_tuple_and_the_dataset_id` | 1 passed |
| AV-REPRODUCIBILITY-03 | `tests/scenario/test_reproducibility.py::test_a_run_records_the_tuple_and_the_dataset_id` | 1 passed |
| AV-REPRODUCIBILITY-04 | `tests/scenario/test_reproducibility.py::test_the_id_is_a_prefixed_sha256_and_is_stable` | 1 passed |
| AV-REPRODUCIBILITY-05 | `tests/scenario/test_reproducibility.py::test_the_id_ignores_row_order_column_order_and_table_order` | 1 passed |
| AV-REPRODUCIBILITY-06 | `tests/scenario/test_reproducibility.py::test_the_id_ignores_how_the_table_is_chunked` | 1 passed |
| AV-REPRODUCIBILITY-07 | `tests/scenario/test_reproducibility.py::test_the_id_changes_when_the_content_changes` | 8 passed |
| AV-REPRODUCIBILITY-08 | `tests/scenario/test_reproducibility.py::test_a_duplicate_row_counts_and_null_differs_from_nan` | 1 passed |
| AV-REPRODUCIBILITY-09 | `tests/scenario/test_reproducibility.py::test_table_names_and_empty_tables_count` | 1 passed |
| AV-REPRODUCIBILITY-10 | `tests/scenario/test_reproducibility.py::test_swapping_values_between_columns_changes_the_id` | 1 passed |
| AV-REPRODUCIBILITY-11 | `tests/scenario/test_reproducibility.py::test_string_and_large_string_are_one_type_for_the_id` | 1 passed |
| AV-REPRODUCIBILITY-12 | `tests/scenario/test_reproducibility.py::test_the_id_is_independent_of_the_kernel` | 1 passed |
| AV-REPRODUCIBILITY-13 | `tests/scenario/test_reproducibility.py::test_the_dataset_id_follows_the_seed_not_the_clock` | 1 passed |
| AV-REPRODUCIBILITY-14 | `tests/scenario/test_reproducibility.py::test_a_manifest_from_before_this_change_still_loads` | 1 passed |
| AV-REPRODUCIBILITY-15 | `tests/scenario/test_reproducibility.py::test_a_manifest_from_a_newer_shape_is_refused_with_a_clear_message` | 1 passed |
| AV-REPRODUCIBILITY-16 | `tests/scenario/test_reproducibility.py::test_replay_regenerates_the_dataset_and_checks_the_id` | 1 passed |
| AV-REPRODUCIBILITY-17 | `tests/scenario/test_reproducibility.py::test_replay_json` | 1 passed |
| AV-REPRODUCIBILITY-18 | `tests/scenario/test_reproducibility.py::test_replay_of_a_changed_dataset_id_is_exit_1_and_names_both_ids` | 1 passed |
| AV-REPRODUCIBILITY-19 | `tests/scenario/test_reproducibility.py::test_replay_uses_the_seed_in_the_manifest` | 1 passed |
| AV-REPRODUCIBILITY-20 | `tests/scenario/test_reproducibility.py::test_replay_reports_environment_differences_without_failing_a_match` | 1 passed |
| AV-REPRODUCIBILITY-21 | `tests/scenario/test_reproducibility.py::test_replay_refuses_what_it_cannot_check_with_exit_2` | 1 passed |
| AV-REPRODUCIBILITY-22 | `tests/scenario/test_reproducibility.py::test_replay_of_a_run_with_a_spec_checks_the_spec_hash` | 1 passed |
| AV-REPRODUCIBILITY-23 | `tests/scenario/test_reproducibility.py::test_a_run_with_chaos_replays_to_the_same_id` | 1 passed |
| AV-REPRODUCIBILITY-24 | `tests/generation/test_w1_15_pinned_fixtures.py::test_the_dataset_id_of_every_pinned_spec_is_unchanged` | 104 passed |
| AV-REPRODUCIBILITY-25 | `tests/generation/test_w1_15_pinned_fixtures.py::test_the_kernels_agree_on_every_id` | 1 passed |
| AV-REPRODUCIBILITY-26 | `tests/docs/test_reference_examples.py::test_reference_page[REPRODUCIBILITY]` | 1 passed |
| AV-REPRODUCIBILITY-27 | `tests/scenario/test_w1_15_replay.py::test_a_run_records_the_version_of_every_generator_it_used` | 1 passed |
| AV-REPRODUCIBILITY-28 | `tests/scenario/test_w1_15_replay.py::test_replay_uses_the_recorded_versions_over_the_specs_pins` | 1 passed |
| AV-REPRODUCIBILITY-29 | `tests/scenario/test_w1_15_replay.py::test_a_manifest_without_generators_still_loads` | 1 passed |
| AV-REPRODUCIBILITY-30 | `tests/scenario/test_w1_15_replay.py::test_a_manifest_without_generators_replays_with_the_latest_versions` | 1 passed |
| AV-REPRODUCIBILITY-31 | `tests/scenario/test_w1_15_replay.py::test_replay_of_a_recorded_version_this_shape_lacks_exits_2` | 1 passed |
| AV-REPRODUCIBILITY-32 | `tests/generation/test_w8_04_byte_identity.py::test_file_hashes_of_two_runs_match_and_leave_out_the_manifest` | 1 passed |
| AV-REPRODUCIBILITY-33 | `tests/generation/test_w8_04_byte_identity.py::test_file_hashes_boundaries` | 1 passed |
| AV-REPRODUCIBILITY-34 | `tests/cli/test_w8_06_identifiers_cli.py::test_a_replay_regenerates_with_the_recorded_mode` | 2 passed |
| AV-REPRODUCIBILITY-35 | `tests/cli/test_w8_06_identifiers_cli.py::test_a_manifest_without_the_field_reads_and_replays_as_reserved` | 1 passed |
| AV-REPRODUCIBILITY-36 | `tests/cli/test_w8_06_identifiers_cli.py::test_a_manifest_with_an_unknown_mode_cannot_be_replayed` | 1 passed |
| AV-REPRODUCIBILITY-37 | `tests/generation/test_w8_04_byte_identity.py::test_the_run_manifest_records_the_writer_versions` | 2 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- Equal ids mean equal content. — Tests cover sampled changes; a universal absence of SHA256 collisions is not a testable absolute guarantee.
- The id does not depend on the kernel or on the platform. — Kernel independence has a row above; cross-platform equivalence requires CI across platforms, not this Linux run.
- Each column is hashed value by value with Shape's canonical value hash (the same for both kernels, never Python's hash()). — Behavior tests cover id invariants but do not pin each internal algorithm step.
- Lists and structs are hashed as their JSON text (sorted keys); dictionary columns are read as their values. — Common-type acceptance does not independently prove this exact encoding algorithm.
- The hash of a cell is mixed (splitmix64) with a salt taken from the column's name and type, and the mixed cells of a row are added modulo 2**64. — No proving test for the stated arithmetic construction was identified in the requested files.
- It is done with two hash seeds, so a row digest is 128 bits. — No explicit internal row-digest width assertion identified.
- The row digests are sorted and hashed with SHA-256 together with the row count. — Order/count invariants are tested, but not the exact internal digest construction.
- The SHA-256 of a JSON document with sorted keys and no spaces: the id format version (1) and, for each table name, the row count, the sorted list of column:type pairs and the table digest. — Public id shape and stability tests do not prove this exact serialization recipe.
- Types are Arrow type names; large_string reads as string, large_binary as binary, and a dictionary column as its value type. — large_string is proven above; large_binary and dictionary equivalence require dedicated assertions.
- What does not: 0.0 versus -0.0 (the hash reads them as equal), and the order of anything. — Ordering is proven above; no signed-zero equivalence assertion identified in requested tests.
- The CSV, JSON Lines and SQL files are byte-identical for the same spec, seed, scale, writer options and Shape version on every platform. — Selected file-hash tests prove same-environment CSV equality; the every-platform/all-writer promise needs the full byte-identity CI matrix.
- See GENERATION_STABILITY.md for the promise that a pinned spec gives the same dataset id in every 1.x release. — Pinned corpus checks prove the current build against fixtures, not all future releases.

Transfer with: W-repro-2 / W-repro-3. Keep Python API examples experimental until API_STABILITY.md lists their surface.

## docs/DETERMINISM.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-DETERMINISM-01 | Stateless generation strategies identify the same row independently of partition boundaries or execution order. | `tests/generation/test_random_access.py::test_random_access_partition_determinism` | none | done |
| AV-DETERMINISM-02 | FirstPerParent answers for a row never depend on which rows were requested before. | `tests/generation/test_first_per_parent_g2.py::test_row_at_in_any_order_gives_the_same_rows`<br>`tests/generation/test_first_per_parent_g2.py::test_rows_at_matches_row_at_for_any_index_order`<br>`tests/generation/test_first_per_parent_g2.py::test_rows_matches_row_at`<br>`tests/generation/test_first_per_parent_g2.py::test_first_per_parent_inside_a_conditional` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-DETERMINISM-01 | `tests/generation/test_random_access.py::test_random_access_partition_determinism` | 1 passed |
| AV-DETERMINISM-02 | `tests/generation/test_first_per_parent_g2.py::test_row_at_in_any_order_gives_the_same_rows` | 1 passed |
| AV-DETERMINISM-02 | `tests/generation/test_first_per_parent_g2.py::test_rows_at_matches_row_at_for_any_index_order` | 1 passed |
| AV-DETERMINISM-02 | `tests/generation/test_first_per_parent_g2.py::test_rows_matches_row_at` | 1 passed |
| AV-DETERMINISM-02 | `tests/generation/test_first_per_parent_g2.py::test_first_per_parent_inside_a_conditional` | 1 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- The cost is a scan of rows 0..i the first time a plan answers for row i, which later calls reuse. — Requested FirstPerParent tests prove answers, not scan cost or cache reuse; W-det-2 must cut or test this.
- Streaming replay is checkpoint-deterministic for deterministic handlers/sources. — Requested generation/reproducibility tests do not test streaming checkpoints; W-det-2 moves this to EMIT.md.
- External side effects require an idempotent/transactional sink to obtain stronger delivery guarantees. — Operational advice without a sink guarantee test; W-det-2 must cut or test it.
- The generation engine's contract (row-addressed random streams, chunk-independent output) is in GENERATION_ENGINE.md. — The linked experimental page has broader claims than this draft proves; W-det-3 must decide the link.

Transfer with: W-det-2 / W-det-3 and its promotion package. Keep Python API examples experimental until API_STABILITY.md lists their surface.

## docs/QUICKSTART.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-QUICKSTART-01 | The first-profile tutorial creates and profiles retail rows and saves an artifact. | `tests/docs/test_launch_tutorials.py::test_tutorial[01-first-profile]` | none | done |
| AV-QUICKSTART-02 | The contract tutorial checks uniqueness, non-null and nonnegative rules. | `tests/docs/test_launch_tutorials.py::test_tutorial[04-contract]` | none | done |
| AV-QUICKSTART-03 | The drift tutorial profiles a changed input and compares its artifacts. | `tests/docs/test_launch_tutorials.py::test_tutorial[03-drift]` | none | done |
| AV-QUICKSTART-04 | The generation tutorial generates data from a profile with explicit output options. | `tests/docs/test_launch_tutorials.py::test_tutorial[05-generate-profile]` | none | done |
| AV-QUICKSTART-05 | The report tutorial renders a report that can be read locally. | `tests/docs/test_launch_tutorials.py::test_tutorial[02-read-report]` | none | done |
| AV-QUICKSTART-06 | The domain tutorial generates a domain into DuckDB. | `tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]` | none | done |
| AV-QUICKSTART-07 | The documented local command examples run with their recorded exit codes and output. | `tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-QUICKSTART-01 | `tests/docs/test_launch_tutorials.py::test_tutorial[01-first-profile]` | 1 passed |
| AV-QUICKSTART-02 | `tests/docs/test_launch_tutorials.py::test_tutorial[04-contract]` | 1 passed |
| AV-QUICKSTART-03 | `tests/docs/test_launch_tutorials.py::test_tutorial[03-drift]` | 1 passed |
| AV-QUICKSTART-04 | `tests/docs/test_launch_tutorials.py::test_tutorial[05-generate-profile]` | 1 passed |
| AV-QUICKSTART-05 | `tests/docs/test_launch_tutorials.py::test_tutorial[02-read-report]` | 1 passed |
| AV-QUICKSTART-06 | `tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]` | 1 passed |
| AV-QUICKSTART-07 | `tests/docs/test_reference_examples.py::test_reference_page[QUICKSTART]` | 1 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- Generation from a profile is available and is being hardened. — Release-policy/status assertion; tutorial proves only its concrete generation example.
- Inspect the rows instead of assuming every property of the original data is reproduced. — Reader guidance; no promise of universal fidelity is inferred.
- Review a change before accepting a baseline. — Reader workflow guidance; no automatic review enforcement is claimed.

Transfer with: W-qs-1 / W-qs-2 and its promotion package. Keep Python API examples experimental until API_STABILITY.md lists their surface.

## docs/SIGNING.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-SIGNING-01 | A signed artifact verifies under its trusted key. | `tests/artifact/test_signing.py::test_sign_then_verify_round_trip` | none | done |
| AV-SIGNING-02 | An unsigned artifact fails verification. | `tests/artifact/test_signing.py::test_unsigned_artifact_fails_verify` | none | done |
| AV-SIGNING-03 | An artifact signed by another key fails verification. | `tests/artifact/test_signing.py::test_wrong_key_fails` | none | done |
| AV-SIGNING-04 | Rewriting component hashes cannot make a tampered signed artifact verify. | `tests/artifact/test_signing.py::test_forged_artifact_with_rewritten_hashes_fails_verify` | none | done |
| AV-SIGNING-05 | Stripping the signature fails verification under a trusted key. | `tests/artifact/test_signing.py::test_forged_and_stripped_signature_fails` | none | done |
| AV-SIGNING-06 | An attacker signing with a different key cannot satisfy the trusted key. | `tests/artifact/test_signing.py::test_attacker_resigns_with_own_key_fails_trusted_key` | none | done |
| AV-SIGNING-07 | A signature is not valid for another message. | `tests/artifact/test_signing.py::test_signature_is_not_valid_for_another_message` | none | done |
| AV-SIGNING-08 | Re-signing replaces the previous signature. | `tests/artifact/test_signing.py::test_resign_replaces_signature` | none | done |
| AV-SIGNING-09 | Signing to another output leaves the source unchanged. | `tests/artifact/test_signing.py::test_sign_to_output_leaves_source_untouched` | none | done |
| AV-SIGNING-10 | The CLI sign and verify flags enforce signature verification. | `tests/artifact/test_signing.py::test_cli_sign_verify_flags` | none | done |
| AV-SIGNING-11 | The signed message is a fixed domain prefix followed by exact manifest bytes. | `tests/artifact/test_signing_keys.py::test_signing_input_is_domain_plus_exact_manifest_bytes` | none | done |
| AV-SIGNING-12 | Private keys can come from stdin, environment, files or registered secret resolvers. | `tests/artifact/test_signing_keys.py::test_private_key_from_stdin_env_file_and_kv` | none | done |
| AV-SIGNING-13 | The CLI can sign with a key from environment, stdin or a file. | `tests/artifact/test_signing_keys.py::test_cli_sign_with_key_from_env_stdin_file` | none | done |
| AV-SIGNING-14 | Public verification keys can come from environment references. | `tests/artifact/test_signing_keys.py::test_cli_verify_key_from_env` | none | done |
| AV-SIGNING-15 | A secret-store reference requires a registered resolver. | `tests/artifact/test_signing_keys.py::test_kv_needs_a_registered_resolver_and_takes_a_fake` | none | done |
| AV-SIGNING-16 | Resolver failures do not expose the secret value. | `tests/artifact/test_signing_keys.py::test_resolver_failure_text_is_not_passed_on` | none | done |
| AV-SIGNING-17 | Core signing does not import a cloud SDK. | `tests/artifact/test_signing_keys.py::test_core_does_not_import_a_cloud_sdk` | none | done |
| AV-SIGNING-18 | Keygen writes an encrypted PKCS8 private key by default. | `tests/artifact/test_signing_keys.py::test_keygen_writes_an_encrypted_pkcs8_key_by_default` | none | done |
| AV-SIGNING-19 | Encrypted keys use standard PKCS8. | `tests/artifact/test_signing_keys.py::test_encrypted_key_is_standard_pkcs8` | none | done |
| AV-SIGNING-20 | Wrong or missing passphrases fail closed. | `tests/artifact/test_signing_keys.py::test_wrong_or_missing_passphrase_fails_closed` | none | done |
| AV-SIGNING-21 | Unencrypted key generation must be explicitly requested and warns. | `tests/artifact/test_signing_keys.py::test_unencrypted_key_must_be_asked_for_and_warns` | none | done |
| AV-SIGNING-22 | Keygen refuses to overwrite an existing key. | `tests/artifact/test_signing_keys.py::test_keygen_refuses_to_overwrite_with_a_friendly_error` | none | done |
| AV-SIGNING-23 | The key and passphrase cannot both come from standard input. | `tests/artifact/test_signing_keys.py::test_key_and_passphrase_cannot_both_use_stdin` | none | done |
| AV-SIGNING-24 | There is no command-line passphrase argument. | `tests/artifact/test_signing_keys.py::test_passphrase_has_no_command_line_form` | none | done |
| AV-SIGNING-25 | Passphrase sources are read in the documented order. | `tests/artifact/test_signing_keys.py::test_read_passphrase_order_and_prompt` | none | done |
| AV-SIGNING-26 | An unsigned read reports its unverified status. | `tests/artifact/test_signing_keys.py::test_unsigned_read_notices_and_reports_status` | none | done |
| AV-SIGNING-27 | A signed read without a trusted key reports the key id without verifying. | `tests/artifact/test_signing_keys.py::test_signed_but_unverified_read_notices_with_key_id` | none | done |
| AV-SIGNING-28 | Verified reads do not issue the unverified notice. | `tests/artifact/test_signing_keys.py::test_verified_read_has_no_notice` | none | done |
| AV-SIGNING-29 | The CLI prints an unverified-read notice on stderr once. | `tests/artifact/test_signing_keys.py::test_cli_prints_the_notice_on_stderr_once` | none | done |
| AV-SIGNING-30 | Cat with a trusted key verifies the signature and prints no unverified note. | `tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_checks_the_signature_and_prints_no_note` | none | done |
| AV-SIGNING-31 | Cat fails closed for unsigned files and wrong verification keys. | `tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_fails_closed_for_an_unsigned_file`<br>`tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_fails_closed_for_the_wrong_key` | none | done |
| AV-SIGNING-32 | The documented local command examples run with their recorded exit codes and output. | `tests/docs/test_reference_examples.py::test_reference_page[SIGNING]` | none | done |
| AV-SIGNING-33 | Keygen creates private key files with POSIX mode 0600. | `tests/artifact/test_signing.py::test_key_files` | none | done |
| AV-SIGNING-34 | Private key reference files are refused when others can access them on POSIX. | `tests/security/test_credential_refs.py::test_file_reference_refuses_a_file_others_can_access` | none | done |
| AV-SIGNING-35 | Public reference files can be read without private-file restrictions. | `tests/security/test_credential_refs.py::test_a_file_that_is_not_secret_can_be_read_with_private_false` | none | done |
| AV-SIGNING-36 | Reference errors name the reference without exposing a value. | `tests/security/test_credential_refs.py::test_env_and_file_errors_name_the_reference_never_a_value` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-SIGNING-01 | `tests/artifact/test_signing.py::test_sign_then_verify_round_trip` | 1 passed |
| AV-SIGNING-02 | `tests/artifact/test_signing.py::test_unsigned_artifact_fails_verify` | 1 passed |
| AV-SIGNING-03 | `tests/artifact/test_signing.py::test_wrong_key_fails` | 1 passed |
| AV-SIGNING-04 | `tests/artifact/test_signing.py::test_forged_artifact_with_rewritten_hashes_fails_verify` | 1 passed |
| AV-SIGNING-05 | `tests/artifact/test_signing.py::test_forged_and_stripped_signature_fails` | 1 passed |
| AV-SIGNING-06 | `tests/artifact/test_signing.py::test_attacker_resigns_with_own_key_fails_trusted_key` | 1 passed |
| AV-SIGNING-07 | `tests/artifact/test_signing.py::test_signature_is_not_valid_for_another_message` | 1 passed |
| AV-SIGNING-08 | `tests/artifact/test_signing.py::test_resign_replaces_signature` | 1 passed |
| AV-SIGNING-09 | `tests/artifact/test_signing.py::test_sign_to_output_leaves_source_untouched` | 1 passed |
| AV-SIGNING-10 | `tests/artifact/test_signing.py::test_cli_sign_verify_flags` | 1 passed |
| AV-SIGNING-11 | `tests/artifact/test_signing_keys.py::test_signing_input_is_domain_plus_exact_manifest_bytes` | 1 passed |
| AV-SIGNING-12 | `tests/artifact/test_signing_keys.py::test_private_key_from_stdin_env_file_and_kv` | 1 passed |
| AV-SIGNING-13 | `tests/artifact/test_signing_keys.py::test_cli_sign_with_key_from_env_stdin_file` | 1 passed |
| AV-SIGNING-14 | `tests/artifact/test_signing_keys.py::test_cli_verify_key_from_env` | 1 passed |
| AV-SIGNING-15 | `tests/artifact/test_signing_keys.py::test_kv_needs_a_registered_resolver_and_takes_a_fake` | 1 passed |
| AV-SIGNING-16 | `tests/artifact/test_signing_keys.py::test_resolver_failure_text_is_not_passed_on` | 1 passed |
| AV-SIGNING-17 | `tests/artifact/test_signing_keys.py::test_core_does_not_import_a_cloud_sdk` | 1 passed |
| AV-SIGNING-18 | `tests/artifact/test_signing_keys.py::test_keygen_writes_an_encrypted_pkcs8_key_by_default` | 1 passed |
| AV-SIGNING-19 | `tests/artifact/test_signing_keys.py::test_encrypted_key_is_standard_pkcs8` | 1 passed |
| AV-SIGNING-20 | `tests/artifact/test_signing_keys.py::test_wrong_or_missing_passphrase_fails_closed` | 1 passed |
| AV-SIGNING-21 | `tests/artifact/test_signing_keys.py::test_unencrypted_key_must_be_asked_for_and_warns` | 1 passed |
| AV-SIGNING-22 | `tests/artifact/test_signing_keys.py::test_keygen_refuses_to_overwrite_with_a_friendly_error` | 1 passed |
| AV-SIGNING-23 | `tests/artifact/test_signing_keys.py::test_key_and_passphrase_cannot_both_use_stdin` | 1 passed |
| AV-SIGNING-24 | `tests/artifact/test_signing_keys.py::test_passphrase_has_no_command_line_form` | 1 passed |
| AV-SIGNING-25 | `tests/artifact/test_signing_keys.py::test_read_passphrase_order_and_prompt` | 1 passed |
| AV-SIGNING-26 | `tests/artifact/test_signing_keys.py::test_unsigned_read_notices_and_reports_status` | 1 passed |
| AV-SIGNING-27 | `tests/artifact/test_signing_keys.py::test_signed_but_unverified_read_notices_with_key_id` | 1 passed |
| AV-SIGNING-28 | `tests/artifact/test_signing_keys.py::test_verified_read_has_no_notice` | 1 passed |
| AV-SIGNING-29 | `tests/artifact/test_signing_keys.py::test_cli_prints_the_notice_on_stderr_once` | 1 passed |
| AV-SIGNING-30 | `tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_checks_the_signature_and_prints_no_note` | 1 passed |
| AV-SIGNING-31 | `tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_fails_closed_for_an_unsigned_file` | 1 passed |
| AV-SIGNING-31 | `tests/cli/test_bugs_cli_cat_verify.py::test_cat_verify_fails_closed_for_the_wrong_key` | 1 passed |
| AV-SIGNING-32 | `tests/docs/test_reference_examples.py::test_reference_page[SIGNING]` | 1 passed |
| AV-SIGNING-33 | `tests/artifact/test_signing.py::test_key_files` | 1 passed |
| AV-SIGNING-34 | `tests/security/test_credential_refs.py::test_file_reference_refuses_a_file_others_can_access` | 7 passed |
| AV-SIGNING-35 | `tests/security/test_credential_refs.py::test_a_file_that_is_not_secret_can_be_read_with_private_false` | 1 passed |
| AV-SIGNING-36 | `tests/security/test_credential_refs.py::test_env_and_file_errors_name_the_reference_never_a_value` | 1 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- A profile that has a value vault records vault: {vault_id, sha256} in its manifest, so the signature covers the vault's hash: replace or alter the vault and shape vault verify --verify PUBKEY and shape generate --vault ... --verify PUBKEY fail (exit 1). — Requested signing tests do not prove vault operations; W-sign-1 moves this experimental section.
- Shape vault rekey writes a new profile, drops the old signature (a notice says so) and signs the new one only with --key. — No proving vault-rekey test in this draft; W-sign-1 moves it.
- --verify PUBKEY on inspect, show, check, diff, query and plan verifies every .shape input before the command runs and stops with exit code 1 if one fails. — Inspect and cat are covered; the entire listed-command matrix is not proven by the selected tests, and W-sign-1 removes query.
- kv://... is a secret store, through a resolver the host registers (the Fabric plugin provides Azure Key Vault). — Fake resolver tests prove registration only; no real Azure Key Vault receipt exists here; W-sign-1 must reword or supply a real run.
- Verification checks the signature first and then every content hash. — Forgery rejection is covered; requested tests do not assert the exact verification ordering.
- Windows has no mode bits: keygen says so (KeyFilePermissionWarning). — Linux execution does not prove the Windows warning path.
- Shape migrate never touches the signed original, writes the migrated artifact as a new file, and writes a signed receipt that names both files by SHA-256 and records the source's signature. — Migration semantics are outside requested signing unit tests; W-sign-1 moves text/example to an experimental page even if its local transcript passes.
- A signed source needs --sign-key, or --unsigned-receipt to accept an unsigned receipt on purpose. — Migration refusal matrix is not proven by the selected tests; W-sign-1 moves this.
- Every signature, in an artifact and in a receipt, carries its algorithm. — Artifact signature tests do not prove all receipt formats.
- Shape never prints a key or a passphrase. — Selected resolver redaction tests are narrower than this absolute output guarantee.
- Artifacts signed before the rotation stay valid under the old key: keep its public half in your trust list for as long as those artifacts matter. — Key matching is tested; lifetime/rotation policy is guidance, not a temporal guarantee test.
- Signing says nothing about whether the content is safe to share. — Security guidance; signing tests do not judge shareability.

Transfer with: W-sign-1 / W-sign-3 and its promotion package. Keep Python API examples experimental until API_STABILITY.md lists their surface.

## docs/VERIFY.md

since: 0.9.4

| Claim | Short claim | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-VERIFY-01 | Verify loads Parquet, CSV and JSONL tables. | `tests/quality/test_verify.py::test_load_tables_formats_and_errors` | none | done |
| AV-VERIFY-02 | A mixed-format directory loads every data file. | `tests/quality/test_verify.py::test_a_mixed_format_directory_loads_every_file` | none | done |
| AV-VERIFY-03 | A table present in two formats is refused. | `tests/quality/test_verify.py::test_a_table_in_two_formats_is_refused` | none | done |
| AV-VERIFY-04 | An explicit format names the files it skips. | `tests/quality/test_verify.py::test_an_explicit_format_names_the_files_it_skips` | none | done |
| AV-VERIFY-05 | Without a schema verify reports row counts. | `tests/quality/test_verify.py::test_without_a_schema_only_row_counts_are_reported` | none | done |
| AV-VERIFY-06 | Verify emits Markdown and JSON reports. | `tests/quality/test_verify.py::test_reports` | none | done |
| AV-VERIFY-07 | CLI exit codes distinguish success, failed gates and report outputs. | `tests/quality/test_verify.py::test_cli_exit_codes_and_report_files` | none | done |
| AV-VERIFY-08 | Strict mode fails on warnings. | `tests/quality/test_verify.py::test_cli_strict_fails_on_warnings` | none | done |
| AV-VERIFY-09 | Unreadable or invalid inputs make verify exit 2. | `tests/quality/test_verify.py::test_cli_input_errors_exit_2` | none | done |
| AV-VERIFY-10 | A gate schema can be loaded from a profile artifact. | `tests/quality/test_verify.py::test_gate_schema_from_a_profile_artifact` | none | done |
| AV-VERIFY-11 | A dataset profile supplies detected foreign keys to verification. | `tests/quality/test_verify.py::test_gate_schema_from_a_dataset_profile_checks_the_detected_foreign_key` | none | done |
| AV-VERIFY-12 | Verify on a shape artifact checks its signature. | `tests/quality/test_verify.py::test_cli_verify_on_a_shape_artifact_checks_its_signature` | none | done |
| AV-VERIFY-13 | Gate schema declarations are validated when read. | `tests/quality/test_verify.py::test_gate_schema_round_trip_and_validation` | none | done |
| AV-VERIFY-14 | Schema conformance fails missing columns and warns about extras or incompatible types. | `tests/quality/test_gates.py::test_schema_conformance_missing_extra_and_type` | none | done |
| AV-VERIFY-15 | Null constraints count null and NaN values. | `tests/quality/test_gates.py::test_null_constraint_counts_nulls_and_nans` | none | done |
| AV-VERIFY-16 | Unique constraints check single and composite primary keys. | `tests/quality/test_gates.py::test_unique_constraint_single_and_composite_keys` | none | done |
| AV-VERIFY-17 | Referential integrity counts orphan keys and ignores null keys. | `tests/quality/test_gates.py::test_referential_integrity_counts_orphans_and_ignores_nulls` | none | done |
| AV-VERIFY-18 | Range constraints check configured bounds. | `tests/quality/test_gates.py::test_range_constraint` | none | done |
| AV-VERIFY-19 | Temporal constraints check date ranges, future dates and ordering. | `tests/quality/test_gates.py::test_temporal_consistency` | none | done |
| AV-VERIFY-20 | File-format gates require readable nonempty files. | `tests/quality/test_gates.py::test_file_format_gate` | none | done |
| AV-VERIFY-21 | Schema drift compares tables, columns and types against a baseline. | `tests/quality/test_gates.py::test_schema_drift` | none | done |
| AV-VERIFY-22 | KS distribution tests warn on statistically different data. | `tests/quality/test_gates.py::test_distribution_ks_passes_for_matching_data_and_warns_on_drift` | none | done |
| AV-VERIFY-23 | Chi-squared distribution tests check enum frequencies. | `tests/quality/test_gates.py::test_distribution_chi_squared` | none | done |
| AV-VERIFY-24 | Missing scipy skips distribution checking with a warning. | `tests/quality/test_gates.py::test_distribution_without_scipy_or_schema_is_skipped` | none | done |
| AV-VERIFY-25 | The runner executes all nine gates. | `tests/quality/test_gates.py::test_runner_runs_all_nine_or_a_chosen_few` | none | done |
| AV-VERIFY-26 | Custom gates can be registered with the runner. | `tests/quality/test_gates.py::test_custom_gate_registration` | none | done |
| AV-VERIFY-27 | The CLI configuration runs the same gates as the Python API. | `tests/quality/test_verify_config.py::test_the_cli_runs_the_config_gates_like_the_python_api` | none | done |
| AV-VERIFY-28 | Configuration-only verification runs without a schema. | `tests/quality/test_verify_config.py::test_a_config_without_a_schema_runs_only_its_gates` | none | done |
| AV-VERIFY-29 | The verify configuration is a versioned document. | `tests/quality/test_verify_config.py::test_the_config_is_a_versioned_document` | none | done |
| AV-VERIFY-30 | Invalid configuration is refused with the offending rule named. | `tests/quality/test_verify_config.py::test_a_bad_config_is_refused_with_the_rule` | none | done |
| AV-VERIFY-31 | Inapplicable temporal rules warn rather than passing silently. | `tests/quality/test_verify_config.py::test_temporal_rules_that_cannot_apply_warn_instead_of_passing_silently` | none | done |
| AV-VERIFY-32 | A generator that copies confidential source rows fails the memorization gate. | `tests/quality/test_memorization.py::test_a_generator_that_copies_source_rows_fails_the_gate` | none | done |
| AV-VERIFY-33 | Memorization reports row indices without printing values. | `tests/quality/test_memorization.py::test_the_report_names_the_rows_and_never_prints_a_value` | none | done |
| AV-VERIFY-34 | Bootstrap copying fails at confidential classifications. | `tests/quality/test_memorization.py::test_the_bootstrap_strategy_is_caught_at_confidential` | none | done |
| AV-VERIFY-35 | Independent generated data passes the memorization gate. | `tests/quality/test_memorization.py::test_an_independent_generator_passes` | none | done |
| AV-VERIFY-36 | The default memorization policy fails from confidential upwards. | `tests/quality/test_memorization.py::test_the_default_policy_fails_from_confidential_upwards` | none | done |
| AV-VERIFY-37 | PII and sensitive classifications rank as confidential. | `tests/quality/test_memorization.py::test_pii_and_sensitive_rank_as_confidential` | none | done |
| AV-VERIFY-38 | The fail_at setting adjusts the classification threshold. | `tests/quality/test_memorization.py::test_fail_at_lowers_or_raises_the_threshold` | none | done |
| AV-VERIFY-39 | Public-only copying is reported but cannot fail the gate. | `tests/quality/test_memorization.py::test_copies_on_public_columns_alone_do_not_fail_but_are_reported` | none | done |
| AV-VERIFY-40 | Shared source keys do not identify a reproduced row. | `tests/quality/test_memorization.py::test_a_value_shared_by_several_source_rows_is_not_a_reproduction` | none | done |
| AV-VERIFY-41 | Null values never match in memorization keys. | `tests/quality/test_memorization.py::test_nulls_never_match` | none | done |
| AV-VERIFY-42 | Numeric noise does not conceal a copied text key. | `tests/quality/test_memorization.py::test_numeric_noise_does_not_hide_a_copied_text_column_but_a_numeric_only_key_is_exact` | none | done |
| AV-VERIFY-43 | A copied row has zero nearest-neighbour distance. | `tests/quality/test_memorization.py::test_nearest_neighbour_distance_is_zero_for_a_copy_and_positive_for_an_independent_table` | none | done |
| AV-VERIFY-44 | The nearest-neighbour distance threshold is opt-in. | `tests/quality/test_memorization.py::test_min_nn_distance_is_an_opt_in_second_criterion` | none | done |
| AV-VERIFY-45 | The row cap does not limit exact row matching. | `tests/quality/test_memorization.py::test_row_cap_keeps_exact_matching_over_every_row` | none | done |
| AV-VERIFY-46 | Unclassified source comparison warns and strict mode fails. | `tests/quality/test_memorization.py::test_cli_verify_source_without_classifications_warns_and_strict_fails` | none | done |
| AV-VERIFY-47 | Timeseries intervals accept the documented fixed-duration units. | `tests/quality/test_timeseries.py::test_every_parses_the_documented_units` | none | done |
| AV-VERIFY-48 | Timeseries intervals refuse unsupported interval values. | `tests/quality/test_timeseries.py::test_every_refuses_what_is_not_an_interval` | none | done |
| AV-VERIFY-49 | Missing time steps are counted and located. | `tests/quality/test_timeseries.py::test_missing_steps_are_counted_and_located` | none | done |
| AV-VERIFY-50 | Gap checks run per group regardless of input order. | `tests/quality/test_timeseries.py::test_gaps_are_found_per_group_and_the_input_order_does_not_matter` | none | done |
| AV-VERIFY-51 | Duplicate and off-grid times warn rather than fail. | `tests/quality/test_timeseries.py::test_duplicates_and_off_grid_steps_warn_and_do_not_fail` | none | done |
| AV-VERIFY-52 | Null times are skipped with a warning. | `tests/quality/test_timeseries.py::test_null_times_are_reported_as_a_warning` | none | done |
| AV-VERIFY-53 | ISO text timestamps are accepted. | `tests/quality/test_timeseries.py::test_iso_text_times_are_read` | none | done |
| AV-VERIFY-54 | A repeated run longer than max_run is stuck. | `tests/quality/test_timeseries.py::test_a_run_longer_than_the_limit_is_stuck` | none | done |
| AV-VERIFY-55 | A run exactly max_run long is allowed. | `tests/quality/test_timeseries.py::test_the_stuck_limit_boundary_allows_a_run_of_exactly_max_run` | none | done |
| AV-VERIFY-56 | Null and NaN values break stuck runs. | `tests/quality/test_timeseries.py::test_nulls_and_nans_break_a_run` | none | done |
| AV-VERIFY-57 | The skipped daylight-saving hour is not counted as a gap. | `tests/quality/test_timeseries.py::test_the_hour_that_does_not_exist_is_not_a_gap_when_the_zone_is_given` | none | done |
| AV-VERIFY-58 | Rows in the nonexistent spring hour are errors. | `tests/quality/test_timeseries.py::test_rows_in_the_skipped_hour_are_reported` | none | done |
| AV-VERIFY-59 | The repeated autumn hour is expected twice by default. | `tests/quality/test_timeseries.py::test_the_repeated_hour_is_expected_twice_by_default` | none | done |
| AV-VERIFY-60 | The repeated autumn hour can be configured to occur once. | `tests/quality/test_timeseries.py::test_the_repeated_hour_can_be_expected_once` | none | done |
| AV-VERIFY-61 | Series not crossing a daylight-saving transition are not checked. | `tests/quality/test_timeseries.py::test_a_series_that_does_not_cross_a_transition_is_not_checked` | none | done |
| AV-VERIFY-62 | Aware timestamps are checked in the configured time zone. | `tests/quality/test_timeseries.py::test_aware_times_are_checked_in_the_zone` | none | done |
| AV-VERIFY-63 | Reconciliation always compares source and target row counts. | `tests/quality/test_reconcile.py::test_a_count_difference_is_reported_with_both_sides` | none | done |
| AV-VERIFY-64 | Count tolerances include the boundary. | `tests/quality/test_reconcile.py::test_count_tolerance_boundaries_are_inclusive` | none | done |
| AV-VERIFY-65 | Count tolerance overrides the default tolerance. | `tests/quality/test_reconcile.py::test_tolerance_is_the_default_for_counts_and_count_tolerance_wins` | none | done |
| AV-VERIFY-66 | Partition counts are compared between both sides. | `tests/quality/test_reconcile.py::test_partition_counts_are_compared` | none | done |
| AV-VERIFY-67 | A partition absent on one side counts as zero. | `tests/quality/test_reconcile.py::test_a_partition_on_one_side_only_counts_as_zero_on_the_other` | none | done |
| AV-VERIFY-68 | Each supported aggregate is computed. | `tests/quality/test_reconcile.py::test_every_aggregate_is_computed` | none | done |
| AV-VERIFY-69 | Aggregates can override tolerances with inclusive boundaries. | `tests/quality/test_reconcile.py::test_aggregate_tolerance_boundaries_are_inclusive_and_per_aggregate` | none | done |
| AV-VERIFY-70 | Aggregate differences are reported per key. | `tests/quality/test_reconcile.py::test_aggregates_per_key_report_the_keys_that_differ` | none | done |
| AV-VERIFY-71 | Keys present on only one side are reported. | `tests/quality/test_reconcile.py::test_keys_on_one_side_only_are_reported` | none | done |
| AV-VERIFY-72 | Source and target aggregate column names may differ. | `tests/quality/test_reconcile.py::test_a_target_column_name_can_differ` | none | done |
| AV-VERIFY-73 | Missing reconciliation columns produce findings. | `tests/quality/test_reconcile.py::test_a_missing_column_is_a_finding_not_a_crash` | none | done |
| AV-VERIFY-74 | Nonnumeric minimum and maximum compare by equality. | `tests/quality/test_reconcile.py::test_non_numeric_min_and_max_compare_by_equality` | none | done |
| AV-VERIFY-75 | Reconciliation reads path sides through the source layer. | `tests/quality/test_reconcile.py::test_sides_are_read_through_load_table` | none | done |
| AV-VERIFY-76 | Reconciliation can use verified tables as its sides. | `tests/quality/test_reconcile.py::test_the_gate_reads_table_sides_from_the_verified_tables` | none | done |
| AV-VERIFY-77 | An unreadable reconciliation side fails with its reason. | `tests/quality/test_reconcile.py::test_an_unreadable_path_side_fails_the_gate_with_the_reason` | none | done |
| AV-VERIFY-78 | Synthetic data retaining the predictive signal passes utility. | `tests/quality/test_utility.py::test_synthetic_data_that_keeps_the_signal_passes_with_high_retention` | none | done |
| AV-VERIFY-79 | Synthetic data losing the predictive signal fails utility. | `tests/quality/test_utility.py::test_the_negative_control_synthetic_data_without_the_signal_fails` | none | done |
| AV-VERIFY-80 | Utility enforces the minimum retention at its boundary. | `tests/quality/test_utility.py::test_min_retention_is_enforced_at_the_boundary` | none | done |
| AV-VERIFY-81 | Utility defaults to minimum retention 0.8. | `tests/quality/test_utility.py::test_the_default_minimum_retention_is_documented_and_applied` | none | done |
| AV-VERIFY-82 | Regression utility uses R squared. | `tests/quality/test_utility.py::test_regression_uses_r_squared` | none | done |
| AV-VERIFY-83 | Utility detects the task or accepts an explicit task. | `tests/quality/test_utility.py::test_the_task_can_be_forced_and_is_detected_from_the_target` | none | done |
| AV-VERIFY-84 | A real model that cannot beat chance makes utility fail. | `tests/quality/test_utility.py::test_a_real_model_that_does_not_beat_chance_cannot_judge_utility` | none | done |
| AV-VERIFY-85 | Held-out real rows never train the synthetic model. | `tests/quality/test_utility.py::test_the_held_out_real_rows_never_train_the_synthetic_model` | none | done |
| AV-VERIFY-86 | Identifier-like text columns are excluded from utility features. | `tests/quality/test_utility.py::test_identifier_like_text_columns_are_not_features` | none | done |
| AV-VERIFY-87 | Missing scikit-learn makes utility name the required extra. | `tests/quality/test_utility.py::test_without_scikit_learn_the_gate_names_the_extra` | none | done |
| AV-VERIFY-88 | Quarantine copies files and writes metadata. | `tests/quality/test_quarantine.py::test_quarantine_file_copies_and_writes_metadata` | none | done |
| AV-VERIFY-89 | Quarantine writes generated tables in supported formats. | `tests/quality/test_quarantine.py::test_quarantine_table_formats` | none | done |
| AV-VERIFY-90 | Quarantine produces a report of quarantined artifacts. | `tests/quality/test_quarantine.py::test_list_and_report` | none | done |
| AV-VERIFY-91 | Quarantine names cannot escape the quarantine root. | `tests/quality/test_quarantine.py::test_names_cannot_escape_the_quarantine_root` | none | done |
| AV-VERIFY-92 | The documented local command examples run with their recorded exit codes and output. | `tests/docs/test_reference_examples.py::test_reference_page[VERIFY]` | none | done |
| AV-VERIFY-93 | Enum frequencies in nontext columns are matched by their text values. | `tests/quality/test_gates_audit.py::test_an_enum_on_a_non_text_column_is_tested` | none | done |

### Execution evidence

These are the latest observed results from the focused run and targeted environment-repair reruns in this isolated checkout (the lane status preserves earlier failures); family counts include every collected parameter instance. Failed or skipped proofs remain open.

| Claim ID | Exact proof | Ran and passed? |
|---|---|---|
| AV-VERIFY-01 | `tests/quality/test_verify.py::test_load_tables_formats_and_errors` | 1 passed |
| AV-VERIFY-02 | `tests/quality/test_verify.py::test_a_mixed_format_directory_loads_every_file` | 1 passed |
| AV-VERIFY-03 | `tests/quality/test_verify.py::test_a_table_in_two_formats_is_refused` | 1 passed |
| AV-VERIFY-04 | `tests/quality/test_verify.py::test_an_explicit_format_names_the_files_it_skips` | 1 passed |
| AV-VERIFY-05 | `tests/quality/test_verify.py::test_without_a_schema_only_row_counts_are_reported` | 1 passed |
| AV-VERIFY-06 | `tests/quality/test_verify.py::test_reports` | 1 passed |
| AV-VERIFY-07 | `tests/quality/test_verify.py::test_cli_exit_codes_and_report_files` | 1 passed |
| AV-VERIFY-08 | `tests/quality/test_verify.py::test_cli_strict_fails_on_warnings` | 1 passed |
| AV-VERIFY-09 | `tests/quality/test_verify.py::test_cli_input_errors_exit_2` | 1 passed |
| AV-VERIFY-10 | `tests/quality/test_verify.py::test_gate_schema_from_a_profile_artifact` | 1 passed |
| AV-VERIFY-11 | `tests/quality/test_verify.py::test_gate_schema_from_a_dataset_profile_checks_the_detected_foreign_key` | 1 passed |
| AV-VERIFY-12 | `tests/quality/test_verify.py::test_cli_verify_on_a_shape_artifact_checks_its_signature` | 1 passed |
| AV-VERIFY-13 | `tests/quality/test_verify.py::test_gate_schema_round_trip_and_validation` | 1 passed |
| AV-VERIFY-14 | `tests/quality/test_gates.py::test_schema_conformance_missing_extra_and_type` | 1 passed |
| AV-VERIFY-15 | `tests/quality/test_gates.py::test_null_constraint_counts_nulls_and_nans` | 1 passed |
| AV-VERIFY-16 | `tests/quality/test_gates.py::test_unique_constraint_single_and_composite_keys` | 1 passed |
| AV-VERIFY-17 | `tests/quality/test_gates.py::test_referential_integrity_counts_orphans_and_ignores_nulls` | 1 passed |
| AV-VERIFY-18 | `tests/quality/test_gates.py::test_range_constraint` | 1 passed |
| AV-VERIFY-19 | `tests/quality/test_gates.py::test_temporal_consistency` | 1 passed |
| AV-VERIFY-20 | `tests/quality/test_gates.py::test_file_format_gate` | 1 passed |
| AV-VERIFY-21 | `tests/quality/test_gates.py::test_schema_drift` | 1 passed |
| AV-VERIFY-22 | `tests/quality/test_gates.py::test_distribution_ks_passes_for_matching_data_and_warns_on_drift` | 1 passed |
| AV-VERIFY-23 | `tests/quality/test_gates.py::test_distribution_chi_squared` | 1 passed |
| AV-VERIFY-24 | `tests/quality/test_gates.py::test_distribution_without_scipy_or_schema_is_skipped` | 1 passed |
| AV-VERIFY-25 | `tests/quality/test_gates.py::test_runner_runs_all_nine_or_a_chosen_few` | 1 passed |
| AV-VERIFY-26 | `tests/quality/test_gates.py::test_custom_gate_registration` | 1 passed |
| AV-VERIFY-27 | `tests/quality/test_verify_config.py::test_the_cli_runs_the_config_gates_like_the_python_api` | 1 passed |
| AV-VERIFY-28 | `tests/quality/test_verify_config.py::test_a_config_without_a_schema_runs_only_its_gates` | 1 passed |
| AV-VERIFY-29 | `tests/quality/test_verify_config.py::test_the_config_is_a_versioned_document` | 1 passed |
| AV-VERIFY-30 | `tests/quality/test_verify_config.py::test_a_bad_config_is_refused_with_the_rule` | 11 passed |
| AV-VERIFY-31 | `tests/quality/test_verify_config.py::test_temporal_rules_that_cannot_apply_warn_instead_of_passing_silently` | 1 passed |
| AV-VERIFY-32 | `tests/quality/test_memorization.py::test_a_generator_that_copies_source_rows_fails_the_gate` | 1 passed |
| AV-VERIFY-33 | `tests/quality/test_memorization.py::test_the_report_names_the_rows_and_never_prints_a_value` | 1 passed |
| AV-VERIFY-34 | `tests/quality/test_memorization.py::test_the_bootstrap_strategy_is_caught_at_confidential` | 1 passed |
| AV-VERIFY-35 | `tests/quality/test_memorization.py::test_an_independent_generator_passes` | 1 passed |
| AV-VERIFY-36 | `tests/quality/test_memorization.py::test_the_default_policy_fails_from_confidential_upwards` | 5 passed |
| AV-VERIFY-37 | `tests/quality/test_memorization.py::test_pii_and_sensitive_rank_as_confidential` | 1 passed |
| AV-VERIFY-38 | `tests/quality/test_memorization.py::test_fail_at_lowers_or_raises_the_threshold` | 1 passed |
| AV-VERIFY-39 | `tests/quality/test_memorization.py::test_copies_on_public_columns_alone_do_not_fail_but_are_reported` | 1 passed |
| AV-VERIFY-40 | `tests/quality/test_memorization.py::test_a_value_shared_by_several_source_rows_is_not_a_reproduction` | 1 passed |
| AV-VERIFY-41 | `tests/quality/test_memorization.py::test_nulls_never_match` | 1 passed |
| AV-VERIFY-42 | `tests/quality/test_memorization.py::test_numeric_noise_does_not_hide_a_copied_text_column_but_a_numeric_only_key_is_exact` | 1 passed |
| AV-VERIFY-43 | `tests/quality/test_memorization.py::test_nearest_neighbour_distance_is_zero_for_a_copy_and_positive_for_an_independent_table` | 1 passed |
| AV-VERIFY-44 | `tests/quality/test_memorization.py::test_min_nn_distance_is_an_opt_in_second_criterion` | 1 passed |
| AV-VERIFY-45 | `tests/quality/test_memorization.py::test_row_cap_keeps_exact_matching_over_every_row` | 1 passed |
| AV-VERIFY-46 | `tests/quality/test_memorization.py::test_cli_verify_source_without_classifications_warns_and_strict_fails` | 1 passed |
| AV-VERIFY-47 | `tests/quality/test_timeseries.py::test_every_parses_the_documented_units` | 7 passed |
| AV-VERIFY-48 | `tests/quality/test_timeseries.py::test_every_refuses_what_is_not_an_interval` | 10 passed |
| AV-VERIFY-49 | `tests/quality/test_timeseries.py::test_missing_steps_are_counted_and_located` | 1 passed |
| AV-VERIFY-50 | `tests/quality/test_timeseries.py::test_gaps_are_found_per_group_and_the_input_order_does_not_matter` | 1 passed |
| AV-VERIFY-51 | `tests/quality/test_timeseries.py::test_duplicates_and_off_grid_steps_warn_and_do_not_fail` | 1 passed |
| AV-VERIFY-52 | `tests/quality/test_timeseries.py::test_null_times_are_reported_as_a_warning` | 1 passed |
| AV-VERIFY-53 | `tests/quality/test_timeseries.py::test_iso_text_times_are_read` | 1 passed |
| AV-VERIFY-54 | `tests/quality/test_timeseries.py::test_a_run_longer_than_the_limit_is_stuck` | 1 passed |
| AV-VERIFY-55 | `tests/quality/test_timeseries.py::test_the_stuck_limit_boundary_allows_a_run_of_exactly_max_run` | 1 passed |
| AV-VERIFY-56 | `tests/quality/test_timeseries.py::test_nulls_and_nans_break_a_run` | 1 passed |
| AV-VERIFY-57 | `tests/quality/test_timeseries.py::test_the_hour_that_does_not_exist_is_not_a_gap_when_the_zone_is_given` | 1 passed |
| AV-VERIFY-58 | `tests/quality/test_timeseries.py::test_rows_in_the_skipped_hour_are_reported` | 1 passed |
| AV-VERIFY-59 | `tests/quality/test_timeseries.py::test_the_repeated_hour_is_expected_twice_by_default` | 1 passed |
| AV-VERIFY-60 | `tests/quality/test_timeseries.py::test_the_repeated_hour_can_be_expected_once` | 1 passed |
| AV-VERIFY-61 | `tests/quality/test_timeseries.py::test_a_series_that_does_not_cross_a_transition_is_not_checked` | 1 passed |
| AV-VERIFY-62 | `tests/quality/test_timeseries.py::test_aware_times_are_checked_in_the_zone` | 1 passed |
| AV-VERIFY-63 | `tests/quality/test_reconcile.py::test_a_count_difference_is_reported_with_both_sides` | 1 passed |
| AV-VERIFY-64 | `tests/quality/test_reconcile.py::test_count_tolerance_boundaries_are_inclusive` | 1 passed |
| AV-VERIFY-65 | `tests/quality/test_reconcile.py::test_tolerance_is_the_default_for_counts_and_count_tolerance_wins` | 1 passed |
| AV-VERIFY-66 | `tests/quality/test_reconcile.py::test_partition_counts_are_compared` | 1 passed |
| AV-VERIFY-67 | `tests/quality/test_reconcile.py::test_a_partition_on_one_side_only_counts_as_zero_on_the_other` | 1 passed |
| AV-VERIFY-68 | `tests/quality/test_reconcile.py::test_every_aggregate_is_computed` | 6 passed |
| AV-VERIFY-69 | `tests/quality/test_reconcile.py::test_aggregate_tolerance_boundaries_are_inclusive_and_per_aggregate` | 1 passed |
| AV-VERIFY-70 | `tests/quality/test_reconcile.py::test_aggregates_per_key_report_the_keys_that_differ` | 1 passed |
| AV-VERIFY-71 | `tests/quality/test_reconcile.py::test_keys_on_one_side_only_are_reported` | 1 passed |
| AV-VERIFY-72 | `tests/quality/test_reconcile.py::test_a_target_column_name_can_differ` | 1 passed |
| AV-VERIFY-73 | `tests/quality/test_reconcile.py::test_a_missing_column_is_a_finding_not_a_crash` | 1 passed |
| AV-VERIFY-74 | `tests/quality/test_reconcile.py::test_non_numeric_min_and_max_compare_by_equality` | 1 passed |
| AV-VERIFY-75 | `tests/quality/test_reconcile.py::test_sides_are_read_through_load_table` | 1 passed |
| AV-VERIFY-76 | `tests/quality/test_reconcile.py::test_the_gate_reads_table_sides_from_the_verified_tables` | 1 passed |
| AV-VERIFY-77 | `tests/quality/test_reconcile.py::test_an_unreadable_path_side_fails_the_gate_with_the_reason` | 1 passed |
| AV-VERIFY-78 | `tests/quality/test_utility.py::test_synthetic_data_that_keeps_the_signal_passes_with_high_retention` | 1 passed |
| AV-VERIFY-79 | `tests/quality/test_utility.py::test_the_negative_control_synthetic_data_without_the_signal_fails` | 1 passed |
| AV-VERIFY-80 | `tests/quality/test_utility.py::test_min_retention_is_enforced_at_the_boundary` | 1 passed |
| AV-VERIFY-81 | `tests/quality/test_utility.py::test_the_default_minimum_retention_is_documented_and_applied` | 1 passed |
| AV-VERIFY-82 | `tests/quality/test_utility.py::test_regression_uses_r_squared` | 1 passed |
| AV-VERIFY-83 | `tests/quality/test_utility.py::test_the_task_can_be_forced_and_is_detected_from_the_target` | 1 passed |
| AV-VERIFY-84 | `tests/quality/test_utility.py::test_a_real_model_that_does_not_beat_chance_cannot_judge_utility` | 1 passed |
| AV-VERIFY-85 | `tests/quality/test_utility.py::test_the_held_out_real_rows_never_train_the_synthetic_model` | 1 passed |
| AV-VERIFY-86 | `tests/quality/test_utility.py::test_identifier_like_text_columns_are_not_features` | 1 passed |
| AV-VERIFY-87 | `tests/quality/test_utility.py::test_without_scikit_learn_the_gate_names_the_extra` | 1 passed |
| AV-VERIFY-88 | `tests/quality/test_quarantine.py::test_quarantine_file_copies_and_writes_metadata` | 1 passed |
| AV-VERIFY-89 | `tests/quality/test_quarantine.py::test_quarantine_table_formats` | 3 passed |
| AV-VERIFY-90 | `tests/quality/test_quarantine.py::test_list_and_report` | 1 passed |
| AV-VERIFY-91 | `tests/quality/test_quarantine.py::test_names_cannot_escape_the_quarantine_root` | 8 passed |
| AV-VERIFY-92 | `tests/docs/test_reference_examples.py::test_reference_page[VERIFY]` | 1 passed |
| AV-VERIFY-93 | `tests/quality/test_gates_audit.py::test_an_enum_on_a_non_text_column_is_tested` | 3 passed |

### Sentences without complete proving tests

The following source sentences (line-wrapped text normalized) are not represented as fully proven claims. No test is invented for them. Reader advice is distinguished from missing behavioral evidence.

- Types: integer, bigint, string, float, decimal, date, datetime, boolean, uuid (any other type is not type-checked). — Basic gate/type tests do not independently pin the complete documented type list and unknown-type policy.
- Every change has a class; a change of class fail_on or a stricter one fails, the others warn. — Basic schema drift is mapped; the fail_on/classes/column_classes override and shape.yml precedence matrix is not proven by these test files.
- Shape verify accepts and ignores privacy. — The privacy/report-card row is outside this claim inventory and W-verify-1 must reword/move it.
- The nearest-neighbour distance is the Euclidean distance over the numeric columns both tables share, each divided by the source's standard deviation (nulls take the source median). — Zero/positive distances are proven, not each normalization and imputation formula detail.
- The report names the index of each reproduced row in the generated table (up to 1,000 of them). — Index/value redaction is proven; the exact 1000-item cap is not explicitly asserted by mapped tests.
- The model is a random forest of 100 trees. — Mapped utility tests prove scores and holdout isolation, not the exact tree count.
- Each finding has expected and observed with the gaps, runs or transitions, up to 20 of each. — Mapped timeseries tests prove findings; the exact 20-item cap is not explicitly asserted.
- Contract v1 accepts the same two lists as optional top-level rules, timeseries and reconcile. — These gate tests do not prove the contract acceptance/version matrix; the page example covers only its concrete check invocation.
- A contract with these rules and no data exits 2. — No matching contract-without-data test is cited in this draft.
- Each error becomes a violation with column, rule, expected and observed; reconcile violations add reconciliation; warnings are not violations. — Contract adaptation needs its own proving tests, not only gate findings.
- Shape scorecard runs these gates and scores accuracy, completeness, conformity, consistency, timeliness and uniqueness from them, with failing-row samples and an optional flag column. — Scorecard is outside this task's proving tests and W-verify-1 moves this section to SCORECARD.md.
- --slice-by scores them per slice; shape skew compares serving data with training data. — Scorecard/skew claims remain for their own pages and test packages.

Transfer with: W-verify-1 / W-verify-3 and its promotion package. Keep Python API examples experimental until API_STABILITY.md lists their surface.
