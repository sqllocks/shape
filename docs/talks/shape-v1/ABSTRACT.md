# Talk abstract: Ship the Shape, Not the Data

> **Using the previous talk's abstract for Oct 3.** (Owner decision, 2026-09-30.) The drafts
> below are kept for later and are **not** in use. Their "as delivered" version promises
> features that won't exist on October 3. What the previous abstract can and can't be
> backed by on Oct 3 is listed in `STATUS.md` ("The abstract in use").

Speaker: Jonathan Stewart (SQLLocks). Draft for the owner to edit.

This talk is written for **the date when profile → generate and the safe profile have
shipped** (owner decision, 2026-09-30). The dependency list is in `READINESS.md`. It is the
follow-up to *Stop Borrowing Contoso*. That talk ended its realism spectrum at level 4,
"production-mirrored: can't have it (PII)". This one is about getting level 4's
**behaviour** without level 4's **rows**, and making data profiling the centre of the story.

## Title options

1. **Ship the Shape, Not the Data: Production-Shaped Dev Environments Without Production Data**
2. **Profile Prod, Rebuild Dev: Data Profiling at Scale with Shape**
3. **Level 4 Without the PII: Profiling, Shape as Code, and Synthetic Dev Environments**

Recommendation: option 1 for Fabric and data-platform venues. Option 3 if the audience saw
*Stop Borrowing Contoso*.

## Abstract, as delivered (150 words)

Dev environments lie. Either they hold a copy of production, with the compliance risk that
brings, or they hold fake data that doesn't behave like production, so bugs surface after
release. Shape, an open-source Python library, takes a third route: profile production,
save how the data behaves as a portable `.shape` artifact, and rebuild dev from that
artifact. This session goes deep on profiling at scale: what a full statistical profile
captures (types, distributions, patterns, keys and cross-table relationships), how fast it is
and how much memory it uses, and how it runs in a Microsoft
Fabric pipeline that profiles production, checks contracts, flags drift and publishes a
privacy-safe shape. Then we generate a multi-table dev environment from that shape, at
scale, and prove it matches by profiling the result. You'll see what's measured, what's
preserved, and what isn't.

## Submission-safe variant (150 words; use if you submit before R1–R6 are READY)

Dev environments lie. Either they hold a copy of production, with the compliance risk that
brings, or they hold fake data that doesn't behave like production. Shape, an open-source
Python library in early access, takes a third route: profile production and save how the
data behaves as a portable `.shape` artifact. This session goes deep on data profiling at
scale: what a full statistical profile captures, how fast Shape's profiler runs and how much
memory it uses, measured on five datasets up to five million rows, and how it runs in a Microsoft
Fabric pipeline that profiles production data, checks contracts and flags drift. We'll also
walk through the workflow Shape is building to rebuild dev environments from a production shape without
moving production rows: what it must preserve, how privacy is enforced, and how we test
that the result matches.

## Short abstract (50 words)

Copying production into dev is risky; fake data lies. Shape profiles production into a
portable, privacy-safe `.shape` artifact and rebuilds dev from it. See data profiling at
scale, a Fabric pipeline that profiles prod and gates on drift, and multi-table data
generated from the shape and proven to match.

## Audience

Data engineers, analytics engineers and platform teams who own dev/test environments or
pipelines, especially on Microsoft Fabric. Also useful for data governance people who get
asked "can we copy prod to dev?".

## Level

Intermediate (200–300). Python familiarity helps; no statistics background is needed.

## Key takeaways

1. **A profile is the most useful description of your data you're not keeping.** It
   captures types, null rates, distributions, patterns, value sets, keys and foreign keys,
   and it can be versioned, diffed and checked.
2. **Profiling at scale is an engineering problem.** Exact vs bounded statistics, canonical
   hashing, and one output schema whether the data is a file, a lakehouse table or a
   partitioned Spark table.
3. **Put profiling in the production pipeline.** Every run saves a timestamped shape,
   checks a contract, diffs against the last run, and gates the load.
4. **A raw profile is not safe to share.** It holds real top values by design. Only a safe
   profile, with suppression and minimum cohorts, should leave production.
5. **Dev can be rebuilt from the shape and proven.** Generate from the safe shape, profile
   the result, and compare. Know exactly which properties are preserved (`shape plan`) and
   which aren't.

## Assumptions (change any of them)

| # | Assumption | Where it matters |
|---|---|---|
| A1 | 45 minutes, including about 3 minutes of Q&A | `OUTLINE.md` |
| A2 | Data and analytics engineers, Fabric-heavy room | Depth of the Fabric sections |
| A3 | Live demos: profiling (local), the prod pipeline (Fabric), generation at scale (local), dev rebuild (local, **⟦PENDING R1–R5⟧**) | `DEMO.md` |
| A4 | Delivered only when `REQUIRE_READY=1 verify_snippets.sh` exits 0 (`READINESS.md`) | Everything marked ⟦PENDING⟧ |
| A5 | "Production" in the demos is a **stand-in**: synthetic retail data from the repo's stand-in generator. No real production data is used or shown | `DEMO.md`, slide 10 |
| A6 | Section 5 shows Shape's own measured numbers (wall-clock, rows per second, peak memory, start-up) and a live profile of a 1M-row file. No generation is demoed | Slides 13, 21–24 |
| A7 | *Stop Borrowing Contoso* is referenced as the speaker's previous talk | Slide 4 |
| A8 | No number from *Stop Borrowing Contoso* is reused. The talk uses only `NUMBERS.md`, which cites committed measurements | `NUMBERS.md` |

## Open questions for the owner

1. **Delivery date:** which date, and does the tracker put G4 plus P7-01/P7-02 before it?
2. **Submit which abstract?** The as-delivered one promises features that don't exist yet.
   The submission-safe variant doesn't.
3. **Venue and audience:** confirm Fabric-heavy. If not, `OUTLINE.md` has a cut line.
4. **The dev-rebuild demo** depends on five pending items (R1–R5). If one slips, cut that
   section to the "how it will work" slides, or move the talk?
5. **Real production data:** should the talk ever show a real customer's profile? The
   default is no: stand-in data only.
6. **Findings F1 and F5** (`STATUS.md`): fix before the talk?
