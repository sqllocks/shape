# ISS-gen: owner issues about generation (lane/ISS-gen)

Status: **built, lead decisions of 2026-10-02 applied; awaiting lead verification.** Issues #9, #10, #11, #12, #17, #18, #19, #24 (generation
side), #25 and #26. Every issue was reproduced on this branch first; the evidence is below. No gate,
tolerance, D-xx or T-xx decision was changed, and no test was skipped or disabled. Issues were not
commented on, labelled or closed. Two perf lanes edit generation hot paths: this lane stays in strategy
semantics and validation (see "Hot paths").

## Reproduction on `lane/ISS-gen` (built from `build/main-plan` 4051235) and result

| # | Reproduced? | Evidence (this branch, before the fix) |
|---|---|---|
| 9 | yes | `validate()` returned `[]` for `{"distribution": "log_normal", ..., "scale": 2, "sigmaa_typo": 9}`; values `[75.3749..., 49.0039..., 80.3400...]` (14 decimals). |
| 10 | yes | `uniform` with `start == end` raised `StrategyError: temporal range must end after it starts`; `uniform` was `[start, end)` and the seasonal/day-weighted path included the end day. |
| 11 | yes | 50 domains, `tutanota.com, icloud.com, inbox.com, hotmail.com, ...`; `company_email` ended in `.com`; `uri` used a pool of `.com/.org/.dev` hosts. |
| 12 | yes | areas 1 to 898 for 3,000 values, every one assignable. |
| 17 | yes | `ValueError: the address strategy needs spec['reference'] rows`, with and without `scope`. |
| 18 | yes (by reading and by the cause) | each `field` column seeded its own draw from `(seed, table, column, chunk)`. The issue's repro needs the GeoNames file; the new test uses the shipped dataset and checks 2,000 rows instead. |
| 19 | yes | `AddressReference` rows in a spec failed `json.dumps` in `GenSchema.from_dict`; the loader's `(locations, provenance)` was rejected; 3 and 59,000 rows each paid the reference parse (the strategy used a pure-Python `AddressPack` per chunk, seeded per chunk). |
| 24 (generation) | yes | `shape from-ddl s.sql -o schema.json` then `Engine(...).generate()`: `total: double`, `created_at` with microseconds. (The profiler side is lane ISS-profile's.) |
| 25 | **partly fixed already** | P4-10 made `shape generate SCHEMA.json` work (the issue's `unrecognized argument` is gone). Still missing: `--rows TABLE=N` and `--out`; `--rows N` with no target still printed an unlabelled demo table. |
| 26 | yes | `shape plan schema.json` ended in `AttributeError: 'list' object has no attribute 'get'` (`_legacy_plan`). The line in the issue (`fidelity.py` 102) moved to 184. |

## What was done, per issue

### #26 and #25 (CLI)
- `shape plan SCHEMA.json` accepts the schema `from-ddl` writes and prints the plan of its run (what
  `generate --dry-run` plans). Evidence documents (`.shape`, JSON) go the old way; a document whose
  `relationships` is a list but that is not a generation schema now raises a one-line `ValueError`.
- `main()` called as the program (`main(None)`, the console script) turns any unexpected exception into
  `shape: error: <Type>: <message> (run with --debug for the traceback)` and exit 2; `--debug` or
  `SHAPE_DEBUG=1` lets it propagate. A call with an argv list (library and tests) always propagates:
  the first version of the handler caught everything and broke
  `tests/streaming/test_cli.py::test_a_killed_run_resumes_...`, which expects the `RuntimeError`.
- `shape generate SCHEMA.json --rows TABLE=N --rows TABLE=M --out DIR` (repeatable; `--out` is an alias of
  `-o`). A bare `--rows N` with a schema is an error that says so; with no target it still prints demo
  rows, now with `shape: demo rows, not generated from any schema` on stderr. `--rows N` with `--from`
  is unchanged. I did not remove the demo output, because `tests/cli/test_product_cli.py` and
  `tests/cli/test_generation_cli.py::test_demo_rows_still_work` (the shipped early-access CLI, §6.2.7)
  assert it; the owner can decide to drop it.
- Tests: `tests/cli/test_iss_gen_cli.py`.

### #24 (generation side): the declared type is kept
- `from-ddl` writes `output_type: "decimal"` for `DECIMAL(p,s)` and `"timestamp"` for `DATETIME` (3
  fractional digits) and `DATETIME2(n)` (n < 6) columns; the engine casts those columns on the finished
  table (`Engine.finalize`: `generate()`, `iter_chunks`, the streamed `on_batch`/`on_table`), so computed
  columns, rule repair and the copula still see numbers. `decimal128(p,s)` values are rounded to `s`; a
  value that does not fit `p` is a `ValueError` naming the column (an unsafe cast would wrap to 0.00).
  `from-ddl` cuts a distribution's `max` to what the type holds (`DECIMAL(3,1)` generated up to 100 and
  could never have been written to the table).
- The domains (66 declared-precision decimal columns in the shipped schemas) are not changed: the keys
  are opt-in through `output_type`, so domain output and T-21 are untouched.
- Tests: `tests/generation/test_iss_gen_types.py`; two existing assertions in `test_ddl.py` and
  `test_ddl_fixes.py` now include `output_type`.
- Harness: `ddl_1to1/differences.py` gets **F9** (the declared DECIMAL type is kept; a distribution's
  `max` cut to the type). `ddl_1to1/verify.py` exits 0, 28/28.

### #10 temporal
- A date `end` (`2026-05-01`) stands for the whole day for every pattern, so the end day is a possible
  day and `start == end` is one single day. An `end` with a time is the exact, exclusive bound (as
  before). Errors say which case: `end 2026-04-30 is before the start 2026-05-01`, or `end equals the
  start ... and the end is an exclusive time: give dates`.
- This **changes uniform values**: `uniform` was `[start, end)` over dates and is now `[start, end + 1
  day)`. It is the reading the issue calls natural; it moves the uniform range by one day, and the T-21
  clauses (KS on the date columns) still pass (domain verify below). Two assertions that encoded the
  old asymmetry in `test_strategies_p404c.py` were updated ("the seasonal range includes its end date;
  the uniform one stops before it"; the end-before-start and equal-time messages).
- Tests: `tests/generation/test_iss_gen_temporal.py`.

### #11 and #12 identifier providers
- Defaults (all `native` and `faker` providers): `email` at `example.com`, `.org`, `.net`;
  `company_email` at `<company>.example`; `uri` at those hosts; `ssn` areas 900 to 999 (never assigned);
  `phone_number` `(AAA) 555-0100` to `555-0199` (reserved for fiction).
- Opt-in: `"domains": "realistic"` (`email`, `company_email`, `uri`) and `"range": "assignable"` (`ssn`,
  `phone_number`) give the old values; the docs warn that they can be real people's. Any other value is a
  `StrategyError` naming the column.
- **Standing decision of 2026-10-01 applied:** each deliberate difference is recorded with its reason in
  named allow-lists, each narrow and self-checking:
  - `benchmarks/vs_spindle/strategy_1to1/identifier_differences.py`: the equivalence cases in
    `cases_p404b.py` generate Shape's side *with the opt-in*, so T-21 clauses (b)-(e) still compare the
    realistic path with the baseline's fixtures (`baseline_p404b.py --check` still matches); the file
    holds the reasons, and `tests/generation/test_iss_gen_identifiers.py` proves the defaults are the
    reserved values.
  - `benchmarks/vs_spindle/domain_1to1/domain_differences.py` + `verify.py`: `retail customer.email`
    may fail **only** the `vocab` check, and only if every domain is reserved; a stale entry fails the run.
    `verify.py --impl shape` exits 0, 60/60, with that one deliberate column marked.
- The pools files are untouched (`test_shape_pools_are_the_baselines` pins them).
- Not changed, for the owner: `ipv4` (public addresses, no person), the masking transform
  `builtins/transforms/_mask_values.py` (it still replaces e-mail with real mail providers: the same
  harm for `shape mask`; it has its own baseline harness `mask_1to1`), and the optional `faker`
  package's own providers (`free_email`, `ssn`, `credit_card_number`, ...).
- Tests: `tests/generation/test_iss_gen_identifiers.py`; the shape test in `test_strategies_p404b.py` now
  checks the old shapes under the opt-in.

### #9 unknown generator keys
- `GenSchema.validate()` warns for every key a built-in strategy does not read, with a "did you mean"
  hint, and says "`scale` is a column property" for `scale`, `null_rate`, `precision`, `max_length`,
  `nullable`, `type`. Nested `params` are checked for `distribution` and the strategies that have them.
  The sets are in `shape/generation/spec_keys.py`; a test reads every strategy module for the keys it
  reads and fails if one is missing there, and another keeps the family table equal to `FAMILIES`
  (`generation` may not import `builtins`, hence the copy). A plugin strategy or distribution family is
  not checked. Seven strategies that were missing from `STRATEGY_REQUIRED_KEYS` (`address`,
  `bootstrap`, `constant`, `choice`, `empirical`, `normal`, `uniform`) no longer warn "Unknown strategy".
- The check found a real bug: `from-ddl` wrote `max_nb_chars` at the top of `faker` text generators,
  where the strategy ignores it (the provider's own default of 200 was used). It is now
  `args: {"max_nb_chars": n}` (harness entry **F10**, `ddl_1to1/verify.py` 28/28).
- Findings **not** changed (they predate this lane and are baseline behaviour): `from-ddl` writes
  `null_rate: 0.15` into nullable foreign-key *generators*, where it is ignored (the column's
  `null_rate` stays 0), so those schemas now warn "`null_rate` is a column property". Proposal: write it
  on the column and record it as a DDL difference. The imported baseline schemas under
  `benchmarks/vs_spindle/fixtures/schemas/` carry `round`, `weight_field` and `type` in generators,
  which are ignored and now warn.
- Tests: `tests/generation/test_iss_gen_spec_keys.py`.

### #17, #18, #19 address
- `address` generates from a scope alone: `{"strategy": "address", "scope": [{"state": "WA"}]}`. Scopes:
  `{"state"}`, `{"postal_code"}`, `{"city", "state"}`, `{"county", "state"}`, `"WA"`, `"98101"`,
  `"Seattle, WA"`, with `weights` and `exclude` (`scope_from_specs` ignored `exclude` when there were no
  weights: fixed; `location_from_spec` takes a state code and a city without a state; the file has CRLF
  line ends, kept).
- The reference places default to the dataset `us_zip_locations` that `sqllocks-shape-domains` already
  ships (D-10: 40,977 ZIPs with city, state and coordinates, GeoNames attribution in
  `THIRD_PARTY_NOTICES.md`). Core still bundles no data. Without the package the error says
  `pip install sqllocks-shape-domains`. **I did not add a second data package** (the issue offers
  "ship a pack or a `shape-geo` package"): D-10 fixes where the ZIP data lives, and a second copy would
  need an owner decision. A `shape-geo` split is a possible follow-up.
- #18: one place is drawn per row from streams keyed `(seed, table, group, row)`; every address column
  of a table with the same `group` (default `address`) agrees, for any chunking and in any column order;
  a different `group` gives an independent address. `field` accepts `zip`, `lat`, `lng` aliases.
- #19: `reference` is `{"dataset": name}` (small schemas), rows (dicts, `AddressReference`, `Location`) or
  the loader's `(locations, provenance)`; `GenSchema.from_dict` turns dataclass rows into dicts;
  the reference and scope are compiled once per engine (`engine.cached`); a reference without streets
  draws a name and a suffix from Shape's street pools. The strategy is vectorized (59,000 addresses in
  0.08 s, was 1.9 s with 41k rows). `AddressPack` and `FastAddressPack` are unchanged.
- Behaviour that changed: the default (no `reference`) used to be an error; the old output of an inline
  reference differs (the draw is now row addressed, not seeded per chunk).
- Tests: `tests/generation/test_iss_gen_address.py`; one assertion of
  `tests/builtins/test_builtins_behaviour.py::test_address_strategy` (the "needs reference" error) was
  replaced with an unknown dataset.
- Not done: street names are not per reference place when the reference has none (the issue's point 5
  asks for the pool; that is what is drawn, independent of the place).

## Decisions of the lead (2026-10-02) and how they were applied (nothing here changes a D-xx or T-xx)
1. `uniform` temporal now includes a date `end` (above): **kept as built.** No one-day `"date"` shortcut.
2. `shape generate --rows N` demo path (labelled): **kept as built.**
3. **`from-ddl` FK `null_rate`: applied.** `ddl_infer._fk_distributions` sets `col.null_rate = 0.15` on
   a nullable foreign key (the engine reads it there) and no longer writes it into the generator. Test:
   `tests/generation/test_iss_gen_fk_null_rate.py` (the column has 0.15 and the generator has none; 20,000
   generated rows have a null rate of 0.15 +- 0.02, a NOT NULL FK has none, and every non-null value is
   a parent key; `validate()` no longer warns about it). `ddl_1to1/verify.py` differed from the baseline
   in 13 columns of 5 cases (the generator key, and the column's `null_rate`), so the harness has a
   new entry **F11** with exactly those 13 columns x 2 paths (smart mode); `verify.py` exits 0, 28/28.
   An assertion in `tests/generation/test_ddl.py` that encoded the inert key was updated.
4. **`shape mask` e-mails: applied.** `_mask_values._email` draws the domain from `example.com`,
   `.org`, `.net` (RFC 2606). Test: `tests/builtins/test_mask.py::test_replacement_emails_use_reserved_example_domains`.
   The `mask_1to1` harness gets the probe `_email_domain_probe` and **MASK-A6**: every Shape replacement
   e-mail must be at a reserved domain (no allowance), and an allow-list `baseline_real_email_domains`
   names the columns where the baseline writes other domains. **Finding:** that list is empty for both
   cases: the pinned baseline's faker (40.40.0) already writes `example.com/.org/.net` addresses, so for
   mask this is a change from Shape's own earlier behaviour (real mail providers), not a difference
   from the baseline. The probe is self-checking (it fails if the list and the output disagree). The
   other checks are unchanged and pass: format preserved, same value -> same replacement, no original
   value written back (and the four negative controls are caught).
5. **GeoNames licence: applied, CC BY 4.0 everywhere.** Checked 2026-10-02 at
   https://download.geonames.org/export/zip/readme.txt: "This work is licensed under a Creative
   Commons Attribution 4.0 License." The readme's own "see" link still names the 3.0 URL
   (`creativecommons.org/licenses/by/3.0/`); the text, which is the statement of the licence, says 4.0.
   https://www.geonames.org/export/ (and /about.html) say only "creative commons attribution license"
   with no version. Changed: `load_geonames_postal` provenance (`CC-BY-4.0`, was `CC-BY-3.0`),
   `docs/EXTERNAL_REFERENCE_ASSETS.md`, `THIRD_PARTY_NOTICES.md` (now cites the readme URL and the date and
   notes the stale link), and the assertion in `tests/location/test_reference.py`. `plugins/shape-domains/README.md`
   already said CC BY 4.0. **The plan's D-10 text (line 201) and §12 (line 2247) say CC-BY-4.0, so
   they agree with this: nothing to report to the lead and the plan was not edited.**
6. **`faker` in the `dev` extra: applied** (`faker>=24`; not core, not `advanced`). Core imports it
   only lazily inside `providers._faker_pool` (`importlib.import_module("faker")`, with an install
   hint on `ImportError`); nothing else in `src` imports it at module level. The two `faker` tests in
   `test_iss_gen_spec_keys.py` ran in this session: 14 passed, none skipped.

## Hot paths
No change to `engine.py` hot loops: `_column` returns as before; the new code is `finalize` (called on
batches and tables that leave the engine, a no-op unless a column declares `decimal`/`timestamp`),
`_declared_callbacks` and a branch in `_generate`. Strategy files touched: `temporal.py`, `providers.py`,
`address_rows.py` (new), `__init__.py` (the `address` class). Expect small merge work with
`lane/P6-01-perf-r4` in `engine.py` and `temporal.py`.

## Results (this session, on the merge of origin/build/main-plan 7d94089 and the decisions above)
The container was new: `$SHAPE_VENV`, the pinned Spindle checkout (`setup_spindle.sh`), `-e plugins/shape-domains` and
`-e ".[dev,advanced]"` (with `faker` from the `dev` extra) were installed first. The merge had one
conflict, `src/shape/cli/generation.py` `cmd_generate`: both sides kept (my `_rows_arg`/`--rows TABLE=N`
and the new `--scale-mode` branch; `--scale-mode` with `--rows` still raises through `run_scale`).
- ruff check and format --check (src tests plugins benchmarks/vs_spindle) clean; mypy strict, 332 files, no
  issues; vulture (`src/shape scripts/vulture_whitelist.py --min-confidence 80`) clean; lint-imports 1 kept, 0 broken;
  `check_user_facing` clean; `bandit -q -r src -ll` shows only the existing nosec warnings. No Rust change.
- START: median of 10 runs of `shape --version` 45.2 ms (gate 300).
- `pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`: SHAPE_KERNEL=rust
  4849 passed (507 s); SHAPE_KERNEL=python 4849 passed (835 s). `pytest -m heavy --ignore=tests/demo/fabric`:
  42 passed.
- Harness, run directories deleted first: `strategy_1to1/baseline.py`, `baseline_p404b.py` and
  `relational_baseline.py --check` all match; `domain_1to1/verify.py --domain retail --scale small --impl shape`
  60/60 PASS; `ddl_1to1/verify.py` 28/28 apart from the listed differences (F1 to F11), exit 0;
  `mask_1to1/verify.py` d2 (1,000,000 rows) PASS and cat PASS, exit 0.
- Not run: `tests/demo/fabric` (needs unixODBC and nbformat), emulator and live tests.
