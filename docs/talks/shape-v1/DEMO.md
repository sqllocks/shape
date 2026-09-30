# Live demo runbook

Two live segments: **part A** is local, on the laptop (~3 min, slide 14). **Part B** is
Microsoft Fabric (~6 min, slides 27–28). **Part C** covers fallbacks, and every step has
one. This reuses what the repo already has: `demo/make_data.py`, `demo/contracts/`,
`demo/DRIFT.md`, `demo/TALK.md` and `integrations/fabric/RUNBOOK.md`. Don't invent new
steps on stage.

Every command in part A was run on 2026-09-30 through `verify_snippets.sh` (see
`STATUS.md`). The Fabric steps in part B come from the runbook and were **not** run live by
the author of this file. They need the owner's dry run (runbook §11).

## 0. Preparation

### 0.1 A week before

1. Owner dry run in Fabric: `integrations/fabric/RUNBOOK.md` §§3–7, and the checklist in §11.
   Record timings in `demo/LIVE_TIMINGS.md`, as measured. Nothing on stage may quote a
   Fabric time that isn't there.
2. Check the §10 "[VERIFY]" items in the runbook, especially the pipeline exit-value
   expression `@json(activity('ProfileTable').output.result.exitValue).passed`.
3. Decide whether PyPI is live (A5 in `ABSTRACT.md`). If it isn't, use the
   `sqllocks_shape-0.9.0-py3-none-any.whl` wheel everywhere (the runbook already does, via
   the notebook's *Resources > builtin* folder).

### 0.2 The day before: build the laptop demo folder

From the repo root (Python 3.11+):

```bash
# 1. Shape and the pinned Spindle baseline (make_data.py reads Spindle's retail schema)
python3 -m venv ~/.venvs/shape && ~/.venvs/shape/bin/pip install -e ".[dev]" deltalake
source scripts/env.sh && bash benchmarks/vs_spindle/setup_spindle.sh

# 2. Demo data: day 1 and day 2 (deterministic, seed 42; DRIFT.md)
source scripts/env.sh && ~/.venvs/shape/bin/python demo/make_data.py --out "$BENCH_DATA_DIR/demo"

# 3. Prove every slide snippet still prints what the slides say (must end ALL SNIPPETS OK)
PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh

# 4. The stage folder: short names, big font
mkdir -p ~/shape-demo/contracts && cd ~/shape-demo
cp "$HOME/bench-data/demo/day1/orders.parquet" orders_day1.parquet
cp "$HOME/bench-data/demo/day2/orders.parquet" orders_day2.parquet
cp "$OLDPWD"/demo/contracts/*.json contracts/
```

Then do a full rehearsal of part A in `~/shape-demo`, and **keep its outputs** in
`~/shape-demo/prerun/` (`day1.shape`, `day2.shape`, `day1.html`, `result.json`) for the
fallback.

### 0.3 On the day, 30 minutes before

- [ ] Terminal: `cd ~/shape-demo && source ~/.venvs/shape/bin/activate`. Font ≥ 20 pt, light
      theme, `clear`. Remove old outputs: `rm -f day1.* day2.* result.json`.
- [ ] `shape --help` runs (warms the file cache).
- [ ] Browser tab 1: `~/shape-demo/prerun/day1.html` (fallback). Tab 2: the Fabric workspace.
- [ ] Fabric: the `shape_profile` Python notebook is open, the session is **already
      started with `vCores: 8`**, and `shape_demo` is attached as the default lakehouse.
      The Environment is already published. Never publish on stage.
- [ ] Fabric: day-1 artifact path copied from the dry run (runbook §4 step 5), ready to
      paste into `baselinePath`.
- [ ] Pipeline `shape_gate_notebook`: the last day-1 (green) and day-2 (red) runs are
      visible in the run history (fallback).
- [ ] Wi-Fi fails? Part A needs no network. Go straight to plan B for part B.

## Part A — Local CLI (slide 14, ~3 minutes)

Type these, or paste them from a notes window. Expected results are in the right-hand
column; the output shown is abridged.

| # | Command | Expect | Say |
|---|---|---|---|
| A1 | `shape profile orders_day1.parquet -o day1.shape --html day1.html --json day1.json` | `{"shape_content_id": "…", "written": "day1.shape"}`, exit 0 | "Profile yesterday's file and save the artifact, a report and a summary." |
| A2 | open `day1.html` in the browser | per-column report | "Self-contained HTML, works offline." (~20 s; scroll to `status` and `order_total`) |
| A3 | `shape check day1.shape contracts/orders.json; echo "exit $?"` | `{"passed": true, "violations": []}`, `exit 0` | "Day 1 meets the contract." |
| A4 | `shape profile orders_day2.parquet -o day2.shape` | exit 0 | "Today's file." |
| A5 | `shape check day2.shape contracts/orders.json --json result.json; echo "exit $?"` | `"passed": false`, violations `status allowed_values` (`unexpected_values: ["lost"]`) and `order_total max` (observed `7189.882`), `exit 1` | "Fails, and says exactly why. Exit 1 is what the pipeline sees." |
| A6 | `shape diff day1.shape day2.shape --fail-on-drift; echo "exit $?"` | changes: `status new_categorical_values`, `order_total new_categorical_values`; `exit 1` | "Diff without writing a rule. It found the new status." |

Optional, if you have 40 seconds more (Python, shows the threshold caveat from slide 12):

```python
import shape
today = shape.profile("orders_day2.parquet", name="orders")
d = shape.diff(shape.load("day1.shape"), today, thresholds={"mean_shift_std": 0.25})
for c in d.changes:
    print(c["column"], c["kind"], c["severity"])
```

Expect `status new_categorical_values low`, `order_total mean_shift medium`,
`order_total new_categorical_values low`.

**Known caveats for part A (say them if they come up; don't hide them):**

- The CLI `diff` uses the default thresholds and has no threshold flag, so it does **not**
  report the +40% `order_total` mean shift (0.43 σ < 0.5 default; `DRIFT.md`). The contract
  catches it on `max`.
- `order_total new_categorical_values` is a side effect: the profiler keeps a value list for
  this float column (as Spindle does), so every day-2 value is "new". Its severity is low.
- **Don't demonstrate exit 2 with a missing `.shape` file.** It currently exits 1 with a
  traceback (finding F1, `STATUS.md`). Use a missing contract instead:
  `shape check day1.shape contracts/nope.json; echo "exit $?"` → `shape: error: …`, `exit 2`.
- The first `shape profile` in a cold terminal takes longer than later ones. Don't
  comment on the time. It's local and not a benchmark.

## Part B — Fabric (slides 27–28, ~6 minutes)

All item names, parameters and expected results are from `integrations/fabric/RUNBOOK.md`.
Timings are only what `demo/LIVE_TIMINGS.md` says, if anything.

| # | Where | Do | Expect | Say |
|---|---|---|---|---|
| B1 | Notebook `shape_profile` | Show the parameter cell: `tableName = "orders_day1"`, `contractPath = "contracts/orders.json"`, `baselinePath = ""`. **Run all** | HTML report inline; exit value `"passed": true`, `"violations": []`, `"drifted": false`; artifacts in `Files/shape/orders_day1/<timestamp>/` | "Same three verbs, reading the Delta table in place." |
| B2 | Same notebook | Set `tableName = "orders_day2"`, paste the day-1 `.shape` path into `baselinePath`, **Run all** | `"passed": false`; violations as in part A; `"drifted": true`; `changes` lists the new status values | "Today's table against yesterday's artifact." |
| B3 | Pipeline `shape_gate_notebook` | Run with `tableName = orders_day1` | Succeeded; `CheckGate` takes the True branch | "Green, and the load continues." |
| B4 | Same pipeline | Run with `tableName = orders_day2` | **Failed** at `FailGate`; the message lists the violations | "Stopped at the gate, with the reason in the error." |
| B5 *(60-min version only)* | Pipeline `shape_gate_udf` | `filePath = demo/day2/orders.parquet` | **Failed** at `CheckContract` with the violations | "Same gate, no notebook session: two function calls." |

While B3/B4 run (pipelines take time to queue), go back to slide 28 and talk through the
diagram. Come back to the run when it finishes.

**Known caveats for part B:**

- The notebook's diff uses the **default** thresholds, so `changes` on day 2 won't include
  `mean_shift` for `order_total`. The gate fails on the contract regardless (`DRIFT.md`).
- A Python notebook defaults to 2 vCores. If `%%configure {"vCores": 8}` didn't apply, the
  profile still finishes, just more slowly. Don't quote any speed-up (runbook §4 step 4,
  `TALK.md`).
- The UDF refuses files above 50 MB by design (240 s limit). For B5 use the `orders` file,
  never `d2.parquet` (far above the cap).
- The PySpark notebook (`shape_profile_spark`) gives the same exit JSON as the Python
  notebook. Mention it and don't run it, unless it's the 60-minute version.

## Part C — Fallbacks

| Symptom | Plan B | Plan C |
|---|---|---|
| Laptop terminal problem in part A | Show `~/shape-demo/prerun/` outputs: open `day1.html`, `cat prerun/result.json` | Walk slides 9–12; they show the verified output |
| `shape` not found | `~/.venvs/shape/bin/shape …` | as above |
| Fabric session cold or slow (B1) | Open the saved report from `Files/shape/orders_day1/<timestamp>/` and narrate; run the notebook during Q&A | Run part A's commands against the local copies and say "this is what the notebook runs" |
| `%%configure` didn't apply | Proceed without quoting times | Stop the session and rerun the configure cell first (only if there's time) |
| Pipeline queued or slow (B3/B4) | Show the last completed day-1 and day-2 runs in the run history | Explain from slide 28 |
| Pipeline expression error (`exitValue`) | Open the Notebook activity output and read `passed` and `violations` there | Slide 28 |
| UDF cold start or timeout | Show the saved result of a prior run | Skip B5 |
| No network at all | Part A as normal; for part B show screenshots from the dry run (put them in the deck as hidden slides) | Say: "Here's what it looks like; the runbook is in the repo" |

## Part D — After the talk

- Delete the day-2 test artifacts from `Files/shape/` if the workspace is shared.
- If the audience found anything that broke, open an issue while you remember it.
- If you quoted live timings, check they match `demo/LIVE_TIMINGS.md` exactly.
