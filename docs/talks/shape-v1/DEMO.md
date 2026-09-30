# Live demo runbook: Ship the Shape, Not the Data (October 3, 2026)

| Demo | Slide | Where | Length | Status |
|---|---|---|---|---|
| **C1** Profile a whole schema, read the report | 10–11 | laptop | ~4 min | runs today (verified) |
| **C2** The production pipeline | 16–19 | Fabric | ~3 min | verified locally; **Fabric only if the owner's dry run (R11) is done** |
| **C2-local** The same pipeline on the laptop | 16–19 | laptop | ~3 min | runs today (verified); **the default if R11 isn't done** |
| **C3** Generation at scale, then profile it | 22 | laptop | ~2 min | runs today, with the **reference benchmark generator** (benchmark code, not the product) |

There are **no** demos for section 6 ("how it will work"). The former D1–D4 (safe shape,
generate from a shape, fidelity, dev-refresh pipeline) are cut: none of their features
exists on October 3 (R1–R9 in `READINESS.md`). Don't show pre-made output for them, and
don't type their planned commands on stage.

**Stand-in data rule:** every "production" table here is synthetic retail from the
equivalence-verified reference benchmark generator. Say so on stage. No real production
data.

Every command here was run on 2026-09-30 through `verify_snippets.sh` (`STATUS.md`).

## Two paths

| | **Path A: dry run done** (R11 ✅) | **Path B: dry run not done** (fallback) |
|---|---|---|
| Demos | C1, **C2 in Fabric**, C3 | C1, **C2-local**, C3 |
| Network | Fabric workspace | none needed |
| Slide 14 | may show `LIVE_TIMINGS.md` rows, read exactly, with SKU and vCores | **no Fabric timings**; platform limits only |
| Slide 16 | "this is the notebook running in Fabric" | "the same calls run in the Fabric notebook in the repo"; show the pipeline as a diagram |

Decide on **October 2**: Path A only if `demo/LIVE_TIMINGS.md` has every cell filled in and every
runbook §11 box is ticked. Otherwise Path B. Either way the talk's timings don't change.

## 0. Preparation

### 0.1 Before October 2

