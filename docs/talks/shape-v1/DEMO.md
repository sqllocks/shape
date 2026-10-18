# Live demo runbook: Ship the Shape, Not the Data (October 3, 2026)

Current Fabric setup is in the [runbook](../../../integrations/fabric/RUNBOOK.md).
The later [live dry-run findings](../../plans/demo_status/DEMO-LIVE.md) record integration
fixes and outstanding live checks; they are not a timing benchmark for this talk.

| Demo | Slide | Where | Length | Status |
|---|---|---|---|---|
| **C1** Profile a whole schema, read the report | 10–11 | laptop | ~4 min | runs today (verified) |
| **C2** The production pipeline | 16–19 | Fabric | ~3 min | verified locally; **Fabric only if the owner's dry run (R11) is done** |
| **C2-local** The same pipeline on the laptop | 16–19 | laptop | ~3 min | runs today (verified); **the default if R11 isn't done** |
| **C3** Profile a 1M-row file, with a clock | 22 | laptop | ~2 min | runs today (verified) |

There are **no** demos for section 6 ("how it will work"). The former D1–D4 (safe shape,
generate from a shape, fidelity, dev-refresh pipeline) are cut: none of their features
exists on October 3 (R1–R9 in `READINESS.md`). Don't show pre-made output for them, and
don't type their planned commands on stage.

**Stand-in data rule:** every "production" table here is synthetic retail data from the
repo's own generator, `demo/make_data.py`, built on Shape and reading nothing outside the
repo (`STATUS.md`, "Demo data generator"). Say so on stage. No real
production data.

Every command here was run on 2026-09-30 through `verify_snippets.sh` (`STATUS.md`).

## Two paths

| | **Path A: dry run done** (R11 ✅) | **Path B: dry run not done** (fallback) |
|---|---|---|
| Demos | C1, **C2 in Fabric**, C3 | C1, **C2-local**, C3 |
| Network | Fabric workspace | none needed |
| Slide 14 | may show the committed Fabric dry-run timing record rows, read exactly, with SKU and vCores | **no Fabric timings**; platform limits only |
| Slide 16 | "this is the notebook running in Fabric" | "the same calls run in the Fabric notebook in the repo"; show the pipeline as a diagram |

Decide on **October 2**: Path A only if a committed Fabric dry-run timing record contains every required measurement and every
runbook §11 box is ticked. Otherwise Path B. Either way the talk's timings don't change.

## 0. Preparation

### 0.1 Before October 2

