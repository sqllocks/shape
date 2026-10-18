# Status: "Ship the Shape, Not the Data" — October 3, 2026 version

Current Fabric setup is in the [runbook](../../../integrations/fabric/RUNBOOK.md).
The later [live dry-run findings](../../plans/demo_status/DEMO-LIVE.md) record integration
fixes and outstanding live checks; they are not a timing benchmark for this talk.

Branch `talk/shape-v1-tech-talk`, with `origin/main` merged in (merge commit, no rebase).
Changed: `docs/talks/shape-v1/`, the demo kit (`docs/talks/shape-v1/SCRIPT.md`, `demo/BENCHMARKS.md`,
`demo/build_benchmark_sheet.py`), `tests/demo/content/test_talk_kit.py`, and two new files:
`benchmarks/measure_product.py` and `benchmarks/baselines/2026-09-30-product/product_bench.json`.

## This revision: Shape's own numbers only (owner directive, final)

The talk and everything shown or handed out with it carry Shape's own measured numbers and
nothing else: no comparison of any kind. The slides below were rewritten or edited.

| Slide | Now |
|---|---|
| 7 | "measured on 5 datasets up to 5M rows, output identical across runs"; "engine acceptance checks (P1-08), then engine timings (G1)" |
| 12 | "what every timing in this talk uses" |
| 13 | wall-clock, rows per second, peak memory of `shape.profile`, exact mode (N-20 to N-24) |
| 15 | how the profile is checked: deterministic output (N-26), tests and coverage (N-10), CI matrix (N-09) |
| 20 | "value counts are part of the profile" |
| 21 | memory per dataset; bounded mode **being built, not measured** |
| 22 | live profile of the 1M × 20 file with a clock (C3) |
| 23 | start-up: 11 ms / 239 ms / 294 ms (N-27) |
| 24 | how the numbers were measured, and what isn't measured |
| 25, 26 | plain description of the planned items |
| 27 | no speed or memory claims; slide 13 is the reference point for future engine measurements |
| 28 | "measure before you claim": number → file, snippet → script, claim → status |
| 31 | "How fast is Shape?" answers with slide 13; the privacy answer makes no claim beyond slide 25 |
| B1, B2 | full profiling table; raw runs and start-up |

