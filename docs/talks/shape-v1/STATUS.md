# Status: "Ship the Shape, Not the Data" — October 3, 2026 version

Branch `talk/shape-v1-tech-talk`, with `origin/main` @ `b2dd663` merged in (`dfa2c96`).
Only `docs/talks/shape-v1/` is changed. No product, test, benchmark, integration, demo,
plan or CI files were touched.

## ⚠️ Owner's to-do before October 3

1. **Fabric dry run (R11): the one pending item you can still do.** Follow
   `integrations/fabric/RUNBOOK.md` **§11 "Owner live dry-run checklist"** (sections 1–10
   for setup), and record every timing as measured in `demo/LIVE_TIMINGS.md` (SKU, vCores,
   runtime, row counts, seconds). Then either tell me or add the rows to `NUMBERS.md` as
   N-9x yourself, quoted exactly.
   - **If it's done by October 2:** Path A: C2 runs live in Fabric; slide 14 may quote
     `LIVE_TIMINGS.md` rows exactly.
   - **If it isn't:** Path B, the fallback, already written: C1, **C2-local** and C3, all
     on the laptop and offline; **no Fabric timings quoted anywhere**; slide 16 says "the
     same calls run in the Fabric notebook in the repo". `DEMO.md` → "Two paths".
2. **October 2: build the stage laptop and run the gate** (`DEMO.md` §0.2) from `main` at or
   after `b2dd663`. Not from the TestPyPI 0.9.0 wheel, which predates the exit-2 fix on
   slide 19. The gate must exit 0:
   `source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh`
3. **October 2: PyPI check for slide 30.** Run `pip index versions sqllocks-shape`. If
   0.9.0 is there: `pip install sqllocks-shape`. If not: the "install from the repo" line.
   Part 3 of the gate also prints this.
4. **October 2: re-check the engine tracker** on `build/main-plan` for slide 7's "being
   built" column. Change it only from the tracker.
5. **One full rehearsal with a clock** (checkpoints in `OUTLINE.md`): 45 minutes, section
   6 no longer than 3:30.
6. **Slides:** the deck is built: https://claude.ai/artifact/BizWK3bVD8chFsQBUK4oBd
   (private until you share it from the page's Share menu). 31 slides + backups B1–B5,
   mapped to `SCRIPT.md` in `DECK.md`. To do: review it, fill in B5 after the rehearsal,
   and on October 2 confirm slide 30's install line (see `DECK.md`).
7. **Abstract:** you're using the previous talk's abstract. Check its sentences against the
   list below, and paste its text into `ABSTRACT.md` if you'd like me to check it word by
   word.
8. Confirm `github.com/sqllocks/shape` is public on the day (it's on slides 1 and 30).

## What changed for the October 3 version

Owner decisions of 2026-09-30, applied:

- **Date: October 3.** R1–R9 are treated as **not ready**.
- **"How it will work"** replaces every former ⟦PENDING R1–R9⟧ slide and demo. Section 6 is
  now three slides (25–27, 3:30) describing the plan's design (§4 architecture, P7-01/02,
  P4-08, P4-09, PF-02, PF-06), each chipped PLANNED or BEING BUILT, with no output, numbers,
  timings or transcripts. Command forms appear once, small, as "planned syntax (may
  change)". Demos **D1–D4 are cut**.
- **Kept:** C1 (profile a whole schema), C2 (prod pipeline; Fabric or C2-local), C3
  (generation at scale), with C3 labelled **"reference benchmark code, not the product"**.
- **Timings rebalanced** (`OUTLINE.md`): profiling 15:00 (was 12:00), prod pipeline 10:00
  (was 7:00), generation 4:30, how it will work 3:30 (was 7:30 of live demos). 31 slides
  (was 33).
- **Rust engine:** described as being built (slides 7, 12, 27). No speed claims; ≥10x and
  ≥30x appear only labelled as targets.
- **Numbers:** product "TBD" columns removed from slides 13 and 23 (port only, labelled).
  `NUMBERS.md` now holds only committed numbers with source and machine: removed N-11 (a
  test count from a session run, not a committed file), N-64 (no machine named), the
  pending-numbers table, and the old-talk numbers (now in F7 below).
- **F1 is fixed on main (`b2dd663`)**: slide 19 now shows `shape check no-such.shape …` →
  exit 2, and the talk says why that matters. R10 → **READY**.
- **F6 is documented on main**: slide 20 is now "a raw `.shape` holds real values; treat it
  as you would the source data", quoting the README and Fabric runbook sentences exactly.
  The safe profile is a PLANNED chip pointing to slide 25.
