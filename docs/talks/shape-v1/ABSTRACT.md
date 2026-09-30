# Talk abstract: Shape 0.9.0 (early access)

Speaker: Jonathan Stewart (SQLLocks). Draft for the owner to edit; the assumptions and open
questions at the end need answers before submission.

## Title options

1. **Green Pipelines, Changed Data: Shape as Code for Microsoft Fabric**
2. **Equivalence Before Speed: Building Shape, a Data Profiler You Can Trust**
3. **Your Data Has a Shape. Put It in Version Control.**

Recommendation: option 1 for a Fabric or data-engineering venue, option 2 for an
engineering or Python venue.

## Abstract (150 words)

Pipelines often succeed while the data inside them changes: null rates creep up, a new
status value appears, an amount shifts by 40%. Row counts and schemas still look fine.
Shape is an open-source (MIT) Python library, now in early access, that captures how data
behaves as a portable, executable artifact: Shape as Code. You profile a table once, save a
`.shape` file, check new data against a small JSON contract, and diff two profiles to see
what drifted. The CLI returns exit codes a pipeline can gate on. This session shows Shape
live in Microsoft Fabric notebooks, User Data Functions and pipeline quality gates. It also
opens up how Shape is built: Arrow data, a pure-Python reference implementation, a Rust
kernel in progress, and a benchmark harness that checks equivalence before it records any
timing. Shape's profiler matches the retired Spindle profiler field for field, bitwise, on
30 datasets. You'll leave knowing what ships today, what doesn't yet, and how to try it.

## Short abstract (50 words)

Pipelines succeed while data quietly changes. Shape, an open-source Python library in early
access, profiles data into a portable `.shape` artifact. You check it against contracts and
diff it for drift. See it gate Microsoft Fabric pipelines live, and see how equivalence
testing against Spindle keeps its profiler honest.

## Audience

Data engineers and analytics engineers who build or run pipelines, especially on Microsoft
Fabric (lakehouses, notebooks, Data Factory pipelines). The architecture section also
suits Python library authors curious about Arrow, Rust extensions and benchmarking
discipline.

## Level

Intermediate (200–300). Attendees should know Python and have run a data pipeline. No Rust
knowledge is needed.

## Key takeaways

1. **A profile is more useful than a schema.** Null rates, value sets, ranges and
   distributions catch changes that type checks and row counts miss.
2. **Three verbs cover most of the job:** `profile`, `check` (against a JSON contract),
   and `diff` (between two profiles). The CLI's exit codes (0 pass, 1 failed check or
   drift, 2 input error) drop straight into a pipeline.
3. **In Fabric today**, Shape runs in Python and PySpark notebooks, in a User Data Function,
   and as a pipeline quality gate. Each surface has limits, and you'll see them.
4. **Equivalence comes before timing.** Check that outputs match, field by field, before
   recording any speed number. Shape's harness caught a bug in itself this way.
5. **Early access means early access.** Profiling, contracts, diff, reports and the Fabric
   integration work now. Generation, plugins, streaming and the Rust engine are in
   progress, and the talk says which is which.

## Assumptions made for this draft (change any of them)

| # | Assumption | Where it matters |
|---|---|---|
| A1 | **45 minutes** including ~4 minutes of Q&A | `OUTLINE.md` timings |
| A2 | Audience: data and analytics engineers, **mostly Microsoft Fabric users** | Examples, depth of Fabric section |
| A3 | **Live demo included**: a local CLI demo (~3 min) plus a Fabric demo (~7 min), each with a recorded or pre-run fallback | `DEMO.md` |
| A4 | The owner's Fabric dry run (`integrations/fabric/RUNBOOK.md` §11) is done before the talk. Until it is, **no Fabric timing is quoted**, because `demo/LIVE_TIMINGS.md` is still empty | `NUMBERS.md`, slide 25 |
| A5 | `sqllocks-shape` 0.9.0 is on **pypi.org** by the talk date. On 2026-09-30 it is only on TestPyPI (pypi.org returns 404) | Slides 8 and 32 |
| A6 | The venue allows the speaker's own laptop, with a local fallback that needs no network | `DEMO.md` |
| A7 | Spindle is mentioned only as the retired predecessor and the benchmark baseline, never as "the thing Shape succeeds" | Wording throughout |
| A8 | The Rust kernel work on `build/main-plan` is shown as **in progress**, with no speed claims | Slides 15–20, 30 |

## Open questions for the owner

1. **Length:** is it 45 minutes? `OUTLINE.md` has cut lines for 30 and 60 minutes.
2. **Venue and audience:** which conference, and how Fabric-heavy is the room? If it is a
   general Python venue, use title 2 and shorten the Fabric section (cut line in `OUTLINE.md`).
3. **Live demo, yes or no?** If the venue network or Fabric capacity is doubtful, run the
   local demo live and show the Fabric part from the pre-run artifacts (`DEMO.md`, plan B).
4. **PyPI:** will the real publish (Morning summary, owner step 3) happen before the
   abstract goes out? If not, the call to action says "install from the GitHub release wheel".
5. **Live Fabric numbers:** after the dry run, do you want slide 25 to show the measured
   `LIVE_TIMINGS.md` rows? They may only be shown as measured, with SKU and vCores.
6. **Finding F1** (`STATUS.md`): `shape check` with a missing `.shape` file exits 1 with a
   traceback instead of 2. It is not on the demo path, but in a pipeline it looks like a failed
   contract. Fix before the talk (outside this branch's scope), or leave it as is?
7. **Repo visibility:** the talk links to `github.com/sqllocks/shape`. Confirm it is public
   on the day.