**Cut, with no replacement:** generation timings and the generation demo (generation isn't in the release), the
"flat memory in bounded mode" number (**no measurement exists**; bounded mode is being
built), and any Fabric number (R11 not done).

**New measurements**, recorded by `benchmarks/measure_product.py` on 2026-09-30 (4 cores,
`Linux-6.18.44-fc-v50`, Python 3.11.15): `shape.profile` exact mode on D1–D4, median of 3
fresh-process runs, profile output hashed and identical across runs; start-up median of 7.
Run-to-run spread on this shared machine is real (D2 Parquet: 2.68 / 1.96 / 1.81 s), so the
talk quotes medians. To re-measure: `source scripts/env.sh && "$SHAPE_VENV/bin/python"
benchmarks/measure_product.py`, then `python demo/build_benchmark_sheet.py`.

## Checks run for this revision (stand-in moved to Shape-generated data, 2026-09-30)

Run with no external checkout on the machine and its environment variable unset.

| Check | Result |
|---|---|
| `REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh` | exit 0, "READY TO DELIVER" |
| `pytest tests/demo/core tests/demo/content` | 133 passed |
| the owner's case-insensitive `git grep` over `docs/talks` and `demo` | empty |

## Demo data generator

`demo/make_data.py` generates all demo data from Shape itself (numpy, pyarrow and Shape; nothing
is read from outside the repo): day-1 and day-2 retail tables and the D2 profiling file
(`demo/d2_table.py`). The "production" stand-in of slides 10 and 20 is its day-1
`customers`, `orders`, `products` and `returns` tables (640,000 rows, `--scale medium`,
seed 42; N-75). The slide-10 call names the tables `customer`, `order`, `product`, `return`,
because the foreign-key rule links `customer_id` to a table called `customer`.

What changed on the talk with that data (all re-measured 2026-09-30, sources in `NUMBERS.md`):

| Was | Now |
|---|---|
| 9 tables, 1,965,400 rows, 8 foreign keys | 4 tables, 640,000 rows, 3 foreign keys (N-75, N-76) |
| "real customer emails" in the `.shape` | every first name, last name, city and state of the customers, and 2 of 47,515 synthetic email addresses (min and max) (N-77) |
| `loyalty_tier` enum (slide 11, B5) | `segment` enum on `customer` |
| `order_total` mean 110.93 → 155.30; shift 0.43 σ | mean 105.94 → 148.31; shift 0.39 σ (N-72, N-74) |

## ⚠️ Owner's to-do before October 3

1. **Fabric dry run (R11): the one pending item you can still do.** Follow
   `integrations/fabric/RUNBOOK.md` **§11 "Owner live dry-run checklist"** (sections 1–10
   for setup), and record every timing as measured in a committed Fabric dry-run timing record (SKU, vCores,
   runtime, row counts, seconds). Then either tell me or add the rows to `NUMBERS.md` as
   N-9x yourself, quoted exactly.
   - **If it's done by October 2:** Path A: C2 runs live in Fabric; slide 14 may quote
     the committed Fabric dry-run timing record rows exactly.
   - **If it isn't:** Path B, the fallback, already written: C1, **C2-local** and C3, all
     on the laptop and offline; **no Fabric timings quoted anywhere**; slide 16 says "the
     same calls run in the Fabric notebook in the repo". `DEMO.md` → "Two paths".
2. **October 2: build the stage laptop and run the gate** (`DEMO.md` §0.2) from `main` at or
   after `b2dd663`. The gate must exit 0:
   `source scripts/env.sh && REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh`
3. **October 2: PyPI check for slide 30.** `pip index versions sqllocks-shape`. If 0.9.0 is
   there (it was on 2026-09-30, F2): `pip install sqllocks-shape`. If not: the repo line.
4. **October 2: re-check the engine tracker** on `build/main-plan` for slide 7's "being
   built" column. Change it only from the tracker.
5. **One full rehearsal with a clock** (checkpoints in `OUTLINE.md`): 45 minutes, section
   6 no longer than 3:30.
6. **Republish the deck** from `DECK.md` (the slides listed there as changed) and fill in B5
   after the rehearsal.
7. **Abstract:** the previous talk's abstract is in use. Check its sentences against the
   list below.
8. Confirm `github.com/sqllocks/shape` is public on the day (slides 1 and 30).

## Earlier owner decisions still applied

- **Date: October 3.** R1–R9 are treated as **not ready**. "How it will work" (slides
  25–27, 3:30) describes the plan's design, chipped PLANNED or BEING BUILT, with no output,
  numbers, timings or transcripts.
- **Kept:** C1 (profile a whole schema), C2 (prod pipeline; Fabric or C2-local), C3.
- **F1 fixed on main (`b2dd663`):** slide 19 shows `shape check no-such.shape …` → exit 2.
- **F6 documented on main:** slide 20 quotes the README and Fabric runbook sentences.
- **Rust engine:** described as being built (slides 7, 12, 27), with no speed or memory
  claims.

## The abstract in use: what Oct 3 can and can't back

The previous talk's abstract text isn't in this repo, so it can't be checked sentence by
sentence. If it is the *Stop Borrowing Contoso* abstract, any sentence with one of these
can't be backed on Oct 3 (F7):

| Claim from the previous talk | Why it can't be backed |
|---|---|
| Any row-count-per-second or "N rows in ~Ns" generation figure | Generation isn't in the release and isn't in this talk; no committed file backs it |
| "top SKU is 39% of order lines", "top 10% of customers carry about 35% of revenue", "9% weekend dip" | Not in any committed file here |
| Any promise of generating from a profile, a privacy-safe profile, a fidelity score, engine speed, or Fabric timings | Planned or unmeasured on Oct 3 (R1–R9, R11) |

If it is the earlier draft of this talk (v1, `ff9e335`): its "live in Fabric notebooks, User
Data Functions and pipeline quality gates" sentence is backed only on Path A (after R11),
and UDFs aren't in the 45-minute demo. Any sentence claiming a field-by-field match with
another tool can't be backed by this talk any more; cut it from the abstract.

## Findings

- **F1: fixed** on `main` in `b2dd663`. Asserted.
- **F2: resolved.** 0.9.0 was published to pypi.org on 2026-09-30; the lead verified a clean
  install. Slide 30 keeps `pip install sqllocks-shape`.
- **F3 / R11: open.** No live Fabric timings; a committed Fabric dry-run timing record has not been recorded for this talk.
- **F5: fixed on `main` in `1b4454d`:** `shape.generate` on a profile raises
  NotImplementedError; `shape plan`/`shape query` on a profile exit 2 with a message. Don't
  demo them.
- **F6: documented** on `main`. The behaviour (real values in a raw `.shape`) is by design
  until P7-01/P7-02.
- **F7:** *Stop Borrowing Contoso*'s figures have no committed source here. None is reused.

## Files

| File | What |
|---|---|
| `OUTLINE.md` | 8 sections, 31 slides, 45-minute timings, the no-dry-run fallback path, cut lines |
| `SCRIPT.md` | Slide-by-slide content and speaker notes |
| `DEMO.md` | C1, C2 (Path A), C2-local (Path B), C3; prep; fallbacks |
| `NUMBERS.md` | Committed numbers only, with source and machine |
| `READINESS.md` | What's gated, and R1–R11 status |
| `ABSTRACT.md` | Previous talk's abstract in use; drafts kept for later |
| `DECK.md` | Source map for the published deck, and what changed in it |
| `verify_snippets.sh` | The delivery gate (`REQUIRE_READY=1`) |