- **`verify_snippets.sh`:** `REQUIRE_READY=1` now gates exactly what the Oct 3 talk claims:
  part 1 asserts every runnable snippet and "runs today" statement (new: exit 2 for a
  missing `.shape` in `check` and `diff`, the quoted README/runbook sentences, the Fabric
  item names on slides 14/16, the 9 tables' 1,965,400 rows, and that every port timing on
  slides 13/23 appears in the committed baselines while `results.json` still has
  `"shape": null`). Part 2 fails the gate on any `TBD` or PENDING marker in the talk files or
  a skipped generation. Part 3 reports R1–R9, R11 and PyPI for information only; a planned
  item that turns READY is flagged "update slides first", not silently claimed.
- `ABSTRACT.md`: note at the top, "Using the previous talk's abstract for Oct 3." Drafts
  kept, not rewritten.
- `READINESS.md`: rewritten as the Oct 3 readiness table.

## Verified in this session (2026-09-30), and how

Environment (fresh container): Python 3.11.15; `~/.venvs/shape` = `pip install -e ".[dev]"`
+ `deltalake` 1.6.6 (pyarrow 25.0.1, numpy 2.4.6); pinned Spindle via
`benchmarks/vs_spindle/setup_spindle.sh` → `~/spindle` @ `422e78d` (read only).
Data: `demo/make_data.py --out ~/bench-data/demo`; `generate.py --impl reference_port
--domain retail --scale medium --seed 42` → 1,965,400 rows.

