# Outline: Green Pipelines, Changed Data

45 minutes (assumption A1 in `ABSTRACT.md`), 33 slides plus backups. Slide numbers match
`SCRIPT.md`. Numbers cite `NUMBERS.md` IDs. Demo steps are in `DEMO.md`.

## The arc at a glance

| # | Section | Slides | Time | Clock | Demo |
|---|---|---|---:|---|---|
| 1 | The problem: pipelines succeed while data silently changes | 1–4 | 4:00 | 0:00–4:00 | |
| 2 | Shape as Code: a portable, executable description of how data behaves | 5–7 | 4:00 | 4:00–8:00 | |
| 3 | profile / check / diff | 8–14 | 8:00 | 8:00–16:00 | **Live 1: local CLI, ~3 min** (slide 14) |
| 4 | How it's built | 15–20 | 7:00 | 16:00–23:00 | |
| 5 | How we know it's right | 21–25 | 7:00 | 23:00–30:00 | |
| 6 | Fabric in practice | 26–29 | 8:00 | 30:00–38:00 | **Live 2: Fabric, ~6 min** (slides 27–28) |
| 7 | Roadmap | 30–31 | 2:30 | 38:00–40:30 | |
| 8 | Call to action | 32 | 1:00 | 40:30–41:30 | |
| — | Q&A | 33 (+ backups B1–B4) | 3:30 | 41:30–45:00 | |

## Section by section

### 1. The problem (0:00–4:00)

Point: a green pipeline run tells you the code ran. It doesn't tell you the data is the
data you expected.

- Slide 1: title and one-line promise.
- Slide 2: the green run. A pipeline succeeded on day 2, yet four things changed in the data.
- Slide 3: the four day-2 changes (N-70 to N-73): null rate 4.97% → 20%, new status `lost`,
  amounts × 1.40, duplicate SKUs.
- Slide 4: why the usual checks miss them. The schema, row count and types are unchanged,
  so no error is thrown.

Transition: "What if the expected behaviour of the data were itself an artifact you could
check in?"

### 2. Shape as Code (4:00–8:00)

Point: capture behaviour (distributions, value sets, keys, patterns), not only structure,
as a portable, executable file.

- Slide 5: the definition, and how it relates to infrastructure as code: declared,
  versioned, diffable, executable in CI and pipelines.
- Slide 6: what a profile contains. Per column: dtype, null rate, cardinality, uniqueness,
  PK/FK, distribution family and parameters, pattern, min/max/mean/std, quantiles, value
  counts, temporal histograms. Across tables: foreign keys.
- Slide 7: **the honesty slide.** Early access 0.9.0. What ships today and what is in
  progress. No "GA", "production-ready" or "certified".

Transition: "Three verbs cover most of it."

### 3. profile / check / diff (8:00–16:00)

Point: a small, stable API (plan §12.2, final for 1.0) and a CLI with exit codes made for
pipelines.

- Slide 8: install and the three verbs.
- Slide 9: `shape.profile` → `shape.save` → `.shape`; `summary()`.
- Slide 10: a contract (the real `demo/contracts/orders.json`); day 1 passes.
- Slide 11: day 2 fails, and the result names the rule, expected and observed values.
- Slide 12: `shape.diff`, including the `mean_shift_std` caveat (N-74). Say it out loud.
- Slide 13: HTML report and the `.shape` artifact (self-contained, offline, fails closed on
  unsafe containers).
- Slide 14: the CLI and exit codes 0/1/2. **Live demo 1** (local, ~3 min, `DEMO.md` part A).

Transition: "That's the surface. Here's what's underneath, and what's changing underneath."

### 4. How it's built (16:00–23:00)

Point: Arrow for data, Python for orchestration, a Rust kernel for the per-batch hot path,
and a pure-Python twin for every kernel function as the correctness oracle.

- Slide 15: the layer diagram (plan §4.1). Say what ships in 0.9.0 (the pure-Python
  profiler) and what is being built on `build/main-plan`.
- Slide 16: the reference twin. The profiler in 0.9.0 **is** the reference implementation
  (DM-1, T-03). `SHAPE_KERNEL=auto|rust|python`. Differential tests.
- Slide 17: zero-copy FFI through the Arrow PyCapsule interface (pyo3 + pyo3-arrow); the
  acceptance test compares buffer addresses (N-63).
- Slide 18: canonical hashing: seeded XXH3-64, canonicalisation rules (N-62). Why never
  Python `hash()`.
