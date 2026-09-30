# Outline: Ship the Shape, Not the Data (October 3, 2026)

45 minutes (A1), 31 slides plus backups. Slide numbers match `SCRIPT.md`; numbers cite
`NUMBERS.md`; demos are in `DEMO.md`.

**What changed for Oct 3 (owner decision, 2026-09-30):** none of the pending items R1–R9
will be ready, so everything that depended on them is now a short **"how it will work"**
section (slides 25–27), labelled *planned* or *being built*, with no output, numbers,
timings or CLI transcripts. The talk leans on what runs today: **profiling, deep and at
scale** (sections 3), and **the production pipeline that profiles prod, saves the shape and
gates on check/diff** (section 4). Together they take 25 of the 45 minutes.

## The arc at a glance

| # | Section | Slides | Time | Clock | Live demo |
|---|---|---|---:|---|---|
| 1 | Dev is lying to you | 1–5 | 4:00 | 0:00–4:00 | |
| 2 | Shape as Code, and honest status | 6–7 | 2:30 | 4:00–6:30 | |
| 3 | **Profiling, deep and at scale** | 8–15 | 15:00 | 6:30–21:30 | **C1** profile a whole schema, read the report (local, ~4 min) |
| 4 | **The production pipeline**: profile, save the shape, check, diff, gate | 16–20 | 10:00 | 21:30–31:30 | **C2** Fabric notebook + pipeline gate (~4 min), or **C2-local** |
| 5 | Generation at scale, with the reference benchmark generator | 21–24 | 4:30 | 31:30–36:00 | **C3** 1.97M rows, then profile them (~2 min) |
| 6 | **How it will work** (planned; no demo) | 25–27 | 3:30 | 36:00–39:30 | none |
| 7 | How we know it's right | 28 | 2:00 | 39:30–41:30 | |
| 8 | Status, and three things for Monday | 29–30 | 1:30 | 41:30–43:00 | |
| — | Q&A | 31 + backups | 2:00 | 43:00–45:00 | |

## Section by section

### 1. Dev is lying to you (0:00–4:00)

Point: the two usual ways to fill dev both fail. A copy of production is a compliance
problem; fake data doesn't behave like production, so bugs escape.

- 1: title. 2: about me (20 s).
- 3: the two bad options, side by side.
- 4: callback to *Stop Borrowing Contoso*: the realism spectrum; level 4, "production-
  mirrored", was "can't have it (PII)". This talk: the first half of getting there
  (profiling production, today) and the design for the second half (planned).
- 5: the whole idea in one diagram: prod → profile → shape → (safe shape → generate → dev
  → compare). Chips: **runs today** on profile, save, check, diff; **planned** on safe
  profile, generate from shape, fidelity.

### 2. Shape as Code, and honest status (4:00–6:30)

- 6: "You source-control your schema. You've never source-controlled the shape."
- 7: three columns: **Runs today (0.9.0, early access)** · **Being built (engine branch,
  not released)** · **Planned (work packages)**.

### 3. Profiling, deep and at scale (6:30–21:30): the core

- 8: anatomy of a profile: column fields, table fields, dataset relationships (2 min).
- 9: how the profiler decides: distribution fitting, pattern families, enums, PK/FK rules
  (2 min).
- 10: **live C1**: profile all nine retail tables in one call; foreign keys detected
  across tables (1 min + 2 min demo).
- 11: reading the report, live in the browser (2 min).
- 12: exact vs bounded, and canonical hashing; what's in the release and what's being
  built (1:30).
- 13: profiling at scale, in numbers: **port** numbers only (N-21, N-23, N-24), with the
  10x target labelled as a target and the misses shown (1:30).
- 14: profiling in Fabric today: the driver path, up to 5M rows, then `sampled: true`
  (1:30). Distributed profiling is one line: "planned, slide 27".
- 15: bitwise parity with Spindle on 30 datasets and 31 fields (1:30).