1. Owner's Fabric dry run (`integrations/fabric/RUNBOOK.md` §11); record timings in
   `demo/LIVE_TIMINGS.md`. (If it doesn't happen: Path B.)
2. `REQUIRE_READY=1 … verify_snippets.sh` exits 0 on the laptop you'll present from.
3. One full rehearsal with a clock (checkpoints in `OUTLINE.md`).

### 0.2 The day before: build the stage folder

From the repo root, Python 3.11+, on `main` at or after `b2dd663` (slide 19's exit 2 for a
missing `.shape` needs it; the TestPyPI 0.9.0 wheel predates it):

```bash
# Shape, the pinned Spindle checkout (the reference generator reads its retail schema)
python3 -m venv ~/.venvs/shape && ~/.venvs/shape/bin/pip install -e ".[dev]" deltalake
source scripts/env.sh && bash benchmarks/vs_spindle/setup_spindle.sh

# Day-1/day-2 orders with documented drift (demo/DRIFT.md)
source scripts/env.sh && ~/.venvs/shape/bin/python demo/make_data.py --out "$BENCH_DATA_DIR/demo"

# The "production" stand-in: retail at medium scale (1,965,400 rows, 9 tables)
source scripts/env.sh && ~/.venvs/shape/bin/python benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42

# Every slide snippet still prints what the slides say; the delivery gate
source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh

# Stage folder
REPO=$PWD; mkdir -p ~/shape-demo/{prod,contracts,prerun} && cd ~/shape-demo
cp "$HOME/bench-out/reference_port/retail/medium/seed42/"*.parquet prod/
cp "$HOME/bench-data/demo/day1/orders.parquet" orders_day1.parquet
cp "$HOME/bench-data/demo/day2/orders.parquet" orders_day2.parquet
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
from pathlib import Path
prod = {f.stem: str(f) for f in Path("prod").glob("*.parquet")}
p = shape.profile(prod, name="retail")
shape.save(p, "retail_prod.shape")
for r in p.summary()["relationships"]:
    print(r["child"], r["child_columns"], "->", r["parent"])
```

Expect 8 lines, including `order ['customer_id'] -> customer` and
`order_line ['product_id'] -> product`. Then:

```python
open("retail_prod.html", "w").write(p.to_html())
```

and open it in the browser (slide 11). Point at a fitted distribution, the `email` pattern
on `customer.email`, the `loyalty_tier` enum, and the relationships. On `customer.email`,
point at the top values: "real values; slide 20."

**Say while it runs:** "Nine tables, just under two million rows. One call."

**Caveats:** `product.category_id` isn't linked to `product_category` (naming rule, slide 9).
Don't quote the run time.

## C2 — The production pipeline in Fabric (slides 16–19, ~3 min) — Path A only

Item names, parameters and expected results come from `integrations/fabric/RUNBOOK.md`.

| # | Where | Do | Expect |
|---|---|---|---|
| C2.1 | Notebook `shape_profile` | parameters `tableName = "orders_day1"`, `contractPath = "contracts/orders.json"`, **Run all** | report inline; exit value `passed: true`; artifacts in `Files/shape/orders_day1/<timestamp>/` (point at the timestamped `.shape`: "every run leaves one") |
| C2.2 | Same | `tableName = "orders_day2"`, `baselinePath` = the day-1 artifact, **Run all** | `passed: false`; violations `status allowed_values`, `order_total max`; `drifted: true` |
| C2.3 | Pipeline `shape_gate_notebook` | show the last day-1 (green) and day-2 (red) runs; open the day-2 Fail message | the message lists the violations |

**Caveats:** the notebook's diff uses the default thresholds, so there's no `mean_shift`
entry on day 2 (`DRIFT.md`). If `%%configure` didn't apply, carry on and quote no times.
Only quote Fabric timings from `demo/LIVE_TIMINGS.md`.

## C2-local — The same pipeline on the laptop (slides 16–19, ~3 min) — Path B, or Fabric is down

```bash
shape profile orders_day1.parquet -o day1.shape --html day1.html
shape check day1.shape contracts/orders.json; echo "exit $?"          # passed true, exit 0
shape profile orders_day2.parquet -o day2.shape
shape check day2.shape contracts/orders.json --json result.json; echo "exit $?"   # exit 1
shape diff day1.shape day2.shape --fail-on-drift; echo "exit $?"      # exit 1
shape check no-such.shape contracts/orders.json; echo "exit $?"       # exit 2 (slide 19)
```

The CLI `diff` has no threshold flag, so it reports the new status values, not the mean
shift. For slide 18's threshold, use the REPL snippet on the slide.

**Say:** "These are the same calls the Fabric notebook in the repo makes. Any orchestrator
that reads exit codes can gate on them." Don't say it ran in Fabric.

## C3 — Generation at scale, then profile it (slide 22, ~2 min)

```bash
source scripts/env.sh   # run from the repo root, in a second terminal tab
python benchmarks/vs_spindle/domain_1to1/generate.py --impl reference_port \
    --domain retail --scale medium --seed 42 2>&1 | grep '^wrote'
```

Expect `wrote …/reference_port/retail/medium/seed42 (1,965,400 rows, …s)`. Say "this is
benchmark code, not the product", and "that's my laptop, not a benchmark", then show slide
23. Optionally re-run C1's profiling call on the output directory to close the loop.

## E — Fallbacks

| Symptom | Plan B | Plan C |
|---|---|---|
| C1 slow or errors | Open `prerun/retail_prod.html`; print relationships from `shape.load("prerun/retail_prod.shape")` | Slide 10 shows verified output |
| Fabric session cold or slow (Path A) | Open the saved report under `Files/shape/orders_day1/<timestamp>/`; run the notebook during Q&A | C2-local |
| Pipeline queued (Path A) | Show the last completed runs | C2-local |
| C3 fails (for example, no Spindle checkout on the laptop) | Show the `prerun/` generation screenshot | Slide 23 numbers |
| No network | Path B: C1, C2-local and C3 all run offline | — |
