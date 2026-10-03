# AUD-gen: audit and fix of the generation engine (lane/AUD-gen)

Status: **done; awaiting lead verification.** 73 findings in the area (one, #327, found while
fixing #193) and 3 outside it; every in-area defect is filed and all but the five listed under
"Left open" are fixed, each with a regression test committed first (failing output in its commit
message). Branch `lane/AUD-gen`, from
`int/INT-15` (f99563e). Area: `src/shape/generation/` and the tests that exercise it
(`tests/generation/` and every test file importing `shape.generation`).

No gate, tolerance or D-xx/T-xx decision was changed; §11 and §2.3 were not edited; `$SPINDLE_ROOT`
was not touched (it is not present in this container); no test was skipped, xfailed or weakened.

## Baseline (before any change)

* `pytest tests/generation -m "not emulator and not live" --cov=shape.generation`:
  1748 passed (with `plugins/shape-domains` installed, as CI does; without it 24 retail tests error).
  Line coverage of `shape.generation` from `tests/generation` alone: 82%. Modules with no line run by
  `tests/generation`: `batches.py`, `hierarchy.py`, `joint.py`, `joint_model.py`, `landing.py`
  (they are run by `tests/iss_gaps`, `tests/joint`, `tests/platform12`); `output.py` 59%,
  `relational.py` 56%, `future.py` 32%, `compiler.py` 60%.
* Determinism checked clean: a 220k-row schema over sequence, weighted_enum, normal, uuid, pattern,
  temporal, foreign_key, lookup and nulls is identical for `SHAPE_THREADS` 1, 3, 4 and 8,
  `chunk_rows` default, 0, 777, 1000 and 5000; `SHAPE_KERNEL=python` and `rust` agree except
  `normal` at about 1 ulp (documented). Early rules and streamed aggregates on and off agree.

## Findings

Severity: critical / high / medium / low. "Out of area" findings are filed only (brief): they are
in `src/shape/builtins/` or elsewhere. Issue numbers and fix commits are filled in as they land.

### High

| # | Where | Defect (reproduction → actual; expected) | Issue | Fix |
|---|---|---|---|---|
| 1 | `certificate.py:37-66` | `shape fidelity PROFILE.json DATA.csv` (a `.shape` profile as the reference) on 10 rows of `completely-different` against a 500-row 4-value profile → `passed: true, score 1.0, level gold`: only `null_count` and `mean` are compared and `None` vs `None` counts as a pass. Expected: fail, or refuse a reference that is not a capture. | #168 | e00153d |
| 2 | `engine.py:994-1025` | The copula runs after the compute phase and rule repair: `order.total = sum_children(order_line.amount)` with `correlated_columns` on `order_line [amount, cost, 0.9]` and rule `cost < amount` → 49/50 totals no longer equal the sum of their lines, 13 rows break the rule and `remaining_violations == []` (computed before the copula). Expected: totals match, rules hold or are reported. | #169 | 8891cc1 |
| 3 | `rules.py:288` | A temporal `cross_table` rule with `<=` (`order.placed <= customer.churned`) → `UFuncTypeError: ufunc 'multiply' cannot use operands with types dtype('<M8[us]') and dtype('float64')` from `Engine.generate()`. Expected: repaired (the `>=`/`>` branch handles temporal columns). | #170 | 8866e3b |
| 4 | `incremental.py:116` | `continue` on an `int8` column `[120,125,127]` → inserts `[-125, 124, -127, ...]` (`.astype` wraps); `uint8` `[250,252,255]` → `[17, 249, 3, ...]`. Expected: values near the input, within the type. | #171 | c3d84a4 |
| 5 | `incremental.py:112` | `continue` on `decimal128(5,2)` `999.99` → `0.00` for every value scaled above 999.99 (`cast(safe=False)`). Expected: values that fit the type. | #171 | c3d84a4 |
| 6 | `ddl.py:592,656-669,782` | Declared FK targets are matched case-sensitively: `CustId INT REFERENCES customer(id)` to `CREATE TABLE Customer (Id ...)` → generator `ref: customer.id`, no relationship, `generate()` raises `FK references non-existent table 'customer'`. Expected: SQL identifiers are case-insensitive. | #172 | d8e75ff |
| 7 | `ddl.py:242,509` | Bracket-quoted types (SSMS "Script Table as"): `[BuyerKey] [int] NOT NULL, [Amount] [decimal](18, 2)` → both `string` + faker. Expected: integer, decimal(18,2). | #173 | 52cfca9 |
| 8 | `ddl.py:223` | `ALTER TABLE [dbo].[Sales] WITH CHECK ADD CONSTRAINT [FK_x] FOREIGN KEY([BuyerKey]) REFERENCES [dbo].[Customers] ([CustomerKey])` (the SQL Server script form), `ALTER TABLE o ADD FOREIGN KEY (buyer) REFERENCES c(cid)` and `... REFERENCES c` (no column) → no relationship. Expected: the foreign key. | #174 | 9cc6745 |
| 9 | `ddl.py:653-690` | A single-column primary key that is also a foreign key (`customer_profile.customer_id INT PRIMARY KEY` with `customer(id)`, by name or declared) gets a skewed `foreign_key` generator: 500 rows, 85 distinct keys. Expected: a unique key. | #175 | dadd104 |
| 10 | `ddl.py:487`, `ddl_infer.py:460` | Columns of a composite `PRIMARY KEY (order_id, product_id)` without `NOT NULL` are nullable and get `null_rate 0.15` → 134 and 145 nulls in the key. Expected: primary-key columns are never null. | #175 | dadd104 |
| 11 | `ddl.py:869-885` | `from_ddl(..., scale="medium:customer=5000,order=7777")` (the documented `-s`) writes `scales.medium.order = 7777` but smart inference's `derived_counts` win: `order` gets 25000 rows. Expected: the override is the count. | #176 | 91e04a3 |
| 12 | `learn.py:441-445` | `shape learn` on a CSV of `01/01/1950`-style dates → the schema's temporal `start` is `'01/01/1950'`, and `shape generate` fails `temporal start '01/01/1950' is not an ISO date`; the range is compared as text (max `12/30/1972`). Expected: a schema that generates. | #177 | 67e5fca |

### Medium

| # | Where | Defect | Issue | Fix |
|---|---|---|---|---|
| 13 | `report/compare.py:470` | `shape fidelity t.csv t.csv` with an all-empty column → `Function 'count_distinct' has no kernel matching input types (null)`, exit 2. | #178 | 4239077 |
| 14 | `report/compare.py:256-260,499,513` | A table compared with itself scores below 100 and fails the default 85: constant column 85.71 (`std_ratio` 0/1e-9), all-null int 35.71 (cardinality 0/0 → 0), a column with `inf` 57.14. FIDELITY.md gives full marks for equal counts and equal std. | #178 | open: T-21 (h) arithmetic, for the lead |
| 15 | `fit.py:325-329,716-720` | `fit_schema(p, rows=N)` plans `null_count` and `row_count` as `preserved`, although the docstring says row-count dependent fields are then `approximate`. | #179 | 2698394 |
| 16 | `learn.py:337-350`, `fit.py:604-612` | A real `_row_id` column is overwritten by the surrogate key, and the plan calls its min and uniqueness preserved. | #180 | d26e375 |
| 17 | `fit.py:282-286` (cast in engine) | int64 values near 2^63 (and uint64 above it) generate as `-9223372036854775808` (float bound rounds to 2^63, `cast(safe=False)` wraps). | #181 | 12dd6fd |
| 18 | `learn.py:423-428` | `shape learn` writes boolean and integer enums without `output_type`: `flag` comes out `'True'`/`'False'` strings, `lvl` comes out `0.0`. | #182 | open: pinned by `test_learn.py`, for the lead |
| 19 | `distribution_fidelity.py:20-29` | `quantile_fidelity` passes when observed quantiles are NaN (`max` skips a NaN that is not first; order-dependent), and passes on an empty reference. | #183 | c1f8c98 |
| 20 | `drift_plan.py:452-454` | An event on a column that does not exist that day (added later, or dropped) raises `DriftPlanError: no column` even when its weight that day is 0; order dependent. | #184 | fcd2e52 |
| 21 | `engine.py:650-654,684` | `null_rate` is never applied to a column whose strategy returns a mapping (`composite_foreign_key`, `null_rate 0.5` → 0 nulls); the docs say the engine applies it after every strategy. | #185 | 824a120 |
| 22 | `engine.py:215-229,809-821`, `lookup.py:122-126` | `dependency_levels` ignores lookup sources, composite foreign keys and relationships, so parent and child share a level; with threads the parent is then generated once per thread plus once (`src` 300k rows: 1.5M rows generated with 4 threads). Output is the same; CPU and memory are not. | #186 | 9a16a40 |
| 23 | `engine.py:150-176` | `derived_counts` and row-count overrides are not checked: `ratio: "2"` gives a 100-digit row count (and `dry_run().ok` is true), a negative ratio or override gives `IndexError` at `engine.py:817`, `fixed: 10.0` is ignored; `per_year` with no `end` assumes 2025 (negative count → `IndexError`; `start > end` → 0 rows). | #187 | ff3431e |
| 24 | `engine.py:324` | Cutting a timestamp to `precision` truncates toward zero, so pre-1970 values round up (`1969-12-31 23:59:59.5` → `1970-01-01 00:00:00`) while later ones round down. | #188 | 55daf10 |
| 25 | `incremental.py:572` | `state_transitions` on an integer or boolean state column → `ArrowTypeError: Expected bytes, got a 'int' object`. | #189 | 5d541e5 |
| 26 | `incremental.py:389` | A time-zone-aware `as_of` is stamped as its wall clock (`12:00+05:00` → `12:00`), not UTC like the default. | #190 | e3f8678 |
| 27 | `incremental.py:686` | A reused `TimeTravelEngine` keeps `_high_water`, so the same input and seed give different keys on the second call. | #190 | e3f8678 |
| 28 | `batches.py:122` | `BatchGenerator` skips `Engine.finalize` when there is no post-pass: `output_type: decimal` comes out `double`, unrounded. | #191 | 555e944 |
| 29 | `rules.py:257,259,288` | `cross_column`/`cross_table` repairs are multiplicative: for zero, negative or small right-hand values (and integers) the repaired value still violates (`a < b` with `b = -10, 0, 0.004`: 2/3 rows still violate). | #192 | 3c1035b |
| 30 | `schema.py:502`, `compute.py:131,161`, correlations | Malformed rules (`cost < amout`, unsupported operators), computed columns with an unknown child table/column and correlations with unknown columns or `r = 7` are silently ignored; `validate()` reports nothing. | #193 | 53e9d68 |
| 31 | `compute.py:104` | `max_children`/`min_children` give `1970-01-01` (temporal) or `'0'` (string) for rows without children. | #194 | abad89a |
| 32 | `compute.py:109` | `lookup_parent` rounds a float parent value to 2 places (`0.5437…` → `0.54`); documented as a copy. | #194 | open: pinned by two tests, for the lead |
| 33 | `ddl_infer.py:768-796` | The seasonal transaction-date generator has no `range_ref`, so it ignores the model's date range (values from 2022). | #195 | 7c6b16b |
| 34 | `ddl_infer.py:199,516-541` | Table roles and derived row counts depend on the order of `CREATE TABLE` statements (order_items 12500 vs 6250 rows). | #196 | e0844ac |
| 35 | `ddl.py:379,527` | Splitting a body ignores string literals: `DEFAULT ')'` loses the next column; `DEFAULT 'a, b c'` adds a column `b`. | #197 | bff5fe6 |
| 36 | `ddl.py:328-366` | Comment markers and apostrophes inside quoted identifiers (`[a--b]`, `"x/*y"`, `[O'Neil]`) drop columns or whole tables; MySQL `#` comments are not stripped. | #197 | bff5fe6 |
| 37 | `ddl.py:457-472,487` | `NOT  NULL` (two spaces or a newline) → type `string` and nullable. | #198 | 17f25d8 |
| 38 | `ddl.py:509` | `INT UNSIGNED`, `DOUBLE`, `TIMESTAMP WITH(OUT) TIME ZONE`, `ENUM(...)` fall through to `string` + faker. | #199 | bf97552 |
| 39 | `ddl.py:399` | MySQL inline `KEY idx_a (a)` becomes a column named `KEY`. | #199 | bf97552 |
| 40 | `ddl.py:298,627` | Same-named tables in two schemas silently overwrite each other; `[dbo].[my.table]` becomes `table`. | #200 | f06b94f |
| 41 | `ddl.py:63-72` | Declared logical types are not kept: `BIT` → `double`, `BOOLEAN` → `'true'` strings, `TIME` → full timestamps, `DATE` → timestamps with a time. | #202 | c5bde83 |
| 42 | `ddl.py:665,771-783` | `REFERENCES ghost(id)` (table not in the file) keeps a `foreign_key` generator that fails validation. | #203 | ea23953 |
| 43 | `ddl.py:378` | `_paren_body` is quadratic on unclosed headers: `"CREATE TABLE t (" * 4000` (64 KB) takes 10 s. | #204 | bff5fe6 |
| 44 | `schema.py:324` | `GenSchema.from_dict` raises `KeyError: 'generation'` on a document the JSON Schema accepts. | #205 | 780c7e2 |
| 45 | `schema.py:400-539` | `validate()` misses unresolved references: FK `ref` without a dot, `composite_foreign_key.ref_table`/`derived.source` naming unknown tables, `scales`/`derived_counts` naming unknown tables, relationships with column lists of different lengths, unsafe column names. | #193 | 53e9d68 |
| 46 | `compiler.py:100-153` | `generate_relational` uses a numeric-shape column as the parent key (91 distinct float keys for 100 rows); a correlation overwrites its target's nulls and bounds while the report calls them preserved. | #206 | ec1e594 |
| 47 | `ddl.py:771` | One FK declared several ways gives duplicate relationships. | #203 | ea23953 |
| 48 | `learn.py:443-445` | Time-zoned timestamp columns lose their zone and are generated outside the observed wall-clock range. | #207 | 26934f8 |

### Low

| # | Where | Defect | Issue | Fix |
|---|---|---|---|---|
| 49 | `incremental.py:751` | Time travel tries only the first key column (`["code","n"]` with a string `code` fails). | #208 | c546ad4 |
| 50 | `incremental.py:289` | An all-null key column → `TypeError: int() argument ... 'NoneType'`. | #208 | c546ad4 |
| 51 | `incremental.py:361` | Empty delta tables lack `_shape_delta_type`/`_shape_delta_timestamp`. | #208 | c546ad4 |
| 52 | `incremental.py:454` | Transitions on a dotted table name (`dbo.orders.status`) are refused. | #208 | c546ad4 |
| 53 | `reference.py:77` | A reference JSON that is an object or a string loads as its keys or characters; invalid JSON names no file. | #209 | 1409b07 |
| 54 | `fanout.py:44-60` | `concentration_weights` silently misses an unreachable target and `two_tier` inverts the ranking. | #210 | ae493c9 |
| 55 | `relational.py:75` | `generate_fk_indices(skew>0)` never picks the last parent (unused helper). | #210 | ae493c9 |
| 56 | `timeline.py:50` | `generate_range(0, 0.3, 0.1)` drops the end point (float accumulation). | #210 | ae493c9 |
| 57 | `permutation.py:59` | `permute` with an index out of range loops forever. | #210 | ae493c9 |
| 58 | `engine.py:653` | A strategy returning an empty mapping → `RuntimeError: generator raised StopIteration`. | #211 | b1855c6 |
| 59 | `levels.py:47` | `assess_fidelity(set())` returns `bronze`. | #211 | b1855c6 |
| 60 | `output.py:89-91` | `fmt="summary"` is refused with a message that lists `summary` as a choice. | #211 | b1855c6 |
| 61 | `strategies.py:131` | `GenerationPlan` seeds `s` and `-s` give the same row 0. | #211 | b1855c6 |
| 62 | `joint_model.py:115` | NaN level of a numeric column is never used (`dict.get(nan)`). | #212 | 4bd28e4 |
| 63 | `arrowkit.py:46-51` | Docstring says the result equals `pyarrow.array`; with a mask NaT/NaN become null. | #212 | 4bd28e4 |
| 64 | `fit.py:213-231` | decimal128 columns never get a scale (min/max are strings in the profile). | #213 | 516f451 |
| 65 | `fit.py:749` | A NaN correlation becomes a -0.999 copula; `None` → `TypeError`. | #213 | 516f451 |
| 66 | `learn.py:239-241` | `guess_provider` matches suffixes inside words (`ethnicity` → city, `membership` → ipv4). | #214 | 0446eba |
| 67 | `drift_plan.py:340` | A ramp longer than its window reports a full-effect date the event never reaches. | #215 | 2517ede |
| 68 | `ddl.py:450-491` | Keyword tests read string literals (`DEFAULT 'NOT NULL'` → not nullable, `DEFAULT 'PRIMARY KEY'` → key). | #197 | bff5fe6 |
| 69 | `ddl.py:881` | Negative or unknown-table scale overrides are accepted and the schema cannot be reloaded. | #217 | aeefaf1 |
| 70 | `ddl_infer.py:719-721` | `_is_placeholder_enum` checks `{type_a, type_b}` but the template is `{type_a, type_b, type_c}`; the `*_type` upgrade is dead. | #218 | 35469e8 |
| 71 | `ddl_infer.py:927-964` | CR-02 depends on column order; CR-05 pairs unrelated `net_`/`gross_` words. | #218 | 35469e8 |
| 72 | `schema.py:271` | `to_dict` fails on inline `AddressReference` rows. | #205 | 780c7e2 |

| 73 | `ddl_infer.py:1180-1215`, `rules.py` | Smart inference writes range rules as `x BETWEEN a AND b` and BR-04 as `order_date >= order_date` (no tables): the rules engine checks neither (found by the #193 checks). | #327 | 1236a49 |

### Out of area (filed only)

| # | Where | Defect | Issue |
|---|---|---|---|
| O1 | `builtins/strategies/temporal.py:131` | Seasonal: the end day is almost never drawn when a profile names months outside the range (`[2045, 2053, 2010, 2092, 7]` for days 1/2/15/30/31). | #219 |
| O2 | `builtins/strategies/temporal.py:107` | Seasonal `month: [..]` as a list → `AttributeError`; month names other than three-letter ones are dropped. Related: #149. | #219 (month names: #149) |
| O3 | `builtins/strategies/keys.py:109` | `foreign_key` fails on an empty child of an empty parent (`table 'order' has no rows`). | #220 |
| — | `builtins/strategies/temporal.py` | Seasonal ignores the time of day of `start`/`end`: already #129. Zero-padded hour keys: already #134. Spec `KeyError` without location: already #138. | — |

### Improvements, not defects

* Duplicated column loop in `Engine.generate_chunk` and `Engine.generate_column` (`engine.py:647-658`, `678-691`).
* `iter_chunks(t, 0)`/`generate_table(..., 0)` use the default size (`chunk_rows or ...`), so the `size < 1` check is unreachable for 0.
* Dead code: `fit._is_covered` (duplicates `learn._is_covered_enum`), `vectorized.composite_keys(prefix=)`, `relational.generate_fk_indices`/`generate_composite_keys`/`materialize_composite_fks` (unused), `_ParsedColumn.default`/`raw_type` in `ddl.py`, ddl_infer branches for logical types the parser never emits.
* Row-count overrides for unknown table names are ignored silently; `generate_chunk("nope", ...)` raises a bare `KeyError`.
* `group_sums` wraps on int64 overflow; `HierarchicalSampler` does not check equal field lengths.

## Left open, and why (for the lead)

| Issue | What is left | Why it is not fixed here |
|---|---|---|
| #178 (second half) | A column compared with itself scores below 100 when it is constant (85.71), all null (35.71) or holds `inf` (57.14), so such a table fails the default 85. | `docs/FIDELITY.md` says these scores equal the T-21 clause (h) comparator's, and `fidelity_1to1` holds Shape to it; changing the arithmetic is a T-21 decision. The crash half (#178, all-empty column) is fixed. |
| #182 | `shape learn` writes integer and boolean value sets without `output_type` (`flag` comes out as `'True'`/`'False'`, `lvl` as floats); `fit` already adds it. | `tests/generation/test_learn.py::test_d2_schema_equals_the_baselines_except_the_listed_columns` and `::test_every_column_that_is_not_listed_is_equal` pin every learned generator equal to the baseline's (d2.age, has_promo, is_active, qty, store_id would differ). The fix (a `typed_enum` difference in `learn.DIFFERENCES` and `learn_1to1/compare.py`) was written, run and reverted. |
| #194 (second half) | `computed` `lookup_parent` rounds a float parent value to 2 places; GENERATION_STRATEGIES.md calls it a copy. | `test_gen_compute_copula.py::test_lookup_parent_copies_from_the_parent` and `test_strategies_p404b.py::test_computed_lookup_parent_copies_the_parents_value` assert the rounding. The min/max half is fixed. |
| #175 (rest) | A bridge table's composite primary key (`order_id, product_id`, both foreign keys) can repeat a pair (219 distinct pairs of 1000 rows). The null and one-to-one halves are fixed. | Unique pairs need a without-replacement pair sampler (a new strategy or a `composite_foreign_key` mode): a design choice, not a local fix. |
| finding 36 (rest) | MySQL `#` line comments are not stripped (`# comment CREATE TABLE ghost (x INT)` creates `ghost`). | `#` also starts SQL Server temporary table names (`#tmp`); telling the two apart needs the dialect, which `from_ddl` does not take. |
| #219, #220 | Seasonal end day / list profile; `foreign_key` on an empty parent. | Outside this lane's paths (`src/shape/builtins/`): filed only. |

## Changes outside `src/shape/generation/` and its tests

* `benchmarks/vs_spindle/ddl_1to1/differences.py`: F12 to F17, the listed differences the fixes
  #173, #176, #202, #195, #218 and #327 make against the baseline's DDL import (the mechanism P4-01b
  and ISS-gen used for F1 to F11). `ddl_1to1/verify.py` exits 0 (28/28) after each.
* `docs/GENERATION_ENGINE.md`, `docs/GENERATION_STRATEGIES.md`, `docs/FIDELITY.md`, `docs/DRIFT.md`:
  the behaviour the fixes changed. `CHANGELOG.md`: a "Fixed (generation engine audit)" list.
* Nothing in `.github/workflows/`, §11 or §2 of the plan, or `$SPINDLE_ROOT` was edited.

## Equivalence and byte identity

* Retail (both modes, `small`, seed 1042) hashes the same as on `int/INT-15` (f99563e) after every
  fix that touches the engine, rules, compute or keys: `53cbe433b1b9...` (script in the session
  scratchpad: every table's Arrow IPC bytes, sorted by name). No fix changes the bytes T-21's
  verifier compares.
* `ddl_1to1/verify.py`: exit 0, 28/28 (F12 to F17 listed).
* `learn_1to1/verify.py d2 mt d1`: exit 0, 0 unexplained differences.
