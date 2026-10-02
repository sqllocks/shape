# Spindle retail domain: 1:1 vectorized port and fair benchmark

This directory holds a numpy + pyarrow port of Spindle's `retail` domain. It reproduces
the same 9 tables, 60 columns, row counts, column order, Arrow/Parquet types, per-column
generation semantics, compute phase and business-rule fixing. It also holds a verifier
that measures how close the port is to Spindle, and a benchmark harness that times both
tools under the same conditions.

| file | what it is |
|---|---|
| `port.py` | `generate(scale='medium', seed=42, spindle_root=...) -> dict[str, pyarrow.Table]` and `write_parquet(tables, dir)`. Uses only numpy, pyarrow and the standard library. Retail only. |
| `generate.py` | Writes one run of a domain as Parquet, for `--impl spindle\|reference_port\|shape`, into `$BENCH_OUT_DIR/<impl>/<domain>/<scale>/seed<N>/`. Each impl runs in its own venv. |
| `verify.py` | Equivalence verifier (T-21 clauses (a)-(h)); reads Parquet only, in the Spindle venv. Tables, FKs and business rules come from `../dump_schema.py`. Exits 1 unless every clause holds, 2 if a required run directory is missing. |
| `export_retail.py` | Writes the `shape-domains` plugin's retail data (the schema, from the baseline's dump, and the four reference datasets, from the baseline checkout); `--check` proves the shipped files equal what it would write. |
| `export_domains.py` | The same for every non-retail domain (`capital_markets` to `telecom`; 3nf and star schemas, every reference file of the baseline's domain); `--check` proves the shipped files equal what it would write. One documented schema difference (`OVERRIDES`). |
| `bench.py` | Benchmark harness: every run is a fresh process (median of `--runs`), timing generate + write through `generate.py`. |

The recorded results quoted below (`verify_*` reports for small, medium and large, the
benchmark JSON, and the seed study) are kept in `benchmarks/baselines/2026-09-29/`. New runs
write to `$BENCH_OUT_DIR`, never into this directory.

## How to run

Set up the pinned Spindle and the venvs once (`docs/plans/COMPLETION_PLAN.md` section 1), then
run everything from the repository root:

```bash
source scripts/env.sh

# one run of one impl (port only: any env with numpy + pyarrow)
"$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 1042

# equivalence: Spindle seed 42 vs the impl at seed 1042, with Spindle seeds 43-46 as the
# self-baseline. Missing run directories are generated first (~3 min at medium).
"$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py --domain retail --scale medium --impl reference_port

# benchmark (holds the exclusive lock $BENCH_OUT_DIR/bench.lock)
"$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/bench.py \
    --impl reference_port --domain retail --scales medium,large --runs 3

# everything, in order (verifiers, benchmarks, re-verification of the timed output)
python benchmarks/vs_spindle/run.py --quick
```

The seed set is fixed by T-21 (Spindle 42 as the reference, 43-46 as the baseline, the impl at
1042) and `verify.py` has no option to change it. `spindle_root` defaults to `$SPINDLE_ROOT`.
The port reads that checkout at runtime and never writes to it (bytecode writing is disabled
while `names.py` is loaded). Nothing from Spindle is copied into this repo.

## What the port reads from Spindle at runtime

* **Schema, weights and parameters.** The port reads the `schema_dict` literal from
  `RetailDomain._build_schema` in `domains/retail/retail.py`. It parses it with `ast` and
  evaluates it with a stub `self` whose `_dist` and `_ratio` resolve against
  `domains/retail/profiles/default.json`, exactly as `Domain._dist` and `Domain._ratio` do.
  Every weight, distribution parameter, null rate, scale preset, derived count, relationship
  and business rule therefore comes from Spindle's own files. Nothing is re-typed.
* **Row counts.** These come from the same `calculate_row_counts` logic: scale presets, then
  `fixed`, `per_parent × ratio` and `per_year × years`. At medium that gives customer
  50,000; product_category 50; promotion 200; store 150; address 75,000; product 5,000;
  order 500,000; order_line 1,250,000; return 85,000. The total is 1,965,400. At large it
  is 19,625,400. Both are verified identical to Spindle.
* **Native provider pools.** `FIRST_NAMES`, `LAST_NAMES`, `STREET_NAMES` and
  `EMAIL_DOMAINS` come from `engine/data/names.py`. `_US_CITIES`, `_US_STATES` and
  `_STREET_SUFFIXES` come from `engine/strategies/native.py`, extracted with `ast`. These
  are the arrays `NativeStrategy` samples from, which is where `faker` delegates.
* **Reference data.** `categories.json`, `product_names.json`, `promo_names.json` and
  `us_zip_locations.json` (40,977 ZIP records) are found with the same search order as
  `reference_data._load_dataset`.
* **Engine behaviour replicated.**
  * Table order: `DependencyResolver` (Kahn's algorithm with a sorted queue) plus
    `_group_by_dep_level`.
  * Per-table column order: `TableGenerator._order_columns` (PK, then FK, then independent,
    then dependent, then computed). The output column order therefore matches Spindle, for
    example `product.cost` comes after `product_status`.
  * Per-table child RNG: `seed ^ sha256(table)[:8]`.
  * `Strategy.apply_nulls`.
  * `apply_compute_phase`: `order_total = round(sum(line_total), 2)`, and 0 for orders
    without lines.
  * `BusinessRulesEngine.fix_violations`, applied in schema order and on the same data
    Spindle applies it to.

## Equivalence results (medium, `retail_verify_medium.txt` in the baselines)

**Method.** Spindle (seed 42) is the reference. The port runs at seed **1042**, so no RNG
stream is shared with Spindle. Four more Spindle seeds (43–46) measure how far Spindle
drifts from *itself* between seeds. A port column counts as equivalent when every check
passes:

* the Arrow type is identical, where `large_string` counts as `string`;
* the null rate is within 5σ, or within 1.5× Spindle's own seed-to-seed drift;
* for numeric and datetime columns, KS ≤ max(the KS critical value at α = 0.001,
  1.5 × Spindle's max seed-to-seed KS + 0.002);
* for low-cardinality categoricals, total variation distance (TVD) ≤ max(3 × multinomial
  noise, 1.5 × Spindle's own TVD + 0.002), and vocabulary overlap ≥ 0.999;
* for high-cardinality strings, vocabulary overlap ≥ 0.999 against Spindle's output plus
  its pools, and the distinct-count ratio is within tolerance. For email, street and
  store_name the check is at component level: every row must parse into pool members, for
  example `first.lower + "." + last.lower + suffix(1..998) + "@" + EMAIL_DOMAINS`.

**Result: 60 / 60 columns equivalent at medium.** No columns are flagged. Small is also
60 / 60 (`retail_verify_small.txt`), and so is large (`retail_verify_large.txt`).
Result files:
* `retail_verify_<scale>.json` and `.txt` in `benchmarks/baselines/2026-09-29/`: full statistics
  per column, top-10 frequencies side by side, mean/std/min/max, FK, fan-out, coherence and
  rule checks, from the earlier retail-specific verifier.
* `retail_verify_medium_sameseed.json`: the port at seed 42, which shows the bit-identical
  columns.
* A current run of `verify.py` writes `$BENCH_OUT_DIR/verify/<impl>_<domain>_<scale>.json` and
  `.txt`. It derives pools, component rules and cross-table checks from the dumped schema
  instead of retail code, so its report has a few different sections (record coherence and
  strategy semantics replace the retail-only rule checks; clause (h) is now asserted).

**Structure.**
* All 9 tables match Spindle: same names, same table order, same column names in the same
  order, identical row counts and identical Arrow types.
* The Arrow types are: int64; string; float64; bool; `timestamp[ns]` for `signup_date`,
  `start_date` and `end_date`; `timestamp[us]` for `order_date` and `return_date`, as
  Spindle's pandas code produces them.
* Nullable integer foreign keys are `object` columns in Spindle and `int64` with nulls in
  the port. Both are written to Parquet as `int64`.

**FK integrity.** Every foreign key in both outputs is 100% valid. That covers 11 FKs,
including the self-reference `parent_category_id` and the looked-up
`order_line.promotion_id`.

**FK fan-out.** Children-per-parent distributions for all 10 parent→child relationships
fall within Spindle's own seed-to-seed range: p50/p90/p99/max, zero-child fraction, and KS
on the count vectors. Examples: orders per customer is 8/15/50/50 in Spindle and 7/15/50/50
in the port, with `max_per_parent` = 50 enforced. Lines per order is 2/5/7/13 against
2/5/7/12, and 8.3% (Spindle) and 8.2% (port) of orders have no lines.

**Address coherence.** In both outputs, 100% of `(city, state, zip)` tuples exist in
`us_zip_locations`. `lat`/`lng` also equal the ZIP record 100% of the time. The state mix
matches uniform-over-ZIP-records sampling, with TVD 0.009 in both.

**Business rules.** Spindle's own `BusinessRulesEngine.validate` reports 0 violations on
both outputs. Every row passes these additional checks in both:
* `order_total == sum(line_total)`
* `return_date > order_date`
* `order_date >= signup_date`
* `refund <= order_total`
* `cost < unit_price`
* `line_total > 0`
* `order_line.unit_price == product.unit_price`
* `order_line.promotion_id == order.promotion_id`
* `discount == promotion.discount_pct` (or 0)
* the `line_total` formula
* promotions last 3–30 whole days
* `is_primary` is the first address per customer
* the shipping address belongs to the order's customer, and is NULL exactly when the
  customer has no address
* the category level/parent hierarchy is consistent (8/21/21)
* the email is built from the row's own first and last name

Side effects of the rule fixing also match. About 49.4% of order dates are rewritten to
`signup + 1 day` in both, about 50% of returns end up exactly 1 day after the order, and
about 8% of refunds end up 0 (8.3% in Spindle, 8.0% in the port).

**Spindle's own `FidelityComparator`** (real = Spindle seed 42, synthetic = port seed
1042). The overall port score is **91.57**. The same comparator scores Spindle against
itself at seeds 43–46 at **91.56 / 91.89 / 89.08 / 92.43**. Per table:

| table | port score | Spindle-vs-Spindle scores (seeds 43–46) |
|---|---|---|
| address | 88.47 | 88.22 / 88.07 / 87.87 / 90.75 |
| customer | 85.35 | 85.13 / 85.63 / 85.51 / 85.68 |
| order | 98.47 | 97.10 / 98.84 / 91.23 / 99.17 |
| order_line | 95.11 | 93.78 / 95.60 / 86.63 / 97.60 |
| product | 94.60 | 94.53 / 94.84 / 94.58 / 94.69 |
| product_category | 85.60 | 87.56 / 87.73 / 84.75 / 87.54 |
| promotion | 92.50 | 93.38 / 91.82 / 91.03 / 92.01 |
| return | 98.39 | 98.67 / 98.48 / 94.60 / 99.08 |
| store | 85.61 | 85.64 / 86.02 / 85.52 / 85.38 |
| **overall** | **91.57** | 91.56 / 91.89 / 89.08 / 92.43 |

The comparator scores even Spindle against itself low on some columns. For example, the
exact-value Jaccard is small for 50k random names. Its absolute scores therefore only mean
something next to the Spindle-vs-Spindle baseline. On that basis the port is
indistinguishable from another Spindle seed. With a shared seed (`--port-seed 42`),
**43 of 60 columns are bit-identical** to Spindle and the comparator scores 98.71. This is
because the port calls numpy's Generator the same way Spindle does wherever that is also
the fastest vectorized form: `rng.choice(pool)` is `rng.integers`, and
`choice(p=...)` is a CDF plus `searchsorted`.

### Per-column table (medium; metric = port vs Spindle, then Spindle's own max seed-to-seed drift / tolerance)

KS and TVD values that are 0.0000 on PK sequences or fixed structures, such as
`level`, are exact by construction. "bit-identical at same seed" refers to
`retail_verify_medium_sameseed.json` (baselines).

| table.column | strategy | arrow type | port vs Spindle metric (baseline max / tol) | bit-identical at same seed | status |
|---|---|---|---|---|---|
| customer.customer_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0123) | yes | equivalent |
| customer.first_name | faker→native `first_name` | string | distinct ratio 1.0000, vocab 1.0000 | yes | equivalent |
| customer.last_name | faker→native `last_name` | string | distinct ratio 0.9998, vocab 1.0000 | yes | equivalent |
| customer.email | faker→native `email` | string | distinct ratio 0.9993, vocab 1.0000; null 0.0497/0.0503 | yes | equivalent |
| customer.gender | weighted_enum | string | TVD 0.0017 (0.0033 / 0.0151) | yes | equivalent |
| customer.loyalty_tier | weighted_enum | string | TVD 0.0048 (0.0061 / 0.0214) | yes | equivalent |
| customer.signup_date | temporal uniform | timestamp[ns] | KS 0.0055 (0.0108 / 0.0181) | yes | equivalent |
| customer.is_active | weighted_enum | string | TVD 0.0000 (0.0036 / 0.0151) | yes | equivalent |
| product_category.category_id | sequence | int64 | KS 0.0000 (0.0000 / 0.3899) | yes | equivalent |
| product_category.category_name | reference_data categories | string | TVD 0.4400 (0.5600 / 1.9739) | no | equivalent |
| product_category.parent_category_id | self_referencing | int64 | KS 0.1905 (0.1905 / 0.4254); null 0.1600/0.1600 | yes | equivalent |
| product_category.level | self_ref_field | int64 | KS 0.0000 (0.0000 / 0.3899) | yes | equivalent |
| promotion.promotion_id | sequence | int64 | KS 0.0000 (0.0000 / 0.1949) | yes | equivalent |
| promotion.promo_name | reference_data promo_names | string | TVD 0.2550 (0.2800 / 1.1848) | yes | equivalent |
| promotion.promo_type | weighted_enum | string | TVD 0.0750 (0.1100 / 0.3785) | yes | equivalent |
| promotion.discount_pct | weighted_enum | double | KS 0.0950 (0.0850 / 0.1949) | yes | equivalent |
| promotion.start_date | temporal uniform | timestamp[ns] | KS 0.0700 (0.1300 / 0.1970) | yes | equivalent |
| promotion.end_date | derived start_date add_days uniform | timestamp[ns] | KS 0.0700 (0.1300 / 0.1970) | yes | equivalent |
| store.store_id | sequence | int64 | KS 0.0000 (0.0000 / 0.2251) | yes | equivalent |
| store.store_name | pattern `Store #{seq:4}` | string | distinct ratio 1.0000, vocab 1.0000 | yes | equivalent |
| store.store_type | weighted_enum | string | TVD 0.0133 (0.0667 / 0.3385) | yes | equivalent |
| store.city | faker→native `city` | string | TVD 0.3933 (0.4733 / 1.7261) | yes | equivalent |
| store.state | faker→native `state_abbr` | string | TVD 0.2867 (0.3467 / 1.3255) | yes | equivalent |
| address.address_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0101) | yes | equivalent |
| address.customer_id | foreign_key customer.customer_id (uniform) | int64 | KS 0.0042 (0.0047 / 0.0101) | yes | equivalent |
| address.address_type | weighted_enum | string | TVD 0.0029 (0.0043 / 0.0151) | yes | equivalent |
| address.street | faker→native `street_address` | string | distinct ratio 1.0003, vocab 1.0000 | yes | equivalent |
| address.city | record_sample us_zip_locations.city | string | distinct ratio 1.0011, vocab 1.0000 | yes | equivalent |
| address.state | record_field us_zip_locations.state | string | TVD 0.0119 (0.0143 / 0.0624) | yes | equivalent |
| address.zip_code | record_field us_zip_locations.zip | string | distinct ratio 0.9989, vocab 1.0000 | yes | equivalent |
| address.lat | record_field us_zip_locations.lat | double | KS 0.0032 (0.0041 / 0.0101) | yes | equivalent |
| address.lng | record_field us_zip_locations.lng | double | KS 0.0040 (0.0041 / 0.0101) | yes | equivalent |
| address.is_primary | first_per_parent | bool | TVD 0.0003 (0.0014 / 0.0124) | yes | equivalent |
| product.product_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0390) | yes | equivalent |
| product.category_id | foreign_key product_category.category_id (uniform) | int64 | KS 0.0218 (0.0206 / 0.0390) | yes | equivalent |
| product.product_name | reference_data product_names | string | TVD 0.0810 (0.0926 / 0.3614) | yes | equivalent |
| product.unit_price | distribution log_normal {"mean": 3.0, "sigma": 1.0, "min": 0.99, "max": 999.99} | double | KS 0.0142 (0.0312 / 0.0488) | yes | equivalent |
| product.product_status | lifecycle | string | TVD 0.0014 (0.0114 / 0.0586) | yes | equivalent |
| product.cost | correlated multiply unit_price | double | KS 0.0134 (0.0286 / 0.0449) | yes | equivalent |
| order.order_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0039) | yes | equivalent |
| order.customer_id | foreign_key customer.customer_id (pareto {"alpha": 1.16, "max_per_parent": 50}) | int64 | KS 0.0048 (0.0039 / 0.0079) | no | equivalent |
| order.store_id | foreign_key store.store_id (zipf {"alpha": 1.3}) | int64 | KS 0.0016 (0.0015 / 0.0043) | no | equivalent |
| order.shipping_address_id | foreign_key address.address_id (uniform, constrained_by customer_id) | int64 | KS 0.0042 (0.0065 / 0.0117); null 0.2239/0.2234 | no | equivalent |
| order.promotion_id | foreign_key promotion.promotion_id (uniform) | int64 | KS 0.0032 (0.0040 / 0.0080); null 0.7002/0.7001 | no | equivalent |
| order.order_date | temporal seasonal | timestamp[us] | KS 0.0043 (0.0062 / 0.0112) | no | equivalent |
| order.status | weighted_enum | string | TVD 0.0017 (0.0026 / 0.0076) | no | equivalent |
| order.order_total | computed sum_children(order_line.line_total) | double | KS 0.1538 (0.4060 / 0.6109) | no | equivalent |
| order_line.order_line_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0025) | yes | equivalent |
| order_line.order_id | foreign_key order.order_id (uniform) | int64 | KS 0.0010 (0.0010 / 0.0035) | no | equivalent |
| order_line.product_id | foreign_key product.product_id (zipf {"alpha": 1.5}) | int64 | KS 0.0008 (0.0009 / 0.0034) | no | equivalent |
| order_line.quantity | distribution geometric {"p": 0.6, "min": 1, "max": 20} | double | KS 0.0008 (0.0007 / 0.0031) | yes | equivalent |
| order_line.promotion_id | lookup order.promotion_id via order_id | int64 | KS 0.0029 (0.0041 / 0.0081); null 0.6999/0.7006 | no | equivalent |
| order_line.unit_price | lookup product.unit_price via product_id | double | KS 0.6113 (0.5283 / 0.7944) | no | equivalent |
| order_line.discount_percent | conditional (promotion_id IS NOT NULL) | double | KS 0.0287 (0.0263 / 0.0414) | no | equivalent |
| order_line.line_total | formula `quantity * unit_price * (1 - discount_percent / 100)` | double | KS 0.3487 (0.4434 / 0.6671) | no | equivalent |
| return.return_id | sequence | int64 | KS 0.0000 (0.0000 / 0.0095) | yes | equivalent |
| return.order_id | foreign_key order.order_id (uniform) | int64 | KS 0.0071 (0.0059 / 0.0108) | no | equivalent |
| return.reason | weighted_enum | string | TVD 0.0054 (0.0080 / 0.0217) | yes | equivalent |
| return.refund_amount | distribution log_normal {"mean": 3.5, "sigma": 1.0, "min": 5.0, "max": 2000.0} | double | KS 0.0571 (0.1167 / 0.1770) | no | equivalent |
| return.return_date | derived order.order_date add_days log_normal | timestamp[us] | KS 0.0051 (0.0108 / 0.0181) | no | equivalent |

