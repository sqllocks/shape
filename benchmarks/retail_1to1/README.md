# Spindle retail domain: 1:1 vectorized port and fair benchmark

This directory holds a numpy + pyarrow port of Spindle's `retail` domain. It reproduces
the same 9 tables, 60 columns, row counts, column order, Arrow/Parquet types, per-column
generation semantics, compute phase and business-rule fixing. It also holds a verifier
that measures how close the port is to Spindle, and a benchmark harness that times both
tools under the same conditions.

| file | what it is |
|---|---|
| `port.py` | `generate(scale='medium', seed=42, spindle_root=...) -> dict[str, pyarrow.Table]` and `write_parquet(tables, dir)`. Uses only numpy, pyarrow and the standard library. |
| `verify.py` | Equivalence verifier (Spindle venv: needs pandas, scipy and Spindle). Writes `verify_report.json` and `verify_summary.txt`. |
| `bench.py` | Benchmark harness. Every run is a fresh process; the median of 3 runs is reported. Writes `bench_results.json`. |
| `verify_report*.json`, `verify_summary*.txt`, `bench_results.json` | Results of the runs quoted below. |

## How to run

```bash
SPY=/tmp/claude-0/spindle-venv/bin/python     # Spindle's venv (pandas 3.0.6, numpy 2.4.6, pyarrow 25.0.1, scipy)
cd /home/user/shape/benchmarks/retail_1to1

# port only (works in any env with numpy + pyarrow; e.g. /tmp/claude-0/venv)
/tmp/claude-0/venv/bin/python port.py --scale medium

# equivalence: Spindle(seed 42) vs port(seed 1042) vs Spindle seeds 43..46 (~3 min at medium)
$SPY verify.py --scale medium
# same-seed variant (shows which columns are bit-identical)
$SPY verify.py --scale medium --port-seed 42 --baseline-seeds 43 --out verify_report_sameseed.json

# benchmark (exclusive lock shared with other benchmark jobs on this machine)
flock /tmp/claude-0/bench.lock $SPY bench.py --scales medium,large --runs 3
```

`spindle_root` defaults to `/home/user/sqllocks/spindle`. The port reads that checkout at
runtime and never writes to it (bytecode writing is disabled while `names.py` is loaded).
Nothing from Spindle is copied into this repo.

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
  `us_zip_locations.json` (33,xxx ZIP records) are found with the same search order as
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

## Equivalence results (medium, `verify_summary.txt`)

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

**Result: 60 / 60 columns equivalent at medium.** No columns are flagged.
{{VERIFY_EXTRA}}

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
2/5/7/12, and 8.3% of orders have no lines in both.

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
8.3% of refunds end up 0.

**Spindle's own `FidelityComparator`** (real = Spindle seed 42, synthetic = port seed
1042). The overall port score is **91.57**. The same comparator scores Spindle against
itself at seeds 43–46 at **91.56 / 91.89 / 89.08 / 92.43**. Per table:

{{FIDELITY_TABLE}}

The comparator scores even Spindle against itself low on some columns. For example, the
exact-value Jaccard is small for 50k random names. Its absolute scores therefore only mean
something next to the Spindle-vs-Spindle baseline. On that basis the port is
indistinguishable from another Spindle seed. With a shared seed (`--port-seed 42`),
**43 of 60 columns are bit-identical** to Spindle and the comparator scores 98.71. This is
because the port calls numpy's Generator the same way Spindle does wherever that is also
the fastest vectorized form: `rng.choice(pool)` is `rng.integers`, and
`choice(p=...)` is a CDF plus `searchsorted`.

### Per-column table (medium; metric = port vs Spindle, then Spindle's own max seed-to-seed drift / tolerance)

{{COLUMN_TABLE}}

## Known deviations (none change the output distribution)

The port uses different random streams from Spindle. Beyond that, every place where it
computes something differently is listed here.

1. **Zipf FKs** (`order.store_id` α=1.3 over 150 stores, `order_line.product_id` α=1.5
   over the products). Spindle draws `rng.zipf(α, 2n)`, discards draws greater than N, and
   loops until it has n. The port samples the truncated Zipf distribution,
   P(k) ∝ k^-α for k ≤ N, directly with an alias table. Rejecting the tail of a Zipf is
   mathematically the same as truncating it, so the distribution is identical. **Perf
   impact:** this is one of the largest single wins. numpy's Zipf rejection sampler is
   slow for α close to 1, and 2n draws are made {{ZIPF_COST}}.
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
   both). Perf impact: large, because Spindle's loop runs about 40k iterations at medium
   and about 400k at large.
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
10. **Random streams.** The streams are not bit-identical in general. At the same seed, 43
    of 60 columns are, as described above. At different seeds every metric falls inside
    Spindle's own seed-to-seed variation.

## Benchmark

{{BENCH}}