- Slide 19: exact vs bounded. `exact=True` for files and tables, which parity and every
  timing use. Bounded mode (HLL/KLL/SpaceSaving, N-60/N-61) is for streams and data larger
  than RAM. One output schema; `error_models` records which mode ran.
- Slide 20: runtime split rules: Python never touches individual values on a hot path; one
  native call per batch covers every column; plugins take whole batches.

Transition: "A faster engine is worthless if it gives different answers. So how do we know
it's right?"

### 5. How we know it's right (23:00–30:00)

Point: equivalence before timing. The harness is the product's conscience, and it caught
the harness itself.

- Slide 21: the rule (plan §6.4): no timing counts until that workload's equivalence
  verifier passes on the timed output. Never lower a gate, never fabricate.
- Slide 22: bitwise parity with Spindle 3.0.1 (pinned, `422e78d`): 30/30 datasets, 34
  fields each, bitwise (N-01, N-02).
- Slide 23: two standards. T-22 for profiling is exact, with tolerances (N-03). T-21 for
  generation is statistical, uses Spindle's own seed-to-seed spread, and fixes the seed set
  (N-04 to N-06).
- Slide 24: the stale-cache bug. The Spindle output cache was keyed by dataset name, D1 was
  regenerated, and the result was 41 mismatches on each D1 file (N-07). The fix was to key
  the cache by SHA-256 of the input. The lesson: a stale cache could equally have produced
  a false pass.
- Slide 25: the numbers we're allowed to say (N-21, with N-24/N-25 misses; targets N-50
  labelled; "port, not product; 4 cores; not Fabric").

Transition: "Now let's run it where the data lives."

### 6. Fabric in practice (30:00–38:00)

Point: the same three verbs, in the four Fabric surfaces, with honest limits.

- Slide 26: the four surfaces (Python notebook, Environment + PySpark notebook, User Data
  Functions, pipelines) and the platform limits (N-80 to N-84).
- Slide 27: notebook profiling a lakehouse Delta table. **Live demo 2** starts here
  (`DEMO.md` part B).
- Slide 28: the pipeline gate. Day 1 is green; day 2 fails at the gate with the reasons.
- Slide 29: the UDF gate and its limits (240 s, 50 MB cap, pure wheel under 28.6 MB).
  Anything larger goes through the notebook. If `LIVE_TIMINGS.md` has rows, show them here,
  exactly as measured.

Transition: "What's next, and what we won't claim until it's measured."

### 7. Roadmap (38:00–40:30)

- Slide 30: the phases. Profiling engine (Rust kernel, in progress: FFI, wheels, hashing,
  sketches, readers and type inference done on `build/main-plan`; fused kernel, engine and
  parity on Rust next); plugins (API v1, first-party features built the same way);
  Fabric/Synapse/ADF pipeline integration; stream profiling; generation with realistic
  distributions; streaming during generation.
- Slide 31: the gates that decide "done" (targets N-50 to N-53), and what we won't say
  until each gate is met.

### 8. Call to action (40:30–41:30)

- Slide 32: try it (`pip install sqllocks-shape`, once published; see A5), run the demo
  locally, bring a table and a contract, open issues, and follow the build in public.

### Q&A (41:30–45:00)

- Slide 33, plus backups: B1 full profiling table (N-20 to N-25), B2 `results.json` run
  (N-30 to N-35), B3 drift side effects from `DRIFT.md`, B4 Fabric fallbacks.

## Cut lines

- **30 minutes:** drop slides 6, 17, 20 and 31; shorten section 5 to slides 21, 22 and 24;
  keep only one live demo (Fabric if it's a Fabric venue, else local).
- **60 minutes:** add B1 as a main slide, show the PySpark notebook and the UDF path live
  (`DEMO.md` B4–B5), and take 10 minutes of Q&A.
- **Non-Fabric venue:** shrink section 6 to slides 26 and 28 (2 minutes) and give the time
  to section 4 (the reference twin and hashing) and section 5.

## Timing checkpoints (glance at the clock)

| At slide | You should be at |
|---|---|
| 8 | 8:00 |
| 15 | 16:00 (if later, skip the report detail on 13 next time and do the local demo faster) |
| 21 | 23:00 |
| 26 | 30:00 (if later than 32:00, run plan B in `DEMO.md`: show the pre-run pipeline) |
| 30 | 38:00 |
