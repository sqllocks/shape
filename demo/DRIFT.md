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
| products | `sku` | not reported: uniqueness is not a diff kind (section 12.3); the contract catches it |

Two things to know before the live run:

- **The default threshold misses drift 3.** A +40% shift is 0.39 baseline standard
  deviations, and the section 12.3 default is 0.5. With default thresholds the diff does not
  show `mean_shift`; pass `thresholds={"mean_shift_std": 0.25}` (tested). The contract still
  fails on `order_total.max`, so the gate is unaffected.
- **Side effect the profiler also reports** (tests pin it, so new noise fails the suite):
  `orders.order_total` `new_categorical_values` (the profiler keeps an enum list for this
  float column and every day-2 value is new). That is the only one: the products diff is
  empty.

## Reproduce

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
pytest tests/demo/content
```

Upload `day1/` and `day2/` to the lakehouse `Files/demo/` folder and the contracts to
`Files/contracts/` (see `integrations/fabric/RUNBOOK.md`).
