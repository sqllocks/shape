# Readiness for October 3, 2026

Current Fabric setup is in the [runbook](../../../integrations/fabric/RUNBOOK.md).
The later [live dry-run findings](../../plans/demo_status/DEMO-LIVE.md) record integration
fixes and outstanding live checks; they are not a timing benchmark for this talk.

**Delivery date: October 3, 2026** (owner, 2026-09-30). On that date the planned items
R1–R9 won't be ready, so the talk presents them only as **"how it will work"** (slides
25–27): the plan's design, labelled *planned* or *being built*, with no output, numbers,
timings or transcripts. They are **not gates** any more.

**Delivery gate (must exit 0 on the presenting laptop):**

```bash
source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh
```

It asserts every claim the Oct 3 talk makes about code that runs today (part 1), and that
no `TBD` or PENDING marker is left in the talk files and slide 22's live profile (C3) really ran
(part 2). Part 3 reports the planned items, the dry run and PyPI for information only.
**On 2026-09-30 it exits 0** (`STATUS.md`).

## What the talk claims runs today (gated by part 1)

| Claim | Slides | How it's checked |
|---|---|---|
| Profile a whole schema (4 tables, 640,000 rows), 3 FKs detected; save/load round trip | 10 | asserted |
| Self-contained HTML report | 11 | no external URLs in `to_html()` |
| A raw `.shape` holds real values | 20 | every customer first name, last name, city and state, and the min and max email, found in `retail_prod.shape` |
| README and Fabric runbook warn to treat `.shape` like the source data | 20 | the quoted sentences are grepped in `README.md` and `integrations/fabric/RUNBOOK.md` |
| Contract check on day 2 fails with the two violations shown | 17 | asserted |
| Diff: default threshold misses the +40% shift; 0.25 catches it | 18 | asserted |
| Profiling a Delta table directory (the notebook's path); the Fabric items named on slides exist | 14, 16 | asserted |
| CLI exit codes 0 / 1 / 1 / 2, and **exit 2 for a missing `.shape`** in `check` and `diff` | 19, C2-local | asserted |
| `shape profile` on the 1M-row, 20-column file exits 0 and writes `d2.shape` | 22 | asserted (not skipped under `REQUIRE_READY=1`) |
| Every timing, rows-per-second, memory and start-up figure in `SCRIPT.md` is in `product_bench.json` (truncated), its output was identical across runs, and `demo/BENCHMARKS.md` is current | 13, 21, 23 | asserted |

## Items

| ID | What | Plan WP / gate | Oct 3 status | In the talk |
|---|---|---|---|---|
| R1 | Safe-profile export and validation | P7-01 | **NOT READY** (planned) | slide 25, "how it will work" |
| R2 | k-anonymity, small-cell suppression | P7-02 | **NOT READY** (planned) | slide 25 |
| R3 | `shape plan` | P4-08 | **NOT READY** (planned) | slide 25 |
| R4 | Generate from a `.shape` | P4-08 | **NOT READY** (planned) | slides 25, 26 |
| R5 | Fidelity report | P4-09 | **NOT READY** (planned) | slide 26 |
| R6 | Retail through the product engine | P4-07, P4-10 | **NOT READY** (planned) | not shown; no generation is demoed |
| R7 | Engine and bounded-mode timings and memory | G1, G4 | **NOT READY**: none measured | slides 21, 27 say "being built, not measured". Slides 13, 21, 23 show `shape.profile` exact mode, which **is** measured |
| R8 | Distributed Spark profiling | PF-02 | **NOT READY** (planned) | slide 14 one line; slide 27 |
| R9 | Generation pipelines | PF-06 | **NOT READY** (planned) | slide 27 |
| R10 | Missing `.shape` exits 2 (finding F1) | — | **READY**: fixed on `main` in `b2dd663`; asserted | slide 19 shows it |
| R11 | **Owner's Fabric dry run**, a committed Fabric dry-run timing record | runbook §11 | **NOT DONE** on 2026-09-30. The owner's to-do (`STATUS.md`) | Done → Path A (C2 in Fabric; timings quoted exactly). Not done → **Path B**: C2-local, no Fabric timings (`DEMO.md`) |
| F2 | `sqllocks-shape` on pypi.org | owner | **not on pypi.org** at 2026-09-30 16:59 UTC (HTTP 404; TestPyPI has 0.9.0) | slide 30 install line decided on Oct 2 |

The Rust engine (P1-01a to P1-06 done on `build/main-plan` @ `bc40cc3`, not merged, not
released) is described on slides 7, 12 and 27 as **being built**, with no speed claims.

## If a planned item lands before October 3

Part 3 will print `READY … the talk still says planned`. Don't upgrade a slide on the day.
Leave it as "how it will work" unless there's time to add a runnable, asserted snippet to
part 1 and a committed number to `NUMBERS.md`.
