# Shape 1.0: definition of done

This is what "1.0 is done" means for the core workflow: **capture or profile, diff, gate, replay**.
Each row is one checkable statement. The proof column names the tests (pytest node ids) or the CI
job that prove it; `python scripts/check_v1_done.py` confirms that every named test is collected by
pytest and every named CI job exists, and `make check` runs it. A row that is not yet true says
`open` and names the work package or issue that closes it. The check proves that the proof exists;
the test suite and CI prove that it passes.

The promises that 1.0 makes are in [CLI_STABILITY.md](CLI_STABILITY.md), [API_STABILITY.md](API_STABILITY.md)
and [plugins/stability.md](plugins/stability.md). What the project will not build is in
[NOT_BUILDING.md](NOT_BUILDING.md).

## Capture and profile

| # | Statement | Proof | Status |
|---|---|---|---|
| V1-01 | The same table captured from CSV, Parquet, a folder, a glob or a Delta table gives the same model. | `tests/cli/test_capture_sources.py::test_the_same_table_gives_the_same_model_in_every_format`, `tests/cli/test_capture_sources.py::test_a_folder_a_glob_and_a_delta_table_give_the_same_model` | done |
| V1-02 | A profile or model written twice from the same input is the same bytes, on any machine. | `tests/artifact/test_container_reproducible.py::test_two_processes_write_equal_bytes` | done |
| V1-03 | A bad input to a command is one `shape: error:` line and exit 2, never a traceback. | `tests/regressions/test_iss_cli_errors.py::test_expected_error_is_one_line_exit_2` | done |
| V1-04 | The share-safe form of a profile is byte-stable. | `tests/cli/test_shape_as_code.py::test_safe_json_is_byte_stable_and_sorted` | done |
| V1-05 | A profile read from an older artifact version still loads. | `tests/artifact/test_migration.py::test_migration_chain`, `tests/artifact/test_container_reproducible.py::test_legacy_deflated_artifact_still_reads` | done |
| V1-06 | Sensitive columns keep statistics and formats only, and a small group is never reported as a count. | | open W1-11 |

## Diff

| # | Statement | Proof | Status |
|---|---|---|---|
| V1-07 | `shape diff` reports a typed difference between two profiles. | `tests/diff/test_diff.py::test_typed_delta`, `tests/diff/test_diff.py::test_diff_models_compares_v2_models_and_v1_captures` | done |
| V1-08 | A change of pattern, spread, range or category shares is found, and samples of one distribution are not reported as drift. | `tests/diff/test_drift_engine.py::test_pattern_change_emails_to_ssns`, `tests/diff/test_drift_engine.py::test_spread_change_with_the_same_mean`, `tests/diff/test_distribution_change_sample_size.py::test_a_real_family_change_is_still_reported` | done |
| V1-09 | A schema change of a feed (a dropped, renamed or retyped column) is reported by `shape compatibility`, exit 5. | `tests/cli/test_capture_sources.py::test_schema_changes_in_a_feed_are_reported` | done |
| V1-10 | A planted-drift sweep finds every planted change, in both kernel modes. | | open W1-08 |

## Gate: `shape check` and `shape verify`

| # | Statement | Proof | Status |
|---|---|---|---|
| V1-11 | `shape check` exits 0 on a profile that meets its contract and 1 on one that does not. | `tests/cli/test_profile_cli.py::test_check_exits_nonzero_on_a_failed_contract`, `tests/cli/test_exit_code_classes.py::test_class_1_a_check_failed` | done |
| V1-12 | `shape check` exits 2 on a missing or unreadable input, with a one-line message. | `tests/cli/test_exit_code_classes.py::test_class_2_bad_input` | done |
| V1-13 | `shape verify` exits 0 when every gate passes, 1 when one fails and 2 on bad input. | `tests/quality/test_verify.py::test_cli_exit_codes_and_report_files`, `tests/quality/test_verify.py::test_cli_input_errors_exit_2` | done |
| V1-14 | `shape verify` fails a generator that copies source rows, and names the rows without printing a value. | `tests/quality/test_memorization.py::test_cli_verify_source_fails_a_copying_generator_with_exit_1`, `tests/quality/test_memorization.py::test_the_report_names_the_rows_and_never_prints_a_value` | done |
| V1-15 | `shape verify` checks the signature of a `.shape` artifact. | `tests/quality/test_verify.py::test_cli_verify_on_a_shape_artifact_checks_its_signature` | done |
| V1-16 | A command's own verdict is exit 3 or above, and each class of exit codes keeps its meaning. | `tests/cli/test_exit_code_classes.py::test_class_3_and_above_is_the_commands_own_verdict`, `tests/cli/test_exit_code_classes.py::test_a_class_is_never_reused_for_another_meaning` | done |
| V1-17 | Every command can print JUnit and SARIF, `--json` and `--dry-run` everywhere, and documents its exit codes. | | open W1-14 |

## Replay: `shape pack replay`

| # | Statement | Proof | Status |
|---|---|---|---|
| V1-18 | Replay regenerates a run from its manifest and checks the dataset id, writing nothing next to the run. | `tests/scenario/test_reproducibility.py::test_replay_regenerates_the_dataset_and_checks_the_id` | done |
| V1-19 | Replay of a changed dataset exits 1 and names both ids and the tuple fields that differ. | `tests/scenario/test_reproducibility.py::test_replay_of_a_changed_dataset_id_is_exit_1_and_names_both_ids`, `tests/scenario/test_reproducibility.py::test_replay_names_the_tuple_fields_that_differ_on_a_mismatch` | done |
| V1-20 | Replay refuses what it cannot check, exit 2. | `tests/scenario/test_reproducibility.py::test_replay_refuses_what_it_cannot_check_with_exit_2` | done |
| V1-21 | The dataset id follows the seed, not the clock, and is the same in both kernel modes. | `tests/scenario/test_reproducibility.py::test_the_dataset_id_follows_the_seed_not_the_clock`, `tests/scenario/test_reproducibility.py::test_the_id_is_independent_of_the_kernel` | done |
| V1-22 | A manifest from a newer Shape is refused with a clear message. | `tests/scenario/test_reproducibility.py::test_a_manifest_from_a_newer_shape_is_refused_with_a_clear_message` | done |

## Promises and release

| # | Statement | Proof | Status |
|---|---|---|---|
| V1-23 | The live CLI is compatible with the 1.0 surface snapshot, and a removed or renamed stable flag fails the check naming it. | `tests/cli/test_cli_surface.py::test_the_live_parser_is_compatible_with_the_baseline`, `tests/cli/test_cli_surface.py::test_removing_a_stable_flag_exits_1_and_names_it` | done |
| V1-24 | Every command that is not stable says "experimental" at the start of its `--help`. | `tests/cli/test_cli_surface.py::test_every_experimental_command_says_so_in_its_help` | done |
| V1-25 | Plugin API v1 is compatible with its baseline, per entry-point group. | `tests/plugins/test_api_v1_compat.py::test_group_protocol_is_compatible_with_the_baseline` | done |
| V1-26 | Every persisted format declares `format` and an integer `version`, and a newer version is refused. | | open W1-01 |
| V1-27 | The native kernel and the pure-Python reference twin pass the suite, on Linux, macOS and Windows. | `ci:ci.yml/test`, `ci:ci.yml/rust` | done |
| V1-28 | Benchmarks against the reference do not regress. | `ci:ci.yml/bench-quick` | done |
| V1-29 | The version is 1.0.0 and the release is published. | | open P8-04 |