## Known deviations (none change the output distribution)

The port uses different random streams from Spindle. Beyond that, every place where it
computes something differently is listed here.

1. **Zipf FKs** (`order.store_id` α=1.3 over 150 stores, `order_line.product_id` α=1.5
   over the products). Spindle draws `rng.zipf(α, 2n)`, discards draws greater than N, and
   loops until it has n. The port samples the truncated Zipf distribution,
   P(k) ∝ k^-α for k ≤ N, directly with an alias table. Rejecting the tail of a Zipf is
   mathematically the same as truncating it, so the distribution is identical. **Perf
   impact:** moderate. At medium, `rng.zipf` takes about 0.37s for 2.5M draws at α=1.5
   and about 0.15s for 1M draws at α=1.3, roughly 0.5s of Spindle's 4.6s generate time.
   The alias path takes about 0.1s in total.
2. **Weighted choice with more than 8 categories** (`category_name` weights, Zipf tables):
   the port uses Walker/Vose alias sampling instead of CDF plus `searchsorted`. It is exact
   and the distribution is identical. With 8 or fewer categories the port uses the same
   CDF form as `rng.choice`. Perf impact: small at medium; it avoids a cache-unfriendly
   binary search per draw at large.
3. **`max_per_parent` enforcement** (`order.customer_id`, Pareto α=1.16, cap 50). Spindle
   runs a Python loop over every over-limit parent, calling
   `rng.choice(positions, excess, replace=False)`, for up to 10 rounds. The port chooses the
   rows to reassign with one `argsort` of random keys within each parent group. That selects
   a uniformly random subset of size `excess` per parent, which is the same distribution.
   The reassignment targets and round structure are unchanged. Perf impact: moderate.
