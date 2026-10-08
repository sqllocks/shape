# Deck: Ship the Shape, Not the Data (October 3, 2026)

**Deck:** https://claude.ai/artifact/BizWK3bVD8chFsQBUK4oBd

It's a Slides artifact, private until the owner shares it from the page's Share menu. It
can be presented from the page and exported to PDF or PPTX. Built 2026-09-30 from
`SCRIPT.md` at `77127b5`; **revised in `DECK.md`/`SCRIPT.md` for the no-comparison directive, so the published deck must be republished (see "Slides changed")**. Every slide has its `SCRIPT.md` **Say**, notes and **Next** as
speaker notes. The notes open with the slide's section, clock range and time budget from
`OUTLINE.md`, plus the clock checkpoints and cut lines at slides 8, 16, 21, 25 and 29.

## Slide map

Deck slide *n* is `SCRIPT.md` slide *n*. Slide ids in the artifact are `s01`…`s31` and
`b1`…`b5`.

| Deck | SCRIPT.md | Section (OUTLINE.md) |
|---|---|---|
| 1–5 | Slides 1–5 | 1 · Dev is lying to you (0:00–4:00) |
| 6–7 | Slides 6–7 | 2 · Shape as Code, and honest status (4:00–6:30) |
| 8–15 | Slides 8–15 (C1 on 10–11) | 3 · Profiling, deep and at scale (6:30–21:30) |
| 16–20 | Slides 16–20 (C2 / C2-local on 16) | 4 · The production pipeline (21:30–31:30) |
| 21–24 | Slides 21–24 (C3 on 22) | 5 · Shape's own numbers (31:30–36:00) |
| 25–27 | Slides 25–27 | 6 · How it will work, PLANNED (36:00–39:30) |
| 28 | Slide 28 | 7 · Measure before you claim (39:30–41:30) |
| 29–30 | Slides 29–30 | 8 · Status and call to action (41:30–43:00) |
| 31 | Slide 31 (likely questions in the notes) | Q&A (43:00–45:00) |
| B1–B5 | Backup slides B1–B5 | Backups |

## Design decisions

- **Look:** IBM Plex Sans with JetBrains Mono for code, on warm off-white. The title (1),
  the statement (6) and Questions (31) are dark slides; the backups use a second, darker
  paper tone. No images, logos or stock art. Every diagram is built from vector shapes.
- **Status colors, always labelled in words:** solid blue = RUNS TODAY; dashed orange =
  PLANNED; dark with amber text = BEING BUILT; grey outline = "Measured, exact mode". Section 6 slides (25–27) carry large PLANNED / BEING BUILT chips at the top,
  and each box also has its own chip and work-package ID.
- **Code:** each runnable snippet (slides 10, 16, 17, 18, 19, 22) is character-for-character
  what `SCRIPT.md` shows. This was checked by extracting the slide text and comparing it
  with the `SCRIPT.md` code blocks. The slide format has no `<pre>`, so indentation and
  aligned comments are kept with non-breaking spaces. Copy code from the repo, not from
  the slide. Output lines (`# …`) are a lighter colour.
- **Numbers:** every number on a slide appears in `SCRIPT.md`, `NUMBERS.md`, `DEMO.md` or
  `demo/DRIFT.md` (checked by script). The header strips on 13, 21, 23 and B1 read
  "`shape.profile`, exact mode · 4 cores · … · not Fabric · median of 3 runs". Bounded mode
  and the Rust engine carry a BEING BUILT chip and **no** number. No slide compares Shape
  with any other tool, version or earlier result. N-IDs are left off the slides except where
  `SCRIPT.md` puts them inside the slide text. The speaker notes cite the rest.
- **Slide 5:** `SCRIPT.md` asks for a left-to-right diagram of 9 steps, which doesn't fit
  one row at a readable size. The deck draws two columns instead: left (RUNS TODAY) and
  right (PLANNED), each read top to bottom, with a vertical line between them. Read it left
  half, then right half.
- **Slide 12:** the exact/bounded "table" is two stacked cards, so each row can carry its
  status chip (RUNS TODAY on exact, BEING BUILT on bounded). Tables can't hold chips.
