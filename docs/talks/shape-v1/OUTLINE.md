# Outline: Ship the Shape, Not the Data

45 minutes (A1), 33 slides plus backups. Slide numbers match `SCRIPT.md`; numbers cite
`NUMBERS.md`; demos are in `DEMO.md`. Anything marked **⟦PENDING Rn⟧** waits on an item in
`READINESS.md`.

**Profiling is the centre:** sections 3 and 4 (slides 8–20) take about 20 of the 45
minutes.

## The arc at a glance

| # | Section | Slides | Time | Clock | Live demo |
|---|---|---|---:|---|---|
| 1 | Dev is lying to you | 1–5 | 4:30 | 0:00–4:30 | |
| 2 | Shape as Code | 6–7 | 2:30 | 4:30–7:00 | |
| 3 | **Profiling, deep and at scale** | 8–15 | 12:00 | 7:00–19:00 | **C1** profile a whole schema (local, ~3 min) |
| 4 | **The production pipeline**: profile, check, save the shape | 16–20 | 7:00 | 19:00–26:00 | **C2** Fabric notebook + pipeline gate (~4 min) |
| 5 | Generation at scale | 21–24 | 5:00 | 26:00–31:00 | **C3** 1.97M rows, then profile them (~2 min) |
| 6 | Rebuild dev from the shape ⟦PENDING R1–R5, R9⟧ | 25–29 | 7:30 | 31:00–38:30 | **D1–D3** safe shape → plan → generate → prove (~4 min) |
| 7 | How we know it's right | 30 | 2:00 | 38:30–40:30 | |
| 8 | Status, and three things for Monday | 31–32 | 2:00 | 40:30–42:30 | |
| — | Q&A | 33 + backups | 2:30 | 42:30–45:00 | |

## Section by section

### 1. Dev is lying to you (0:00–4:30)

Point: the two usual ways to fill dev both fail. A copy of production is a compliance
problem; fake data doesn't behave like production, so bugs escape.

- 1: title. 2: about me (20 s).
- 3: the two bad options, side by side.
- 4: callback to *Stop Borrowing Contoso*: the realism spectrum. Level 4, "production-
  mirrored", was marked "can't have it (PII)". This talk: level 4's behaviour, without its
  rows.
- 5: the whole idea in one diagram: prod → profile → **safe** shape → generate → dev, with
  a check on both ends. Each arrow carries a status chip.

### 2. Shape as Code (4:30–7:00)

- 6: "You source-control your schema. You've never source-controlled the shape." A `.shape`
  file is declared, versioned, diffable and executable.
- 7: status on delivery day (from `READINESS.md`, filled in at delivery).

### 3. Profiling, deep and at scale (7:00–19:00) — the core

Point: what a profile knows, how each part is computed, how we know it's right, and how it
scales.

- 8: anatomy of a profile: column fields, table fields, dataset relationships.
- 9: how the profiler decides things: type inference, distribution fitting (sample, fit,
  full-column refit for `fit_score`), pattern families, enum detection, PK and FK rules.
- 10: **live C1**: profile all nine retail tables in one call; foreign keys detected
  across tables.
- 11: reading the report.
- 12: exact vs bounded, and canonical hashing. How the same profile works on a file, a
  stream and a partitioned table.
- 13: profiling at scale, in numbers. Port numbers today (N-21..N-25, with their 10x
  misses); product numbers **⟦PENDING R7⟧**; targets labelled.
- 14: profiling at scale in Fabric: the driver path today (up to 5M rows, then `sampled`);
  distributed per-partition profiles merged on the driver **⟦PENDING R8⟧**.
- 15: bitwise parity with Spindle on 30 datasets and 34 fields. That's why you can trust
  every field on slide 8.

### 4. The production pipeline (19:00–26:00)

Point: profiling belongs in the prod pipeline. Every run leaves a shape behind.

- 16: the pipeline: read prod → profile → save a timestamped `.shape` → check contract →
  diff vs last run → gate → publish the safe shape **⟦PENDING R1–R2⟧**. **Live C2** starts.
- 17: day 2 fails the contract, and the result says why.
- 18: diff against the last run, and the `mean_shift_std` caveat.
- 19: CLI exit codes, which is how any orchestrator gates.
- 20: **raw vs safe**: a raw profile contains real values (top 500 value counts, min/max)
  by design. Only a safe profile leaves prod **⟦PENDING R1–R2⟧**.

### 5. Generation at scale (26:00–31:00)

- 21: generate a schema, not columns: parents before children; every FK points at a real
  key.
- 22: **live C3**: retail at medium scale, 1,965,400 rows across nine tables, with the
  reference generator (labelled; product engine swap **⟦PENDING R6⟧**). Then profile it
  with slide 10's call.
- 23: generation at scale in numbers: N-40/N-41 (port, 4 cores, after T-21 passed);
  product **⟦PENDING R7⟧**; targets.
- 24: why generation equivalence is statistical: T-21, Spindle's own seed spread, fixed
  seed set.

### 6. Rebuild dev from the shape (31:00–38:30) ⟦PENDING R1–R5, R9⟧

- 25: the flow: safe shape → `shape plan` → `shape generate --from` → dev lakehouse →
  profile → compare.
- 26: `shape plan`: what will and won't be preserved. This is the honesty feature.
- 27: **live D2**: generate the dev environment from the safe shape.
- 28: **live D3**: prove the twin: profile dev and compare with the prod shape
  (`shape fidelity`; P4-08's acceptance test is profile → generate → profile within T-22
  tolerances for modelled fields).
- 29: the dev-refresh pipeline: prod publishes the safe shape; dev regenerates on a
  schedule (PF-06). Nothing sensitive moves.

### 7. How we know it's right (38:30–40:30)

- 30: equivalence before timing, and the stale-cache bug the harness caught.

### 8. Status and call to action (40:30–42:30)

- 31: what shipped and what's next (streaming, plugins, Synapse/ADF).
- 32: three things for Monday.

### Q&A (42:30–45:00)

- 33, plus backups: B1 full profiling table; B2 `results.json`; B3 drift side effects;
  B4 Fabric fallbacks; B5 what a profile field list looks like in JSON.

## Cut lines

- **If R1–R5 aren't all READY by the date:** replace section 6 with slides 25 and 26 only,
  shown as "how it will work", with no live demo and "planned" chips. Move 3 minutes to
  section 3, and put the submission-safe abstract in the programme. Or move the talk.
- **30 minutes:** drop 2, 9, 12, 14, 24 and 30; C1 only as a recording.
- **60 minutes:** add B5 and a deeper walk through the report (slide 11), show the UDF gate,
  and take 8 minutes of Q&A.
- **Non-Fabric venue:** replace C2 with the local CLI (`DEMO.md` C2-local) and cut slide 14.

## Clock checkpoints

| At slide | Be at |
|---|---|
| 8 | 7:00 |
| 16 | 19:00 (if later, shorten C2 to the pipeline gate only) |
| 21 | 26:00 |
| 25 | 31:00 (if later than 33:00, skip D3's live run and show the pre-run comparison) |
| 31 | 40:30 |