1. Owner's Fabric dry run (`integrations/fabric/RUNBOOK.md` §11); record timings in
   a committed Fabric dry-run timing record. (If it doesn't happen: Path B.)
2. `REQUIRE_READY=1 … verify_snippets.sh` exits 0 on the laptop you'll present from.
3. One full rehearsal with a clock (checkpoints in `OUTLINE.md`).

### 0.2 The day before: build the stage folder

From the repo root, Python 3.11+, on `main` at or after `b2dd663` (slide 19's exit 2 for a
missing `.shape` needs it; the TestPyPI 0.9.0 wheel predates it):

```bash
# Shape
python3 -m venv ~/.venvs/shape && ~/.venvs/shape/bin/pip install -e ".[dev]" deltalake

# All demo data: day-1 and day-2 retail tables (documented drift, demo/DRIFT.md) and d2.
# The day-1 customers, orders, products and returns tables are the "production" stand-in
# (640,000 rows, 4 tables)
source scripts/env.sh && ~/.venvs/shape/bin/python demo/make_data.py --out "$BENCH_DATA_DIR/demo"

# Every slide snippet still prints what the slides say; the delivery gate
source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh

# Stage folder
REPO=$PWD; mkdir -p ~/shape-demo/{prod,contracts,prerun} && cd ~/shape-demo
for t in customers orders products returns; do cp "$HOME/bench-data/demo/day1/$t.parquet" prod/; done
cp "$HOME/bench-data/demo/day1/orders.parquet" orders_day1.parquet
cp "$HOME/bench-data/demo/day2/orders.parquet" orders_day2.parquet
cp "$HOME/bench-data/demo/day1/d2.parquet" d2.parquet   # C3: 1,000,000 rows x 20 columns
cp "$REPO"/demo/contracts/*.json contracts/
```

Rehearse every demo once in `~/shape-demo`. Keep the outputs in `prerun/`
(`retail_prod.shape`, `retail_prod.html`, `day1.shape`, `day2.shape`, `result.json`, and,
on Path A, a screenshot of each Fabric step) for the fallbacks. `prerun/` holds real values
from the stand-in; it's synthetic, but handle it the way slide 20 says.

### 0.3 30 minutes before

- [ ] Terminal in `~/shape-demo`, venv active, font ≥ 20 pt, `clear`.
      `rm -f *.shape *.html *.json` (keep `prerun/`).
- [ ] Python REPL open in `~/shape-demo` with `import shape` already run (hides import time).
- [ ] Browser: tab 1 `prerun/retail_prod.html`; on Path A, tab 2 the Fabric workspace.
- [ ] Path A only: `shape_profile` notebook session **already started with 8 vCores**,
      lakehouse attached; day-1 artifact path copied; the last green and red pipeline runs
      visible.
- [ ] Say it to yourself once: "production here is a stand-in; section 6 is planned."

## C1 — Profile a whole schema (slides 10–11, ~4 min)

In the REPL:

```python
prod = {
    "customer": "prod/customers.parquet",
    "order": "prod/orders.parquet",
    "product": "prod/products.parquet",
    "return": "prod/returns.parquet",
}
p = shape.profile(prod, name="retail")
shape.save(p, "retail_prod.shape")
for r in p.summary()["relationships"]:
    print(r["child"], r["child_columns"], "->", r["parent"])
```

Expect 3 lines: `order ['customer_id'] -> customer`, `return ['order_id'] -> order` and
`return ['product_id'] -> product`. Then:

```python
open("retail_prod.html", "w").write(p.to_html())
```

and open it in the browser (slide 11). Point at a fitted distribution, the `email` pattern
on `customer.email`, the `segment` enum, and the relationships. On `customer.last_name`,
point at the top values: "real-looking values; in production they'd be real; slide 20."

**Say while it runs:** "Four tables, 640,000 rows. One call, and it finds three foreign keys."

**Caveats:** the dict names the tables `customer`, `order`, … because the rule (slide 9) links
`customer_id` to a table called `customer`; with the plural file names it finds nothing.
`product.category_id` has no parent table in the stand-in. Don't quote the run time.

## C2 — The production pipeline in Fabric (slides 16–19, ~3 min) — Path A only

Item names, parameters and expected results come from `integrations/fabric/RUNBOOK.md`.

| # | Where | Do | Expect |
|---|---|---|---|
| C2.1 | Notebook `shape_profile` | parameters `tableName = "orders_day1"`, `contractPath = "contracts/orders.json"`, **Run all** | report inline; exit value `passed: true`; artifacts in `Files/shape/orders_day1/<timestamp>/` (point at the timestamped `.shape`: "every run leaves one") |
| C2.2 | Same | `tableName = "orders_day2"`, `baselinePath` = the day-1 artifact, **Run all** | `passed: false`; violations `status allowed_values`, `order_total max`; `drifted: true` |
| C2.3 | Pipeline `shape_gate_notebook` | show the last day-1 (green) and day-2 (red) runs; open the day-2 Fail message | the message lists the violations |

**Caveats:** the notebook's diff uses the default thresholds, so there's no `mean_shift`
entry on day 2 (`DRIFT.md`). If `%%configure` didn't apply, carry on and quote no times.
Only quote Fabric timings from a committed Fabric dry-run timing record.

## C2-local — The same pipeline on the laptop (slides 16–19, ~3 min) — Path B, or Fabric is down

```bash
shape profile orders_day1.parquet -o day1.shape --html day1.html --capture full
shape check day1.shape contracts/orders.json; echo "exit $?"          # passed true, exit 0
shape profile orders_day2.parquet -o day2.shape --capture full
shape check day2.shape contracts/orders.json --json result.json; echo "exit $?"   # exit 1
shape diff day1.shape day2.shape --fail-on-drift; echo "exit $?"      # exit 1
shape check no-such.shape contracts/orders.json; echo "exit $?"       # exit 2 (slide 19)
```

`--capture full`: the contract's `order_total` `min` and `max` need the real values, which the
default (safe) capture withholds, so `shape check` would exit 2; the demo data is synthetic.
The CLI `diff` has no threshold flag, so it reports the new status values, not the mean
shift. For slide 18's threshold, use the REPL snippet on the slide.

**Say:** "These are the same calls the Fabric notebook in the repo makes. Any orchestrator
that reads exit codes can gate on them." Don't say it ran in Fabric.

## C3 — Profile a 1M-row file, with a clock (slide 22, ~2 min)

```bash
time shape profile d2.parquet -o d2.shape
```

Expect one JSON line, `{"shape_content_id": "…", "written": "d2.shape"}`, and the `time`
output. Say "that's my laptop with a projector, not the benchmark machine", and compare with
slide 13 (D2: 1.96 s on 4 cores, not Fabric) without claiming the two match. Don't quote the
live time as a measurement.

## E — Fallbacks

| Symptom | Plan B | Plan C |
|---|---|---|
| C1 slow or errors | Open `prerun/retail_prod.html`; print relationships from `shape.load("prerun/retail_prod.shape")` | Slide 10 shows verified output |
| Fabric session cold or slow (Path A) | Open the saved report under `Files/shape/orders_day1/<timestamp>/`; run the notebook during Q&A | C2-local |
| Pipeline queued (Path A) | Show the last completed runs | C2-local |
| C3 is slow or fails | Show the `prerun/` screenshot of the same command | Slide 13 numbers |
| No network | Path B: C1, C2-local and C3 all run offline | — |