- **Slide 16:** the pipeline is six boxes in a row. The full save path
  `Files/shape/<table>/<timestamp>/<table>.shape` is on its own line below them, because it's
  too long for a box.
- **Slide 30, install line:** shows `pip install sqllocks-shape`. When the deck was built
  (2026-09-30, after the checks in `STATUS.md`), `pip index versions sqllocks-shape`
  reported `0.9.0` on pypi.org. The gate's part 3 also reported `F2 on PyPI`. The owner's
  October 2 check still decides. If 0.9.0 isn't on pypi.org then, change the line to
  `git clone github.com/sqllocks/shape && pip install ./shape` (the slide's notes say so).
  Note that finding F2 in `STATUS.md` still says "not on PyPI". I didn't edit it.

## Content that isn't on the slides, or was changed to fit

| Slide | What | Why / what to do |
|---|---|---|
| 2 | *Stop Borrowing Contoso* slide 2 content | That slide isn't in the repo. The three lines come from `SCRIPT.md`'s Say text ("25 years in data", "Lots of Fabric migrations", "I build Shape in the open") under the name line. Swap in the old slide's lines if you prefer. |
| 11 | The live `retail_prod.html` screenshot | Not embedded: it's shown live in the browser (C1). The slide carries a "LIVE IN THE BROWSER" chip and the four callouts to point at, in order. |
| 15 | Was a per-field matrix thumbnail | Slide 15 is now three facts: deterministic output, tests and coverage at Gate G0, the CI matrix. No thumbnail. |
| B3 | "50 extra rows" (DRIFT.md) | Said as "the duplicated-SKU rows", with 5,050 rows / 5,000 distinct (N-73) in the table, so every number comes from `NUMBERS.md`. |
| B5 | `segment` profile JSON | The slide has the call and a marked paste area. `SCRIPT.md` says to take the JSON from the rehearsal run, so paste it after the October 2 rehearsal, or hide the slide. |

## Slides changed in this revision (republish these)

Slide numbers are `SCRIPT.md` / deck numbers. **Republish the deck with these slides and
their speaker notes replaced:**

| Slide | Change |
|---|---|
| 7 | Two table cells (see `SCRIPT.md` slide 7) || 10 | **Code block, footer and notes**: the call now names four tables explicitly; output is 3 relationships; footer "four retail tables, 640,000 rows, 3 foreign keys found; synthetic data generated by Shape" (N-75, N-76) |
| 11 | Callout `loyalty_tier` enum → `segment` enum, in the slide text and notes |

| 12 | Slide text and notes: "what every timing in this talk uses" |
| 13 | **Rewritten**: wall-clock, rows per second, peak memory; header strip; footer; notes |
| 15 | **Rewritten**: how the profile is checked || 18 | Callout and notes: "0.43 σ" → "0.39 σ" (N-74) |
| 20 | **Bullet and notes**: "every first name, last name, city and state, and 2 of the 47,515 email addresses (min and max)", synthetic; in production they'd be your customers' (N-77) |

| 21 | **Rewritten**: memory per dataset; bounded mode "being built, not measured" |
| 22 | **Rewritten**: live profile of the 1M × 20 file with a clock; new code block |
| 23 | **Rewritten**: start-up times |
| 24 | **Rewritten**: how the numbers were measured, and what isn't |
| 25 | Slide text and notes: safe-profile box |
| 26 | Slide text and notes: fidelity-report box |
| 27 | Footer and notes: "no speed or memory claims" |
| 28 | **Rewritten**: "Measure before you claim" |
| 31 | Speaker notes: two Q&A answers |
| B1 | **Rewritten**: full `shape.profile` table, both file formats |
| B2 | **Rewritten**: raw runs and start-up || B5 | Column name in the call: `loyalty_tier` → `segment` |


Slides not listed above are unchanged. Also check the published deck's title, every
slide's speaker notes and its slide list; this repo change can't reach the artifact.

## Checks for this revision (2026-09-30)

See `STATUS.md`: the grep is empty, the talk-kit tests pass, and the new blocks of
`verify_snippets.sh` pass. The full delivery gate needs the stage laptop's prep tooling
and wasn't re-run here. 