| Check | Command | Result |
|---|---|---|
| Delivery gate | `source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh` | **exit 0**, "CLAIMS OK", "READY TO DELIVER" |
| Part 1 highlights | (same run) | 8 FKs across 9 tables; raw `.shape` contains 500 of the first 5,000 customer emails; README/runbook sentences found; day 2 → `status allowed_values`, `order_total max 7189.882`; diff 0.25 → `order_total mean_shift medium`; Delta path max 5135.63; CLI exits 0/0/0/1/1/2/**2/2**; generation wrote 1,965,400 rows; slide 13/23 numbers found in `benchmarks/baselines/2026-09-29/`; `results.json` `"shape": null` |
| Part 3 (info) | (same run) | R1–R9 planned (R1 probe exit 2, R3 exit 1, R6 exit 2); **R11 not done** (`LIVE_TIMINGS.md` still all TBD); F2 not on PyPI |
| The gate isn't vacuous | appended "TBD" to `OUTLINE.md`, re-ran, restored | exit 1, "NOT READY TO DELIVER (talk text)" |
| Old-talk numbers aren't in the baselines | searched the committed baseline JSONs for 42.0, 19.6 | not found (26.51, 14.89 found) |
| F1 fix | `shape check no-such.shape …`; `shape diff no-such.shape day2.shape` | both exit 2, `shape: error: [Errno 2] …` |
| F2 / PyPI | `pip index versions sqllocks-shape`; `curl https://pypi.org/pypi/sqllocks-shape/json` | **not on pypi.org** (no distribution; HTTP 404), checked **2026-09-30 16:59 UTC and again 17:07 UTC**. TestPyPI lists only `0.9.0` |
| Engine status (slide 7) | `git show origin/build/main-plan:docs/plans/COMPLETION_PLAN.md` tracker | P1-01a..P1-06 done, P1-07 onward todo; `build/main-plan` @ `bc40cc3`, not merged to `main` |
| Stale-cache fix (slide 28) | `git branch -r --contains 3b7c1f0` | `origin/build/main-plan` only (F4) |
| Plan text for section 6 | read P4-08, P4-09, P7-01, P7-02, PF-02, PF-06 and §4 in `docs/plans/COMPLETION_PLAN.md` | slides 25–27 paraphrase them; no claim beyond the plan text |

Not verified: anything live in Fabric (R11), and nothing in section 6 runs (by design).

## The abstract in use: what Oct 3 can and can't back

The owner is using the previous talk's abstract. **Its text isn't in this repo or its
history** (only mentions of *Stop Borrowing Contoso*), so I couldn't check it sentence by
sentence. What I can say:

**A. If it's the *Stop Borrowing Contoso* abstract**, any sentence with one of these can't be
backed on Oct 3 (F7):

| Claim from the previous talk | Why it can't be backed |
|---|---|
| "1.97M rows in ~2s" / "500k orders in about 2s" (medium) | Committed Spindle medium: 5.29 s (N-40, machine M1); 5.79 s (N-35, M2) |
| "19.6M rows in ~42s" (large) | Committed Spindle large: 102.64 s (N-41, M1) |
| "21.7k rows in 0.08s" (small) | Committed Spindle small: 0.25 s (N-34, M2) |
| "top SKU is 39% of order lines", "top 10% of customers carry about 35% of revenue", "9% weekend dip" | Not in any committed file here |
| Any promise of generating from a profile, a privacy-safe profile, a fidelity score, product or Rust speed, or Fabric timings | Planned or unmeasured on Oct 3 (R1–R9, R11) |

**B. If it's the earlier draft of this talk** (v1, commit `ff9e335`,
`docs/talks/shape-v1/ABSTRACT.md`), sentence by sentence:

| Sentence | Oct 3 |
|---|---|
| "Pipelines often succeed while the data inside them changes: null rates creep up, a new status value appears, an amount shifts by 40%." | ✅ `DRIFT.md`, slides 17–18 |
| "Shape is an open-source (MIT) Python library, now in early access…" | ✅ `LICENSE` is MIT |
| "You profile a table once, save a `.shape` file, check new data against a small JSON contract, and diff two profiles…" / "The CLI returns exit codes a pipeline can gate on." | ✅ slides 16–19 |
| "This session shows Shape **live in Microsoft Fabric notebooks, User Data Functions and pipeline quality gates**." | ❌ **Can't back as written.** Live Fabric only on Path A (after R11); UDFs aren't in the 45-minute demo (only the 60-minute cut line). On Path B nothing is live in Fabric |
| "…Arrow data, a pure-Python reference implementation, a Rust kernel in progress, and a benchmark harness that checks equivalence before it records any timing." | ✅ as "in progress" (slides 27–28) |
| "Shape's profiler matches the retired Spindle profiler field for field, bitwise, on 30 datasets." | ✅ N-01, slide 15 |
| "You'll leave knowing what ships today, what doesn't yet, and how to try it." | ✅ (install line decided Oct 2, F2) |
| Short abstract: "See it gate Microsoft Fabric pipelines live" | ❌ only on Path A |

Neither version mentions profile → generate, so the talk's section 6 is extra, not a broken
promise. The v1 draft doesn't mention generation at scale, which C3 adds.

## Findings

- **F1: fixed** on `main` in `b2dd663`; `check`/`diff` with a missing first `.shape` exit 2.
  Asserted.
- **F2: open.** `sqllocks-shape` is not on pypi.org (checked 2026-09-30 16:59 and 17:07 UTC);
  TestPyPI has 0.9.0 only. Re-check October 2.
- **F3 / R11: open.** No live Fabric timings; `demo/LIVE_TIMINGS.md` is a placeholder.
- **F4:** the stale-cache fix on slide 28 is commit `3b7c1f0`, on `build/main-plan` only.
- **F5: unchanged, not on any slide.** `shape plan` on a 0.9.0 artifact still exits 1 (part 3
  probe); `shape.generate(profile.to_dict())` returns placeholders; `shape generate` is a
  two-column toy. Don't demo any of them.
- **F6: documented** on `main` (README "What a `.shape` file contains"; Fabric runbook
  warning). Slide 20 quotes both. The underlying behaviour (real values in a raw `.shape`)
  is by design until P7-01/P7-02.
- **F7:** *Stop Borrowing Contoso*'s timings don't match the committed baselines (table
  above). None is reused.

## Remaining questions for the owner

1. **Which "previous talk's abstract"?** *Stop Borrowing Contoso*'s, or this talk's v1 draft
   (`ff9e335`)? If it's the v1 draft, its "live in Fabric notebooks, User Data Functions"
   sentence needs the dry run (Path A) and a UDF step, or the talk will under-deliver on it.
   Want UDF step C2.4 added to Path A?
2. Still 45 minutes, and a Fabric-heavy room?
3. Do you want a rendered deck next (from `SCRIPT.md`)?

## Files

| File | What |
|---|---|
| `OUTLINE.md` | 8 sections, 31 slides, 45-minute timings, the no-dry-run fallback path, cut lines |
| `SCRIPT.md` | Slide-by-slide content and speaker notes for Oct 3; section 6 is "how it will work" |
| `DEMO.md` | C1, C2 (Path A), C2-local (Path B), C3; prep; fallbacks |
| `NUMBERS.md` | Committed numbers only, with source and machine |
| `READINESS.md` | What's gated, and R1–R11 status for Oct 3 |
| `ABSTRACT.md` | Note: previous talk's abstract in use; drafts kept for later |
| `verify_snippets.sh` | The delivery gate (`REQUIRE_READY=1`) |

## Re-check

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42
source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh
```