4. **Constrained FK** (`order.shipping_address_id` constrained by `customer_id`). Spindle
   loops over each distinct customer with pandas `groupby` and `rng.choice`. The port
   builds dense per-customer offset/count tables once and picks
   `offset + floor(u·count)`. It gives the same uniform choice among the customer's
   addresses, and NULL when the customer has no address (22.3% of orders at medium in
   both). **Perf impact: this is the single largest difference.** Spindle's loop runs about
   40k iterations at medium and about 400k at large. cProfile at medium puts
   `get_constrained_fks` at about 47% of Spindle's generate time. It is also why
   Spindle's `order` table takes 68s at large against 2.4s at medium (10× the rows, 28× the
   time).
5. **Seasonal `order_date`.** Spindle draws multinomial bucket counts over 84
   (month × day-of-week) buckets, samples days per bucket, concatenates and shuffles. The
   port draws one categorical bucket per row, then a uniform valid day in it. A shuffled
   multinomial sample is an iid categorical sequence, so the distribution is identical.
   Spindle also generates a random microsecond-of-day offset that the hour profile then
   throws away (it normalizes to midnight). The port skips that dead work; the output is
   the same.
6. **Hour-of-day is uniform in both. This is a Spindle quirk, not a port deviation.** In
   `retail.py` the hour profile defaults to
   `{"distribution": "bimodal", "peaks": [12, 20]}`. But `profiles/default.json` overrides
   it with `{"peaks": [12, 20], "std_dev": 2}`, which has no `"distribution"` key. As a
   result `TemporalStrategy._apply_hour_profile` takes its uniform branch. The port reads
   the same profile, so it reproduces the uniform hours. The verifier shows about 4.17%
   per hour in both.
