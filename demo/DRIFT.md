# Day-2 drift

`demo/make_data.py` writes two copies of the retail data. **Day 1** is the healthy baseline.
**Day 2** has four deliberate, deterministic changes (seed 42, medium scale), so the quality
gate visibly fails. Only the three drifted tables are written for day 2.

| # | Change | Day 1 | Day 2 |
|---|---|---|---|
| 1 | `customers.email` null rate rises | 4.97% | exactly 20% |
| 2 | `orders.status` gains a new value | `cancelled, completed, processing, returned, shipped` | adds `lost` on 2% of rows |
| 3 | `orders.order_total` mean shifts by +40% | mean 105.94 | every value × 1.40 (mean 148.31) |
| 4 | `products.sku` loses uniqueness | 5,000 unique SKUs | 50 extra rows reuse an existing SKU (5,050 rows, 5,000 distinct) |

Table names follow the lakehouse layout: `customers`, `orders`, `products`, `returns` and
`d2` (`order` and `return` are SQL keywords, so the files are plural). Day 1 holds all five
(medium scale: 50,000 customers, 500,000 orders, 5,000 products, 85,000 returns, 1,000,000 d2
rows); day 2 holds only the three drifted tables. `products.sku` is a category prefix plus the
zero-padded `product_id` (such as `ELC-004217`). The 50 duplicate rows get new `product_id` values, so
only `sku` loses uniqueness. Day-1 `customers.email` has exactly 2,485 nulls (4.97%) and the
largest day-1 order is pinned to 5135.63, so the figures in `demo/TALK.md` hold.

## What the contracts flag

Day 1 passes every contract in `demo/contracts/`. Day 2 fails on exactly these rules
(verified with `shape.check` in `tests/demo/content/`):

| Table | Column and rule | Contract | Day-2 observed |
|---|---|---|---|
| customers | `customers.email` — `max_null_rate` | 0.08 | 0.20 |
| orders | `orders.status` — `allowed_values` | five day-1 values | unexpected value `lost` |
| orders | `orders.order_total` — `max` | 6000 | 7189.882 (day 1: 5135.63) |
| products | `products.sku` — `unique` | true | 5,000 distinct in 5,050 rows |

`orders.order_total` has no rule on its mean: the v1 contract format has no mean rule, so the
+40% shift is caught by the `max` bound and reported by `shape.diff` as `mean_shift`.

## What `shape.diff(day1, day2)` reports

| Table | Column | Kind |
|---|---|---|
| customers | `email` | `null_rate_change` (0.0497 to 0.20) |
| orders | `status` | `new_categorical_values` |
| orders | `order_total` | `mean_shift` — **needs `thresholds={"mean_shift_std": 0.25}`** |
| products | `sku` | not reported: 50 duplicates in 5,050 rows move distinct values per row by 1%, under the `uniqueness_rate` default of 5%; the contract's `unique` rule catches it |

Two things to know before the live run:

- **The default `mean_shift` threshold misses drift 3.** A +40% shift is 0.39 baseline
  standard deviations (0.3876 = (148.3128 − 105.9377) / 109.3338: the day-2 and day-1
  `order_total` means, which the stage diff below prints as the `mean_shift` change's
  `current` and `baseline`, over the day-1 `std` that `shape inspect o1.shape` shows for
  `order_total`; `tests/demo/content/test_demo_data.py` recomputes it from the profiles), and
  the section 12.3 default is 0.5, so `mean_shift` only appears with
  `thresholds={"mean_shift_std": 0.25}` (tested). The shift is not invisible with the defaults:
  the profiler keeps an enum list for this float column, so the default diff reports it as
  `category_shift` (medium) and `range_change` (low). The contract still fails on
  `order_total.max`, so the gate is unaffected.
- **Side effects the diff also reports** (tests pin them, so new noise fails the suite):
  `orders.order_total` `new_categorical_values` (every day-2 value is new), `category_shift`
  and `range_change`. The products diff is empty. The kinds, thresholds and per-column overrides
  are in `docs/DRIFT.md`.

## In the Fabric notebook (a saved baseline)

`shape_profile`'s day-2 run diffs against the day-1 **artifact** it saved (`baselinePath`), and the
notebook saves with `shape.save`'s default safe capture. A safe-capture profile keeps statistics
and formats but leaves out the values that `order_total`'s comparisons need, so that diff reports
**only** `orders.status` `new_categorical_values`: `order_total`'s `range_change` and
`category_shift` are not evaluable (skipped, never drift), and `mean_shift` is under the default
threshold as above. The contract gate is unaffected (it checks the day-2 profile in memory, and
still fails on `status` and `order_total` `max`). To show `order_total`'s drift in a diff, diff two
full-capture profiles, as below. `tests/demo/content/test_demo_data.py`
(`test_the_notebook_baseline_diff_reports_only_the_new_status`) pins this.

## On the command line (the local fallback of `demo/TALK.md`)

From the repository root, after `python demo/make_data.py --out data`:

```bash
shape profile data/day1/orders.parquet -o o1.shape --html o1.html --capture full
shape check o1.shape demo/contracts/orders.json        # exit 0
shape profile data/day2/orders.parquet -o o2.shape --html o2.html --capture full
shape check o2.shape demo/contracts/orders.json        # exit 1: status allowed_values, order_total max 7189.882
shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium   # 942 bytes
shape diff o1.shape o2.shape | wc -c                   # 1155856 bytes: do not run this on stage
```

`--capture full` is needed because the orders contract checks `order_total`'s `min` and `max`,
which the default (safe) capture does not keep: with it `shape check` cannot evaluate them and
exits 2. The demo data is synthetic, so a full capture of it holds no personal values; keep the
`.shape` files out of git all the same. The byte counts are the stdout of the two `shape diff`
commands on these profiles (stderr, with the note that the files are not signed, not counted).
`tests/demo/content/test_demo_data.py` runs the commands as `demo/TALK.md` gives them and
checks the exit codes, the violations and both byte counts; `demo/rehearse.sh` runs them too.

## Reproduce

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
pytest tests/demo/content
```

Upload `day1/` and `day2/` to the lakehouse `Files/demo/` folder and the contracts to
`Files/contracts/` (see `integrations/fabric/RUNBOOK.md`).
