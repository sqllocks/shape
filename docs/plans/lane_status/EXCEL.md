# EXCEL — Excel source (#50) and multi-sheet workbook sink (#51) (lane/EXCEL)

Status: **Step 1 (source, #50) built and pushed; Step 2 (sink, #51) built and pushed.** Final-HEAD suites for both
kernels are recorded in "Checks" (see the last line there). Branch from `build/main-plan` @ 7d94089; not merged since
(main-plan moved to 521310f, docs/tracker only). Times US Eastern. No PR, no comments on the issues.

## Step 1 — Excel source (#50)

| Requirement | Evidence |
|---|---|
| `.xlsx` reader in `[excel]`, openpyxl read-only; used by `shape profile` and commands that read a source | `src/shape/io/excel.py`; `shape.io.open_source("book.xlsx#Sheet")`, `open_workbook`; profile via `src/shape/profile/reference/workbook.py`; `tests/excel/test_excel_source.py` |
| One table per sheet; all visible sheets as a dataset; `book.xlsx#Sheet` / option | `test_one_table_per_visible_sheet`, `test_a_sheet_is_picked_with_a_fragment`, CLI `--sheet`, `--include-hidden` (`test_cli_profile_json_lists_findings`) |
| Cell types kept; ZIP/NDC/member id keep leading zeros | `test_text_cells_keep_leading_zeros`, `test_excel_types_come_through`; a text-stored column is never inferred as number/date by the profile (`_Col.text`) |
| Findings: numbers/dates stored as text, hidden columns, hidden sheets (reported, read on request), error cells (all 7 codes), duplicate headers (renamed `name_2`..), sentinels (`99999`, `00000`, `9999-12-31`, `N/A`, ...) with counts, shares, cells, examples | one e2e test each in `test_excel_source.py` (`test_numbers_stored_as_text...` ... `test_sentinel_values_are_reported`); extra kinds: `blank_header`, `mixed_types` |
| Clear errors for `.xls` and password-protected files | `test_xls_is_refused...`, `test_password_protected_workbook_is_refused`, `test_a_file_that_is_not_a_workbook`, `test_an_archive_that_inflates_absurdly_is_refused` (zip-bomb guard, my addition) |
| Leading-zero round trip through `profile` and `generate --from` | `test_leading_zero_identifiers_survive_profile_and_generate_from`, `test_repeated_zip_codes_regenerate_as_zero_padded_text`. Needed new built-in providers `digits` / `digit_ids` (`providers.py`) and a rule in `generation/learn.py::_zero_padded_width`: a text column of fixed-width digits with a leading zero is generated as zero-padded text, before the pattern rules (the profiler's `phone` pattern otherwise turned a 7-digit member id into a phone number; the pattern detector itself is parity-fixed and untouched) |
| Findings additive; parity for other sources | `findings` key only exists for workbook profiles (`test_csv_profiles_have_no_findings`); `shape profile safe` drops findings (`test_the_share_safe_form_carries_no_findings`); parity run below |

Design notes: findings are in `table["findings"]` (the sheet) and `dataset["findings"]` (workbook-level: hidden sheets);
`Profile.summary()` includes them. Docs: `docs/EXCEL.md`.

## Step 2 — workbook sink (#51)

| Requirement | Evidence |
|---|---|
| One sheet per table in dependency order, valid unique names, `_README` | `src/shape/builtins/sinks/workbook.py`; `ExcelSink.write_workbook`; `generation/output.py` (`excel` = one `<domain>.xlsx` for `write_result`/`write_engine`); `test_retail_round_trips_through_a_workbook`, `test_sheet_names_are_valid_and_unique` |
| `_README`: version, seed, domain/schema, scale, time, row counts, sheet mapping, planted chaos/drift | `test_readme_says_what_the_file_is`, `test_the_mapping_is_in_the_readme`, `test_readme_lists_the_planted_chaos`, `test_readme_lists_the_planted_drift`, `test_chaos_log_and_drift_plan_from_files_and_the_cli` (`generate -f excel --chaos-log --drift-plan`) |
| Styled frozen header, capped fitted widths, text format for identifier columns, date formats | `test_header_widths_freeze_and_date_formats`, `test_identifier_columns_stay_text_through_the_workbook` |
| Beyond 1,048,576 rows: clear error | `test_a_table_beyond_the_sheet_limit_is_refused_before_writing` (no split; documented in `docs/EXCEL.md`) |
| e2e: retail to a workbook, read back with the Step 1 reader, equal tables, ids still text | `test_retail_round_trips_through_a_workbook` (timestamps compared to the millisecond: a cell keeps ms) |

Behaviour change: `-f excel` used to write `<table>.xlsx` per table; it now writes one workbook (updated
`tests/cli/test_generation_cli.py::test_generate_excel`, `docs/GENERATION_ENGINE.md`). The per-table sink `write()` is unchanged.

### Open item for the lead (chaos e2e through the real `shape chaos`)
`shape chaos` and the ground-truth log live on `lane/ISS-gaps`, not on `build/main-plan` yet, so the committed
`_README` chaos test uses a log written in that log's documented format (`run` record, then `change` records). I
verified against the real command in a scratch merge of `origin/lane/ISS-gaps` (only `CHANGELOG.md` conflicted):
`shape chaos retail --scale small --seed 7 --corrupt duplicates=0.02@order --corrupt negative_amounts=0.05@order -o D`
then `write_workbook(..., chaos_log=D/_chaos_ground_truth.jsonl)` gave a `_README` listing 355 changes
(`order duplicates 100`, `order negative_amounts order_total 255`). Once ISS-gaps is merged, a test that runs
`shape chaos` and feeds `--chaos-log` should be added (not committed now: it cannot pass on this branch).
Drift: the answer-key format is read from `lane/ISS-diff`'s `ground_truth.json` (`start`, `days`, `events`), also unmerged.

## Checks
- ruff check + format --check (src tests plugins benchmarks/vs_spindle), mypy strict (333 files), vulture, lint-imports (1 kept), `check_user_facing.py` clean, `check_user_facing.py --wheel` on the pure wheel (it ships `io/excel.py`, `sinks/workbook.py`, `profile/reference/workbook.py`), `bandit -q -r src -ll` (no findings): all clean at Step 2 HEAD.
- START: `shape version` median 67 ms (min 63) under load (limit 300).
- Profile parity `verify.py --impl shape`, SHAPE_KERNEL=rust: exit 0; SHAPE_KERNEL=python: exit 0 (Step 1 code; Spindle baseline set up with `setup_spindle.sh`, datasets regenerated).
- Suites `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, Step 1 commit: rust and python each 4809 passed, 1 failed (`tests/io/test_readers.py::test_errors` asserted `.xlsx` is unsupported; fixed to `.docx` in 6cb8d73).