7. **String building.** Spindle builds email and street strings with a per-row Python
   f-string. The port gathers from pools and concatenates with
   `pyarrow.compute.binary_join_element_wise`. The lowercasing and space-stripping,
   `str(f).lower().replace(' ', '')`, run once per pool entry with Python's own `str.lower`,
   so the strings are byte-identical. The `max_length` truncation of names is applied to
   pool entries, which is equivalent. The email `max_length=255` truncation uses
   `utf8_slice_codeunits`; it is a no-op on this data.
8. **Nullable integer FKs.** Spindle holds these as pandas `object` columns of Python
   `int`/`None`. The port holds them as `int64` with a validity bitmap. Both are Parquet
   `int64`. Formulas and conditions see NaN in both. For example, `discount_percent` is 0
   when `promotion_id` is NULL.
9. **Generic interpreter coverage.** The port implements every strategy and branch the
   retail schema reaches. The following unused branches raise `NotImplementedError`:
   * native providers phone, ssn, company, sentence, uri and pystr;
   * FK `sample_rate` and filter;
   * `pattern` tokens that reference columns;
   * `computed` min/max.
   If `retail.py` starts using one of them, the port fails loudly rather than drifting
   silently.
10. **Post-fix validation.** Spindle's `fix_violations` ends with a full `validate()`
    pass. The port runs the equivalent vectorized validation too, stored in
    `Engine.rule_violations`, so the timed work matches. The result is 0 remaining
    violations in both.
