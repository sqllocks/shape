# Status: Shape 0.9.0 tech talk

Branch `talk/shape-v1-tech-talk` (from `main` @ `764cb37`). Written 2026-09-30 by an
unattended session. Only `docs/talks/shape-v1/` was changed. Nothing in `src/`, `tests/`,
`benchmarks/`, `integrations/`, `demo/`, `docs/plans/` or CI was touched. No tags, and no
PyPI actions.

## Done

| File | What |
|---|---|
| `ABSTRACT.md` | 3 title options, 150-word and 50-word abstracts, audience, level, 5 takeaways, assumptions A1–A8, open questions |
| `OUTLINE.md` | The arc from problem to call to action; 33 slides in 8 sections with timings and clock checkpoints; cut lines for 30 and 60 minutes and for a non-Fabric venue |
| `SCRIPT.md` | Slide-by-slide notes (what's on the slide, what to say, transitions) for 33 slides plus 4 backups, Q&A prep, and wording rules |
| `DEMO.md` | Live demo runbook: prep, part A local CLI, part B Fabric (from `integrations/fabric/RUNBOOK.md`), fallbacks for every step, known caveats |
| `NUMBERS.md` | Every number used, by ID, with source file and machine (M1/M2); targets labelled; Fabric limits separated from measurements |
| `verify_snippets.sh` | Runs every slide snippet and asserts the output the slides show |

## What was verified, and how (all in this session)

1. **Environment.** Python 3.11.15. `~/.venvs/shape` with `pip install -e ".[dev]"` plus
   `deltalake` (pyarrow 25.0.1, numpy 2.4.6). The pinned Spindle baseline was installed with
   `benchmarks/vs_spindle/setup_spindle.sh` (Spindle 3.0.1 @ `422e78d`, cloned read-only
   into `~/spindle`, not modified).
2. **Demo data.** `source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"`
   wrote day 1 (10 files) and day 2 (3 files).
3. **Slide snippets.** `PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh`
   gave **exit 0, ALL SNIPPETS OK**. It covers the Python snippets on slides 9–13, the Delta
   table snippet on slide 27 (run against a local Delta table written with `deltalake`, not in
   Fabric), and the CLI snippets on slide 14 with exit codes 0 / 1 / 1 / 2. The day-2
   violations (`status allowed_values` with `lost`; `order_total max` observed 7189.882),
   the default-threshold diff *missing* `mean_shift`, and `mean_shift_std: 0.25` catching it
   all match `demo/DRIFT.md`.
4. **Published artifact.** The same script against `sqllocks-shape==0.9.0` installed from
   **TestPyPI** into a clean venv also gave **exit 0**.
5. **DEMO.md part A** was replayed as written (stage folder, each command, the optional
   Python diff), and every expected result matched.
6. **In-progress architecture (slides 15–20).** A worktree of `build/main-plan` @ `ef6a8cd`
   was built with maturin (Rust 1.94.1). `shape._kernel.version()` gave `0.9.0` (kernel
   `rust`). `pytest tests/kernel` gave **96 passed** under `SHAPE_KERNEL=rust` and **96
   passed** under `SHAPE_KERNEL=python`. That covers the zero-copy buffer-address tests,
   hashing and sketches. A spot check found `hash_value(1) == hash_value(1.0)`, NaN and
   null hashing to `None`, and identical `hash_column` output from the Rust kernel and the
   Python twin. No code from this branch appears on a slide.
7. **Numbers.** Each entry in `NUMBERS.md` was read from its source file:
   `profile_bench.json`, `retail_bench.json`, `results.json`, `DM-01_verify_output.txt`
   (30 PASS, 34 field rows, `EXIT=0`), `GATE-G0.md`, `DRIFT.md`, plan §§2–3 and §12, and
   commit `3b7c1f0`.
8. **Wording.** A grep for GA, production-ready, certified, successor and "replaces" finds them
   only in the rules that forbid them.

**Not verified:** anything that runs live in Fabric (it needs the owner's workspace; runbook
§§10–11), and `pip install sqllocks-shape` from pypi.org (see F2).

## Findings for the owner

- **F1: a missing first `.shape` argument exits 1, not 2.**
  `shape check no-such.shape contracts/orders.json` prints a `FileNotFoundError` traceback
  and exits 1, and `shape diff no-such.shape day2.shape` also exits 1. §12.2 requires 2 for
  an input error. A missing contract file, a missing second `diff` argument, and a missing
  `profile` source all correctly exit 2. Cause: `src/shape/cli/main.py:182-185` sends
  `check`/`diff` to the new path only when `_artifact_kind()` of the first argument returns
  `"profile"`. For a missing file it returns `None`, so the call falls through to the legacy
  branch, which isn't wrapped by `_run()`. In a pipeline, a missing artifact would look like a failed
  contract. Not fixed here (outside this branch's scope). The talk avoids this case, and
  `verify_snippets.sh` reports it without failing.
- **F2: `sqllocks-shape` isn't on pypi.org yet.** On 2026-09-30, `https://pypi.org/pypi/sqllocks-shape/json`
  returns 404, while TestPyPI has 0.9.0. Slides 8 and 32 say `pip install sqllocks-shape`,
  and speaker notes flag the check. Either publish before the talk (Morning summary, owner
  step 3) or switch those slides to the release wheel.
- **F3: no live Fabric timings.** `demo/LIVE_TIMINGS.md` is still a placeholder, so the talk
  quotes no Fabric time. Slide 29 has a marked space for them.
- **F4: the stale-cache fix is only on `build/main-plan`** (`3b7c1f0`), not on `main`. The
  talk cites the commit on that branch. That's fine, but a reader following the link from
  `main` won't find it until the next merge.

## Assumptions made (from `ABSTRACT.md`)

45 minutes; data and analytics engineers; a Fabric-heavy room; live demo included (local
plus Fabric, each with a fallback); the owner's dry run happens before the talk; Spindle
appears only as the retired baseline; the Rust work is shown as in progress with no speed
numbers.

## Open questions for the owner

1. Length and venue: are 45 minutes and a Fabric-heavy audience right? (`OUTLINE.md` has
   cut lines.)
2. Live demo: keep both segments, or run Fabric from the pre-run artifacts?
3. PyPI (F2): publish before the abstract goes out?
4. Live Fabric numbers (F3): after the dry run, show `LIVE_TIMINGS.md` rows on slide 29?
5. F1: fix before the talk?
6. Title: which of the three in `ABSTRACT.md`?
7. Deck format: this branch has the script, not rendered slides. Should the deck be built
   from `SCRIPT.md` (for example as a slide artifact or `.pptx`) as a follow-up?

## Re-checking before the talk

```bash
source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh   # must end ALL SNIPPETS OK
```

If `results.json`, the baselines or `LIVE_TIMINGS.md` change, update `NUMBERS.md` first,
then the slides that cite the changed IDs.