### 4. The production pipeline (21:30–31:30)

Point: profiling belongs in the prod pipeline. Every run leaves a shape behind, and the
gate reads it.

- 16: the pipeline: read prod → profile → save a timestamped `.shape` → check contract →
  diff vs last run → gate. **Live C2** (1 min + 3 min demo).
- 17: day 2 fails the contract, and the result says why (1:30).
- 18: diff against the last run, and the `mean_shift_std` caveat (1:30).
- 19: CLI exit codes, including **exit 2 for a missing `.shape`** (fixed on main, b2dd663)
  (1:00).
- 20: **a raw `.shape` holds real values**: up to 500 per column plus min/max. The README
  and the Fabric runbook now say to treat `.shape` files like the source data. A safe
  profile is planned (slide 25) (2:00).

### 5. Generation at scale (31:30–36:00)

- 21: generate a schema, not columns: parents before children (0:30).
- 22: **live C3**: retail at medium scale, 1,965,400 rows, nine tables, with the
  **reference benchmark generator**: benchmark code, not the product, not in the pip
  package. Then profile the output with slide 10's call (0:30 + 2 min demo).
- 23: generation numbers: **port** only (N-40/N-41), target labelled (1:00).
- 24: why generation equivalence is statistical: T-21 (0:30).

### 6. How it will work (36:00–39:30), planned, no demo

Every slide in this section carries a **PLANNED** or **BEING BUILT** chip. No output, no
numbers, no timings. Command forms are shown only as "planned syntax (may change)".

- 25: rebuild dev from the shape: safe profile (P7-01/P7-02) → `shape plan` → generate
  from the shape (P4-08) (1:30).
- 26: proving the twin: P4-08's acceptance test and the fidelity report (P4-09) (1:00).
- 27: the engine and Fabric at scale: §4 architecture (Rust kernel **being built**, no
  speed claims; targets only as targets), distributed profiling (PF-02), generation
  pipelines (PF-06) (1:00).

### 7. How we know it's right (39:30–41:30)

- 28: equivalence before timing, and the stale-cache bug the harness caught.

### 8. Status and call to action (41:30–43:00)

- 29: what runs today, what's being built, what's planned (the slide 7 table again,
  one line each).
- 30: three things for Monday, all doable with 0.9.0 today.

### Q&A (43:00–45:00)

- 31, plus backups: B1 full profiling table; B2 `results.json`; B3 drift side effects;
  B4 Fabric fallbacks; B5 one column's profile JSON.

## Fallback path (if the owner's Fabric dry run, R11, isn't done)

- Replace C2 with **C2-local** (`DEMO.md`): the same profile → check → diff → exit codes,
  on the laptop. Slide 16 shows the Fabric pipeline as a diagram and names the notebook and
  pipeline in the repo, without claiming it was run live.
- **Quote no Fabric timings at all.** Slide 14 keeps only the platform limits (N-80,
  N-83), which are documentation, not measurements.
- Demos are then **C1, C2-local, C3**, all local and offline. Timings don't change.

## Cut lines

- **Running long at slide 16 (later than 22:30):** shorten C2 to the day-2 gate only.
- **Running long at slide 25 (later than 37:00):** show slide 25 only, and say "slides 26
  and 27 are in the deck online".
- **30 minutes:** drop 2, 9, 12, 14, 24, 26, 27 and 28; C1 only.
- **60 minutes:** add B5, a longer report walk (slide 11), the UDF gate
  (`shape_gate_udf`), and 8 minutes of Q&A. Section 6 stays at 3:30.
- **Non-Fabric venue:** C2-local, and cut slide 14.

## Clock checkpoints

| At slide | Be at |
|---|---|
| 8 | 6:30 |
| 16 | 21:30 (if later than 22:30, shorten C2 to the day-2 gate) |
| 21 | 31:30 |
| 25 | 36:00 (if later than 37:00, slide 25 only) |
| 29 | 41:30 |