11. **Conditional's null test.** Spindle computes `_is_null` with a per-row Python list
    comprehension over 1.25M `promotion_id` values. The port uses the validity mask. The
    result is the same: `discount_percent` is 0.0 where `promotion_id` is NULL.
12. **Random streams.** The streams are not bit-identical in general. At the same seed, 43
    of 60 columns are, as described above. At different seeds every metric falls inside
    Spindle's own seed-to-seed variation.

## Benchmark

### Setup
* Machine: 4 cores (`sched_getaffinity` = 4), Linux 6.18 Firecracker VM, 15 GB RAM,
  Python 3.11.15.
* Both tools run under the Spindle venv: numpy 2.4.6, pyarrow 25.0.1, pandas 3.0.6. The
  port is also shown under the shape venv (pyarrow 23.0.1, no pandas).
* Seed 42. Every run is a fresh process, and tools are interleaved run by run. Numbers are
  the median of 3 runs.
* **Timed region.** Spindle: `RetailDomain()` plus `Spindle()` plus
  `generate(scale, seed)` plus `PandasWriter.to_parquet(...)`. `PandasWriter.to_parquet`
  is the `spindle generate --format parquet` CLI path: sequential
  `df.to_parquet(index=False)` with snappy compression. Port: `port.generate(...)` (load
  config, pools and reference data, then generate) plus sequential
  `pq.write_table(..., compression="snappy")`.
