# Live demo runbook: Ship the Shape, Not the Data

| Demo | Slide | Where | Length | Status |
|---|---|---|---|---|
| **C1** Profile a whole schema | 10 | laptop | ~2 min | runs today (verified) |
| **C2** The production pipeline | 16–19 | Fabric (local fallback C2-local) | ~3 min | verified locally; Fabric needs the owner's dry run (R11) |
| **C3** Generation at scale, then profile it | 22 | laptop | ~2 min | runs today (reference generator, labelled); swap at R6 |
| **D1** Safe shape | 20 | laptop | ~30 s | ⟦PENDING R1–R2⟧ |
| **D2** Generate dev from the shape | 27 | laptop | ~1.5 min | ⟦PENDING R3–R4⟧ |
| **D3** Prove the twin | 28 | laptop | ~1.5 min | ⟦PENDING R4–R5⟧ (the diff half runs today) |
| **D4** Dev-refresh pipeline | 29 | Fabric | show, don't run | ⟦PENDING R9⟧ |

**Stand-in data rule:** every "production" table here is synthetic retail from the
equivalence-verified reference generator. Say so on stage. No real production data.

Every command without a marker was run on 2026-09-30 through `verify_snippets.sh`
(`STATUS.md`). Marked steps use the plan's command forms and must be replaced with the
shipped syntax and re-run before delivery (`READINESS.md`).

## 0. Preparation

### 0.1 Weeks before

1. `REQUIRE_READY=1 … verify_snippets.sh` exits 0, or cut section 6 (`OUTLINE.md` cut line).
2. Owner's Fabric dry run (runbook §11); record timings in `demo/LIVE_TIMINGS.md`.
3. Resolve every ⟦PENDING⟧ marker in this file and in `SCRIPT.md`.

### 0.2 The day before: build the stage folder

From the repo root, Python 3.11+:

```bash
# Shape, the pinned Spindle checkout (the reference generator reads its retail schema)
python3 -m venv ~/.venvs/shape && ~/.venvs/shape/bin/pip install -e ".[dev]" deltalake
source scripts/env.sh && bash benchmarks/vs_spindle/setup_spindle.sh

# Day-1/day-2 orders with documented drift (demo/DRIFT.md)
source scripts/env.sh && ~/.venvs/shape/bin/python demo/make_data.py --out "$BENCH_DATA_DIR/demo"

# The "production" stand-in: retail at medium scale (1,965,400 rows, 9 tables)
source scripts/env.sh && ~/.venvs/shape/bin/python benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42

# Every slide snippet still prints what the slides say
source scripts/env.sh && PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh

# Stage folder
REPO=$PWD; mkdir -p ~/shape-demo/{prod,contracts,prerun} && cd ~/shape-demo
cp "$HOME/bench-out/reference_port/retail/medium/seed42/"*.parquet prod/
cp "$HOME/bench-data/demo/day1/orders.parquet" orders_day1.parquet
cp "$HOME/bench-data/demo/day2/orders.parquet" orders_day2.parquet
cp "$REPO"/demo/contracts/*.json contracts/
```

Rehearse every demo once in `~/shape-demo`. Keep the outputs in `prerun/`
(`retail_prod.shape`, `retail_prod.html`, `day1.shape`, `day2.shape`, `result.json`, and a
screenshot of each Fabric step) for the fallbacks.

### 0.3 30 minutes before

- [ ] Terminal in `~/shape-demo`, venv active, font ≥ 20 pt, `clear`.
      `rm -f *.shape *.html *.json` (keep `prerun/`).
- [ ] Python REPL open in `~/shape-demo` with `import shape` already run (hides import time).
- [ ] Browser: tab 1 `prerun/retail_prod.html`; tab 2 the Fabric workspace.
- [ ] Fabric: `shape_profile` notebook session **already started with 8 vCores**, lakehouse
      attached; day-1 artifact path copied; the last green and red pipeline runs visible.
- [ ] Say it to yourself once: "production here is a stand-in."

## C1 — Profile a whole schema (slide 10, ~2 min)

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
on `customer.email`, the `loyalty_tier` enum, and the relationships.

**Say while it runs:** "Nine tables, just under two million rows. One call."

**Caveats:** `product.category_id` isn't linked to `product_category` (naming rule, slide 9).
Don't quote the run time.

## C2 — The production pipeline (slides 16–19, ~3 min)

Item names, parameters and expected results come from `integrations/fabric/RUNBOOK.md`.

