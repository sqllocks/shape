# Day-2 drift

`demo/make_data.py` writes two copies of the retail data. **Day 1** is the healthy baseline.
**Day 2** has four deliberate, deterministic changes (seed 42, medium scale), so the quality
gate visibly fails. Only the three drifted tables are written for day 2.

| # | Change | Day 1 | Day 2 |
|---|---|---|---|
| 1 | `customers.email` null rate rises | 4.97% | exactly 20% |
| 2 | `orders.status` gains a new value | `cancelled, completed, processing, returned, shipped` | adds `lost` on 2% of rows |
| 3 | `orders.order_total` mean shifts by +40% | mean 110.93 | every value × 1.40 (mean 155.30) |
| 4 | `products.sku` loses uniqueness | 5,000 unique SKUs | 50 extra rows reuse an existing SKU (5,050 rows, 5,000 distinct) |

Table names follow the lakehouse layout: `customers`, `orders`, `products` (the retail
tables `customer`, `order` and `product`; `order` is a SQL keyword). The retail schema has
no SKU column, so `make_data.py` derives `products.sku` (`SKU-000001` ...) from
`product_id`. The 50 duplicate rows get new `product_id` values, so only `sku` loses
uniqueness.

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

- **The default threshold misses drift 3.** A +40% shift is 0.43 baseline standard
  deviations, and the section 12.3 default is 0.5. With default thresholds the diff does not
  show `mean_shift`; pass `thresholds={"mean_shift_std": 0.25}` (tested). The contract still
  fails on `order_total.max`, so the gate is unaffected.
- **Side effects the profiler also reports** (tests pin them, so new noise fails the suite):
  `orders.order_total` `new_categorical_values` (the profiler keeps an enum list for this
  float column and every day-2 value is new), and `products.category_id`
  `distribution_change` (the 50 extra rows tip its distribution classification from
  `uniform` to none).

## Reproduce

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
pytest tests/demo/content
```

Upload `day1/` and `day2/` to the lakehouse `Files/demo/` folder and the contracts to
`Files/contracts/` (see `integrations/fabric/RUNBOOK.md`).