* Imports are outside the timed region for both. Parquet output is deleted between runs,
  outside the timed region. Parquet sizes match: 36 MB at medium and 375 MB at large for
  both tools.
* Every benchmark invocation held the exclusive lock `$BENCH_OUT_DIR/bench.lock`. Before each run the
  harness waited until the 1-minute load average was below 1.5. It also measured the CPU
  used by *other* processes during the run (from `/proc/stat` minus the child's rusage).
  Runs where other processes averaged more than 0.75 cores were discarded and retried
  after 30s (see "disturbed ... retrying" in the log). Load averages just before the kept
  runs were 0.9–1.5. Another agent's development jobs were running on the machine
  intermittently; the residual interference is listed per run below.

**medium** (1,965,400 rows; median of 3 fresh-process runs, seed 42)

| tool | total s | generate s | write s | rows/s total | rows/s generate | user s | sys s | minor faults | peak RSS MB | Parquet MB | run totals (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Spindle (spindle-venv) | 5.29 | 4.65 | 0.65 | 371,505 | 422,917 | 4.48 | 0.89 | 57,652 | 540 | 36 | 5.29, 5.645, 4.728 |
| port (spindle-venv, **primary**) | 1.26 | 0.55 | 0.71 | 1,562,720 | 3,559,490 | 0.90 | 0.22 | 47,191 | 424 | 36 | 1.258, 0.994, 1.589 |
| port (shape venv, pyarrow 23) | 1.09 | 0.60 | 0.49 | 1,811,007 | 3,282,387 | 0.91 | 0.18 | 65,884 | 336 | 36 | 1.217, 1.068, 1.085 |

Speed-up (port vs Spindle, same interpreter and libraries): **total 4.21×, generate 8.42×, write 0.92×**.

Per-table generation time (s, median):

| | customer | product_category | promotion | store | address | product | order | order_line | return | compute+rules | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Spindle | 0.09 | 0.00 | 0.00 | 0.00 | 0.19 | 0.00 | 2.44 | 1.67 | 0.03 | 0.31 | construct 0.00 |
| port | 0.03 | 0.00 | 0.00 | 0.00 | 0.09 | 0.00 | 0.18 | 0.16 | 0.01 | 0.04 | load config/pools 0.02, to_arrow 0.01 |

Import time, outside the timed region: Spindle 1.01s (pandas and Spindle), port 0.32s (numpy, pyarrow and the pyarrow lazy-pandas warm-up). Other-process CPU during the kept runs, in average cores: Spindle [0.077, 0.127, 0.176], port [0.134, 0.262, 0.297].

**large** (19,625,400 rows; median of 3 fresh-process runs, seed 42)

| tool | total s | generate s | write s | rows/s total | rows/s generate | user s | sys s | minor faults | peak RSS MB | Parquet MB | run totals (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Spindle (spindle-venv) | 102.64 | 96.58 | 6.03 | 191,199 | 203,210 | 84.89 | 16.23 | 603,252 | 3353 | 375 | 106.127, 102.644, 101.245 |
| port (spindle-venv, **primary**) | 14.89 | 10.66 | 4.31 | 1,318,090 | 1,840,291 | 8.21 | 6.40 | 97,363 | 2087 | 375 | 14.769, 14.974, 14.889 |
| port (shape venv, pyarrow 23) | 13.83 | 9.34 | 4.33 | 1,419,501 | 2,102,170 | 8.33 | 5.07 | 186,156 | 1979 | 375 | 15.372, 13.663, 13.826 |

Speed-up (port vs Spindle, same interpreter and libraries): **total 6.89×, generate 9.06×, write 1.40×**.

Per-table generation time (s, median):

| | customer | product_category | promotion | store | address | product | order | order_line | return | compute+rules | other |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Spindle | 0.86 | 0.00 | 0.00 | 0.00 | 1.53 | 0.01 | 68.04 | 19.04 | 0.24 | 5.96 | construct 0.00 |
| port | 0.18 | 0.00 | 0.00 | 0.00 | 0.30 | 0.00 | 2.48 | 6.79 | 0.11 | 0.65 | load config/pools 0.02, to_arrow 0.09 |

Import time, outside the timed region: Spindle 0.98s (pandas and Spindle), port 0.38s (numpy, pyarrow and the pyarrow lazy-pandas warm-up). Other-process CPU during the kept runs, in average cores: Spindle [0.593, 0.078, 0.191], port [0.317, 0.085, 0.12].


### Fairness notes (read before quoting the numbers)
* **Where the win comes from.** Spindle already vectorizes most strategies with numpy.
  The gap comes from a few Python-level loops on its hot path:
  * the per-customer `get_constrained_fks` loop, the dominant cost;
  * the per-parent `_enforce_max_per_parent` loop;
  * the per-row `_is_null` list comprehension in `conditional`;
  * per-row f-strings for email and street;
  * the per-row `max_length` truncation list comprehension on names and email;
  * `rng.zipf` rejection sampling;
  * pandas overhead in lookups, `map` and `groupby`.
  The port removes those and nothing else. At large, Spindle's `order` table alone is 68s
  of its 97s.
* **Parquet writing is roughly the same for both, as expected.** Both end in the same
  pyarrow Parquet writer. Spindle also pays for the pandas→Arrow conversion. At medium the
  port's write is not faster: 0.71s against 0.65s median, and the port's three writes
  ranged 0.45–0.75s. At large the port writes 1.4× faster (4.3s against 6.0s), mostly
  because it skips the conversion. Most of the total speed-up comes from generation.
* **The VM's first-touch memory cost inflates large-scale numbers for both tools.** On this
  Firecracker VM, freed guest pages go back to the host, so newly faulted memory costs
  about 4s per GB. Allocating and touching 10 GiB took about 41s, repeatably. Whichever
  tool has the larger peak RSS pays more. Spindle peaks at 3.35 GB at large, the port at
  2.09 GB, and that shows up in `sys` time: 16.2s against 6.4s. On bare metal both large
  numbers would be lower. Spindle's `user` time alone (84.9s against 8.2s) gives a
  speed-up of about 10× that is independent of the VM.
* **Single process for both.** Neither tool uses multiprocessing. pyarrow's Parquet writer
  and some pandas→Arrow conversion use the pyarrow thread pool in both. user+sys stays
  close to wall time for both.
* **Library versions.** The primary comparison runs both tools with identical numpy and
  pyarrow. The port is slightly faster under pyarrow 23 than 25 at large (13.8s against
  14.9s). This does not favour the port in the headline number.
* **pyarrow warm-up.** pyarrow lazily imports pandas the first time it converts a Python
  list when pandas is installed, which costs about 0.2s. Spindle imports pandas before its
  timed region, so the port's child triggers that lazy import in its untimed import phase
  as well. The port does nothing else outside the timed region. It reads `retail.py`, the
  profile, the name pools, `native.py` and all four reference JSONs (including the 3 MB
  ZIP file) inside the timed region, just as Spindle does.
* **Semantic parity.** Only work that cannot change the output was dropped: the discarded
  microsecond offsets in seasonal dates. Everything else runs, including the post-fix
  validation pass and all rule fixing.
* **Not measured.** Spindle's chunked, streaming and Spark paths, `to_parquet` via
  `GenerationResult` (4 threads, zstd), and scales above large.

## The product (`--impl shape`)

`--impl shape` is the product path, not a port: the `shape.domains` plugin (`sqllocks-shape-domains`,
installed with `pip install -e plugins/shape-domains`) supplies the schema and reference data,
`shape.generation.engine.Engine` generates it on every core, and `shape.generation.output.write_engine`
writes snappy Parquet as each table is final. The timed region is construct (load the domain, build
the engine) + generate + write; the strategy and sink plugins are loaded before it, as the baseline's
import loads all of its own (T-19: imports are excluded for both tools). Because generation and
writing overlap, the runner reports the whole as `gen_s` and `write_s` is 0. `bench.py --warmup 1`
discards one fresh-process run per tool before the timed ones (T-19).

```bash
source scripts/env.sh
"$SPINDLE_PY" benchmarks/vs_spindle/domain_1to1/verify.py --domain retail --scale medium --impl shape
"$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/bench.py \
    --impl shape --domain retail --scales medium,large --runs 5 --warmup 1
```