| # | Where | Do | Expect |
|---|---|---|---|
| C2.1 | Notebook `shape_profile` | parameters `tableName = "orders_day1"`, `contractPath = "contracts/orders.json"`, **Run all** | report inline; exit value `passed: true`; artifacts in `Files/shape/orders_day1/<timestamp>/` (point at the timestamped `.shape`: "every run leaves one") |
| C2.2 | Same | `tableName = "orders_day2"`, `baselinePath` = the day-1 artifact, **Run all** | `passed: false`; violations `status allowed_values`, `order_total max`; `drifted: true` |
| C2.3 | Pipeline `shape_gate_notebook` | show the last day-1 (green) and day-2 (red) runs; open the day-2 Fail message | the message lists the violations |

**Caveats:** the notebook's diff uses the default thresholds, so there's no `mean_shift`
entry on day 2 (`DRIFT.md`). If `%%configure` didn't apply, carry on and quote no times.
Only quote Fabric timings from `demo/LIVE_TIMINGS.md`.

### C2-local (non-Fabric venue, or Fabric is down)

```bash
shape profile orders_day1.parquet -o day1.shape --html day1.html
shape check day1.shape contracts/orders.json; echo "exit $?"          # passed true, exit 0
shape profile orders_day2.parquet -o day2.shape
shape check day2.shape contracts/orders.json --json result.json; echo "exit $?"   # exit 1
shape diff day1.shape day2.shape --fail-on-drift; echo "exit $?"      # exit 1
```

The CLI `diff` has no threshold flag, so it reports the new status values, not the mean
shift. For slide 18's threshold, use the REPL snippet on the slide. Don't show exit 2 with a
missing `.shape` (finding F1); use a missing contract if needed.

## C3 — Generation at scale, then profile it (slide 22, ~2 min)

```bash
source scripts/env.sh   # run from the repo root, in a second terminal tab
python benchmarks/vs_spindle/domain_1to1/generate.py --impl reference_port \
    --domain retail --scale medium --seed 42 2>&1 | grep '^wrote'
```

Expect `wrote …/reference_port/retail/medium/seed42 (1,965,400 rows, …s)`. Say "that's my
laptop, not a benchmark", then show slide 23. Optionally re-run C1's profiling call on the
output directory to close the loop.

**⟦PENDING R6⟧** At delivery, if the product engine is READY, replace this with
`shape generate retail --scale medium --seed 42 --format parquet -o gen/` (P4-10 form; confirm
the syntax) and drop the "reference generator" label.

## D1 — Safe shape (slide 20, ~30 s) ⟦PENDING R1–R2⟧

```bash
shape profile validate --safe retail_prod.shape        # expect: FAIL, raw values present
<safe export, command set by P7-01> retail_prod.shape -o retail_safe.shape
shape profile validate --safe retail_safe.shape        # expect: pass
```

Before delivery, add an assertion to `verify_snippets.sh` that `retail_safe.shape` contains
none of `prod/customer.parquet`'s emails (R2).

## D2 — Generate dev from the shape (slides 26–27, ~1.5 min) ⟦PENDING R3–R4⟧

```bash
shape plan retail_safe.shape                                                  # R3
shape generate --from retail_safe.shape --seed 42 --format parquet -o dev/    # R4 (confirm flags)
ls dev/
```

Expect the nine tables in `dev/`. Read two lines of `plan` output aloud: one preserved and
one not modelled.

## D3 — Prove the twin (slide 28, ~1.5 min) ⟦PENDING R4–R5⟧

```python
dev = shape.profile({f.stem: str(f) for f in Path("dev").glob("*.parquet")}, name="retail")
d = shape.diff(shape.load("retail_safe.shape"), dev)
print(d.drifted, len(d.changes))
for c in d.changes[:10]:
    print(c["column"], c["kind"], c["severity"])
```

```bash
shape fidelity …        # R5: shipped syntax; show per-table scores and the exit code
```

The multi-table `diff` runs today (verified with day-1 vs day-2 tables). Only the `dev/`
input is pending. At delivery, rehearse this and decide what to say about any reported
change (it should correspond to a "not modelled" line from `plan`).

## D4 — Dev-refresh pipeline (slide 29) ⟦PENDING R9⟧

Show the PF-06 pipeline definition and its last run in the dev workspace. Don't run it live.

## E — Fallbacks

| Symptom | Plan B | Plan C |
|---|---|---|
| C1 slow or errors | Open `prerun/retail_prod.html`; print relationships from `shape.load("prerun/retail_prod.shape")` | Slide 10 shows verified output |
| Fabric session cold or slow | Open the saved report under `Files/shape/orders_day1/<timestamp>/`; run the notebook during Q&A | C2-local |
| Pipeline queued | Show the last completed runs | Slide 16 diagram |
| C3 fails (for example, no Spindle checkout on the laptop) | Show the `prerun/` generation screenshot | Slide 23 numbers |
| D1–D3 fail live | Show the pre-run outputs (only if they were real runs) | Slides 25–28, "here's what it did in rehearsal" |
| No network | C1, C3, C2-local and D1–D3 all run offline | — |
