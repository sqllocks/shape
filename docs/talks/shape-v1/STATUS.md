# Status: "Ship the Shape, Not the Data"

Branch `talk/shape-v1-tech-talk` (from `main` @ `764cb37`). Only `docs/talks/shape-v1/`
is changed. No product, test, benchmark, integration, demo, plan or CI files were touched.

## History

1. **v1 (2026-09-30):** a talk about profile / check / diff and Fabric quality gates.
2. **v2 (2026-09-30, this version), after the owner's review of *Stop Borrowing Contoso*:**
   refocused on **data profiling** (deep, and at scale), a **production pipeline that
   profiles prod and saves the shape**, **generation at scale**, and **rebuilding dev from
   the shape without production data**. The owner chose to write it **for the date when
   profile → generate (P4-08) and the safe profile (P7-01/P7-02) have shipped**, and to show
   generation at scale live with the reference generator, labelled. v1 is in git history.

## Files

| File | What |
|---|---|
| `ABSTRACT.md` | 3 titles; an as-delivered abstract **and** a submission-safe variant; short abstract; audience; takeaways; assumptions A1–A8; open questions |
| `OUTLINE.md` | 8 sections, 33 slides, timings (profiling about 20 of 45 minutes); cut lines, including one for pending items that slip |
| `SCRIPT.md` | Slide-by-slide notes; every slide that depends on unbuilt work is marked ⟦PENDING Rn⟧ |
| `DEMO.md` | Demos C1–C3 (run today), D1–D4 (pending); prep, caveats, fallbacks |
| `NUMBERS.md` | Every number, with source and machine; profiler rules N-65..N-69; pending numbers; old-talk numbers not to reuse |
| `READINESS.md` | R1–R11: every dependency, its work package, slides, and how it's checked |
| `verify_snippets.sh` | Part 1 asserts every runnable snippet; part 2 reports each pending item. `REQUIRE_READY=1` is the go/no-go check |

## Verified in this session (2026-09-30)

- Environment: Python 3.11.15; `~/.venvs/shape` (`pip install -e ".[dev]"` + `deltalake`,
  pyarrow 25.0.1); pinned Spindle 3.0.1 @ `422e78d` via `setup_spindle.sh` (read only).
- Data: `demo/make_data.py` (day 1/day 2), and the reference generator at retail medium
  (`generate.py --impl reference_port --seed 42`: 1,965,400 rows, 9 tables).
- `verify_snippets.sh`: **part 1 all asserted and passing, exit 0.** It covers the
  multi-table profile of all 9 tables with 8 FKs, save/load round trip, the HTML report,
  the raw-profile PII claim, check/diff day 1 vs day 2, the Delta table path, CLI exit codes
  0/1/1/2, and live generation of 1,965,400 rows. **Part 2: 10 items PENDING**, as expected.
  `REQUIRE_READY=1` exits 1.
- The v1 snippets also passed against the TestPyPI `sqllocks-shape==0.9.0` wheel. v2's
  snippets were run against `main` only.
- `build/main-plan` @ `ef6a8cd` (Rust kernel) was built, and `tests/kernel` passed 96/96 in
  both `SHAPE_KERNEL=rust` and `python` modes. It backs the hashing and sketches statements
  on slide 12 (as "built on the engine branch"). No branch code is on a slide.
- Profiler rules on slide 9 were read from `src/shape/profile/reference/`.

Not verified: anything live in Fabric (R11), and all pending items (R1–R9).

## Findings for the owner

- **F1: a missing first `.shape` argument exits 1, not 2.** `shape check no-such.shape c.json`
  and `shape diff no-such.shape b.shape` exit 1 with a traceback. The cause is
  `src/shape/cli/main.py:182-185`: it routes to the new path only when `_artifact_kind()`
  returns `"profile"`, so a missing file falls through to the legacy branch. Tracked as R10.
- **F2: `sqllocks-shape` isn't on pypi.org** (404; TestPyPI has 0.9.0).
- **F3: no live Fabric timings** (`demo/LIVE_TIMINGS.md` is a placeholder). Tracked as R11.
- **F4: the stale-cache fix** cited on slide 30 is commit `3b7c1f0`, on `build/main-plan` only.
- **F5: nothing on `main` can generate from a profile, and two legacy commands mislead.**
  `shape.generate(profile.to_dict())` returns placeholder strings (`'value_0'`, …) for every
  column. `shape.generate(profile)` raises AttributeError. `shape plan <0.9.0 .shape>`
  crashes (KeyError `shape.json`, exit 1). `shape generate` is a two-column toy
  (`--rows`, `--seed`). None of these appears in the talk. P4-08/P4-10 replace them.
- **F6: a raw `.shape` contains real values.** It keeps the top-500 value counts per column,
  by design for Spindle parity, plus real min/max strings. In the stand-in, **500 real customer
  email addresses** were found in the saved artifact. The Fabric notebooks write raw `.shape`
  files into the lakehouse `Files/` area, so anyone who can read that folder can read those
  values. Worth a warning in the product docs now, before the safe profile (P7-01/P7-02)
  exists. The talk's slide 20 is built on this.
- **F7: *Stop Borrowing Contoso*'s timings don't match the committed baselines.** For
  example, "19.6M rows in ~42s" against 102.64 s for Spindle large on the 4-core baseline
  machine. None of them is reused (`NUMBERS.md`, last section).

## Open questions for the owner

1. **Delivery date**, checked against the tracker: realistically after G4 plus P7-01/P7-02.
2. **Which abstract to submit:** as-delivered (it promises unbuilt features) or
   submission-safe.
3. If an R-item slips: **cut section 6 to "how it will work", or move the talk?**
4. Should findings F1, F5 and F6 be fixed or documented before the talk? (Outside this
   branch's scope.)
5. Confirm the stand-in-only rule: no real customer profile is ever shown.
6. Fabric-heavy room and 45 minutes: still right?
7. Do you want rendered slides next (for example a deck built from `SCRIPT.md`)?

## Re-check

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42
PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh                    # today
REQUIRE_READY=1 PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh    # delivery gate
```
