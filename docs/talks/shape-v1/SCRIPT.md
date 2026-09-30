# Speaker script: Ship the Shape, Not the Data

33 slides plus 5 backups, about 45 minutes. Each slide has: **On the slide**, **Say**
(speaker notes, meant to be paraphrased) and **Next**. Numbers cite `NUMBERS.md` (N-xx).
**⟦PENDING Rn⟧** marks anything that waits on an item in `READINESS.md`. Resolve every
marker before delivery.

## Verification

- Snippets **without** a marker were run on 2026-09-30 by [`verify_snippets.sh`](verify_snippets.sh)
  (part 1, asserted) against `main` @ `764cb37` (Python 3.11.15, pyarrow 25.0.1): exit 0.
  Data came from `demo/make_data.py` and the reference generator
  (`benchmarks/vs_spindle/domain_1to1/generate.py --impl reference_port`), using the pinned
  Spindle checkout.
- Snippets **with** a marker use the command forms written in the plan's work packages.
  They **don't run today**; the script's part 2 reports each one as PENDING. Replace them
  with the shipped syntax and real output before delivery (`READINESS.md`, last section).

## Wording rules

- "Production" in every demo is a **stand-in**: synthetic retail data. Say so on slide 10.
- Timings are of the **port**, on **4 cores**, **not Fabric**, after equivalence passed,
  until R7 gives product numbers. Say all four each time.
- A raw profile holds real values. Never call a raw `.shape` "safe" or "anonymised".
- Spindle is the retired project and the baseline. Don't call Shape its "successor", and
  don't say "GA", "production-ready" or "certified".
- Don't reuse numbers from *Stop Borrowing Contoso* (A8).

---

## Section 1 — Dev is lying to you (0:00–4:30)

### Slide 1 — Title (0:00, 20 s)

**On the slide:** "Ship the Shape, Not the Data". Subtitle: "Production-shaped dev
environments without production data". Jonathan Stewart · SQLLocks / SQLBites ·
`github.com/sqllocks/shape`.

**Say:** "Today is about the data in your dev environment, and how to make it behave like
production without it being production."

**Next:** "Quick intro."

### Slide 2 — About me (0:20, 20 s)

**On the slide:** the same content as *Stop Borrowing Contoso* slide 2, trimmed to three
lines.

**Say:** "25 years in data, lots of Fabric migrations, and I build Shape in the open."

**Next:** "Every one of those migrations had the same argument about dev."

### Slide 3 — The two bad options (0:40, 1 min 20 s)

**On the slide:** two columns.

| Copy production into dev | Fake data in dev |
|---|---|
| PII in a less-protected environment | Uniform values, no skew |
| Compliance sign-off, masking projects | No relationships, no seasonality |
| Size and cost | Tests pass; production fails |

**Say:** "Every team has this argument. Option one: copy prod. It behaves right, but now
customer data lives in an environment with weaker controls, and you're in a masking
project. Option two: fake data. It's safe, but it's flat. Tests pass because the data never
does the strange things production does. Both options lose."

**Next:** "I've talked about this before, from the demo side."

### Slide 4 — Level 4 (2:00, 1 min)

**On the slide:** the realism spectrum from *Stop Borrowing Contoso*: 1 Random · 2
Plausible · 3 Statistically realistic · **4 Production-mirrored, "can't have it (PII)"**.
Level 4 is circled, and "can't have it" is struck through and replaced with "its behaviour,
without its rows".

**Say:** "Some of you saw *Stop Borrowing Contoso*. The realism spectrum ended at level 4,
production-mirrored, and I told you that you can't have it because of PII. This talk takes
that back, with a condition: you can have level 4's behaviour, the distributions, keys,
patterns and rhythms, without level 4's rows."

**Next:** "Here's the whole idea on one slide."

### Slide 5 — Ship the shape, not the data (3:00, 1 min 30 s)

**On the slide:** a left-to-right diagram.

`prod tables` → **profile** → `raw .shape` → **safe profile** → `safe .shape` →
**generate** → `dev tables` → **profile** → **compare** with the prod shape (loops back).

Status chips under each arrow, filled in at delivery. On 2026-09-30: profile ✅ · safe
profile ⟦PENDING R1–R2⟧ · generate from shape ⟦PENDING R4⟧ · compare ✅ (`shape.diff`) /
fidelity ⟦PENDING R5⟧.

**Say:** "Profile production where it lives. Turn the profile into a safe one: no rare
values, no small groups. Only that file leaves production. Dev generates its data from the
file, then profiles what it generated and compares it with production's shape, so you can
prove the twin, not just claim it. Everything in this talk hangs off this diagram."

**Next:** "First, why a file?"

## Section 2 — Shape as Code (4:30–7:00)

### Slide 6 — You've never source-controlled the shape (4:30, 1 min 15 s)

**On the slide:** "You source-control your schema. You've never source-controlled the
**shape**." Beneath: `.shape` is **declared · versioned · diffable · executable**.

**Say:** "DDL, dbt models, migrations: all in git. But how the data behaves, which is where
'it broke in prod' usually lives, isn't anywhere. Shape as Code means that behaviour is a
file. You can commit it, diff it in a pull request, check new data against it, and, as
we'll see, generate from it."

**Next:** "Honest status first."

### Slide 7 — Status on delivery day (5:45, 1 min 15 s)

**On the slide:** a table filled in from `READINESS.md` and the tracker **on the day**.
Draft as of 2026-09-30:

| Shipped (0.9.0) | Built for this talk's date | Still to come |
|---|---|---|
| profile, save/load, check, diff, CLI, HTML report | safe profile ⟦R1–R2⟧ | stream profiling |
| multi-table FK detection | generate from shape, `plan` ⟦R3–R4⟧ | plugins |
| Fabric notebook, Environment, UDF, pipeline gate | fidelity report ⟦R5⟧ | Synapse and ADF |
| bitwise parity with Spindle's profiler (30 datasets) | product generation engine ⟦R6⟧ | |

**Say:** "Here's exactly what's shipped and what's new since the last release. Shape is
open source and built against a public plan, so you can check every line of this slide in
the repo."

**Next:** "Let's go deep on profiling, because everything else is built on it."

## Section 3 — Profiling, deep and at scale (7:00–19:00)

### Slide 8 — Anatomy of a profile (7:00, 1 min 30 s)

**On the slide:** three nested boxes.

- **Dataset:** tables, and relationships (foreign keys detected across tables).
- **Table:** row count, primary key, detected FKs, correlation matrix.
- **Column (34 fields in the parity check, N-02):** dtype · null count/rate · cardinality
  and ratio · unique · enum flag and values · min/max/mean/std · quantiles · distribution
  family and parameters · fit score · pattern · outlier rate · string lengths · top-500 value
  counts · hour/day-of-week/temporal histograms · PK/FK flags.

**Say:** "This is what one profile knows. At dataset level: which tables reference which.
At table level: the key and the correlations. Per column, a lot: the value distribution,
the fitted family, the text pattern, the top values, when things happen in time. Keep this
slide in mind, because generation later will use exactly these fields."

**Next:** "How does it decide those things?"

### Slide 9 — How the profiler decides (8:30, 1 min 45 s)

**On the slide:** four panels, each with its rule (N-65 to N-69):

- **Distribution:** sample 2,000 values (fixed seed); fit normal, uniform, exponential,
  lognormal; keep the best KS fit with p > 0.05, else none.
- **Pattern:** sample 1,000 strings; test 12 families (email, uuid, ssn, mac, ipv4, ipv6,
  iban, postal, date, phone, currency, language).
- **Enum:** cardinality < 200, or ratio < 0.30 with cardinality < 50,000.
- **Keys:** PK = no nulls, every value distinct, integer or UUID (id-like names win); FK
  across tables when a column named `<table>_id` matches that table's key.

**Say:** "None of this is magic. Distributions are fitted on a fixed-seed sample of 2,000,
four families are tested, and a family is only reported if it actually passes the test.
That's why a lot of real columns say 'none', and that's the honest answer. Patterns are
tested on a sample of a thousand. Enums have a cardinality rule. Foreign keys use a naming
rule, which is simple, predictable and easy to explain in a code review. And the seeds are
fixed, so the same data gives the same profile every time."

**Next:** "Let's run it on a whole schema."

### Slide 10 — Profile a whole schema → Live C1 (10:15, 1 min + demo ~2 min) — *verified*

**On the slide:**

```python
import shape
from pathlib import Path

prod = {f.stem: str(f) for f in Path("prod").glob("*.parquet")}
p = shape.profile(prod, name="retail")
shape.save(p, "retail_prod.shape")
for r in p.summary()["relationships"]:
    print(r["child"], r["child_columns"], "->", r["parent"])
# order_line ['order_id'] -> order
# order_line ['product_id'] -> product
# order ['customer_id'] -> customer
# return ['order_id'] -> order
# ... (8 relationships)
```

Footer: "`prod/` = nine retail tables, 1,965,400 rows (N-75). **A synthetic stand-in for
production.**"

**Say:** "Our 'production' today is a stand-in: nine retail tables, just under two million
rows, synthetic. I'll show you the generator later. One call profiles all nine, and it
finds the foreign keys between them by itself. One file holds the whole schema's shape."
→ **`DEMO.md` C1.**

Notes: `product.category_id` → `product_category` is **not** detected. That's the naming
rule (the column isn't called `product_category_id`), the same as Spindle's. If someone
spots it, say so; it's slide 9's rule in action. Don't quote the demo's run time, because
it isn't a benchmark.

**Next:** "Humans read the report."

### Slide 11 — Reading the report (13:15, 1 min) — *verified*

**On the slide:** a screenshot of `p.to_html()` for the retail profile. Callouts: a
fitted distribution, a detected `email` pattern, an enum with weights, the relationships
table.

**Say:** "Every profile renders as one self-contained HTML page, with no external assets.
It's what I attach to a ticket. Three things I look at first: null rates that surprise
me, columns with no fitted distribution, and relationships I didn't expect."

**Next:** "Two design choices make this work beyond one laptop."

### Slide 12 — Exact vs bounded, and canonical hashing (14:15, 1 min 30 s)

**On the slide:** left, the exact vs bounded table (N-60): `exact=True` is the default for
files and tables, and is what parity and every timing use. Bounded uses HLL (p=14), KLL
(k=200) and SpaceSaving (64); it's mergeable, for streams and partitioned or
larger-than-memory data. One output schema; `error_models` says which mode ran. Right,
hashing (N-62): seeded XXH3-64, never Python `hash()`; `1` ≡ `1.0`; NaN and null excluded;
timestamps normalised to µs.

**Say:** "Scale needs two things. First, a bounded mode: sketches whose memory doesn't
grow with the data and that merge, so ten partitions can be profiled separately and
combined. Exact stays the default, and it's what we test and time against. Second, hashing
that's the same on every machine and every run. Python's `hash()` changes between
processes, so we never use it. That's what lets profiles made in different places be
compared at all."

(Status for Q&A: hashing and sketches are built, with a Rust implementation and a Python
twin that must agree (P1-02, P1-03). The `exact=` switch arrives with the engine, P1-07.)

**Next:** "So how fast is it?"

### Slide 13 — Profiling at scale, in numbers (15:45, 1 min 30 s)

**On the slide:** header strip: "4 cores · Intel Xeon 2.10 GHz · Linux · Python 3.11 · not
Fabric · equivalence verified first".

| Profile (Parquet) | Spindle 3.0.1 | Port, 4 threads | Shape ⟦PENDING R7⟧ |
|---|---:|---:|---:|
| D2: 1M × 20 | 26.51 s | 1.82 s (14.6x) | TBD from `results.json` |
| D3: 5M × 10 | 47.91 s | 5.35 s (9.0x) | TBD |
| D4: 100k × 200 | 28.31 s | 4.23 s (6.7x) | TBD |

Footer: "**Target:** ≥10x per workload, ≥30x stretch (N-50)."

**Say:** "On a 4-core machine, after outputs were checked identical, profiling a million
rows by twenty columns took 26.5 seconds with the retired Spindle and 1.8 with the
vectorised port that Shape was built from." [At delivery, if R7 is READY: "and Shape itself
now does it in __ s, measured on __"; read only the committed row.] "Notice the wide table:
the port only reached 6.7x, below our own target. That's what the Rust engine is for."

(Sources: N-21, N-23, N-24. Until R7 is READY, **delete the Shape column**. Never show TBD
on stage.)

**Next:** "And in Fabric?"

### Slide 14 — Profiling at scale in Fabric (17:15, 1 min)

**On the slide:** two lanes.

- **Today:** Python notebook reads Delta with `deltalake`; PySpark notebook profiles on the
  driver up to 5,000,000 rows, then samples and reports `sampled: true` (runbook §5).
- **⟦PENDING R8⟧ Distributed:** each partition profiled in bounded mode through
  `mapInArrow`, merged on the driver (PF-02). Acceptance: the distributed profile equals the
  single-process bounded profile within the sketch bounds.

**Say:** "Today, in Fabric, a table goes to the driver, and above five million rows we
sample and say so in the result. We never silently sample. The next step is the bounded
mode from the previous slide: each Spark partition builds its own profile and the driver
merges them. That's why mergeable sketches matter."

(If R8 isn't READY on the day, keep only the "Today" lane and say the rest is next.)

**Next:** "A profile is only useful if every one of those fields is right. How do we know?"

### Slide 15 — Every field, bitwise (18:15, 45 s)

**On the slide:** the per-field matrix thumbnail from `DM-01_verify_output.txt`. **30/30
datasets · 34 fields · every value bitwise-identical** to Spindle 3.0.1 (pinned `422e78d`)
(N-01, N-02).

**Say:** "We profiled 30 datasets with the retired Spindle and with Shape: wide, tall,
multi-table, and 20 edge cases from three-row tables to every pattern family. We compared
all 34 fields. Every value was bitwise identical. So when I show you a profile, it's a
profile you can check, not a profile you have to trust."

**Next:** "Now put profiling where it belongs: in the production pipeline."

## Section 4 — The production pipeline (19:00–26:00)

### Slide 16 — The prod pipeline → Live C2 (19:00, 1 min + demo ~3 min) — *verified locally*

**On the slide:** the pipeline: read prod table → `shape.profile` → save
`Files/shape/<table>/<timestamp>/<table>.shape` (+ HTML, summary) → `check` contract →
`diff` vs last run → **gate** (If Condition on `passed`) → publish **safe** shape to the
dev-readable location **⟦PENDING R1–R2⟧**. The core of the notebook:

```python
import shape

p = shape.profile("/lakehouse/default/Tables/orders_day1", name="orders_day1")
r = shape.check(p, "/lakehouse/default/Files/contracts/orders.json")
```

**Say:** "Every scheduled run profiles the production table, saves a timestamped shape
next to it, checks the contract, diffs against the previous run, and gates the load. After
a month you have a history of how your data behaved every single day, and it's just files."
→ **`DEMO.md` C2.**

Notes: the snippet was run locally against a Delta table written with `deltalake`. The
Fabric run itself is the owner's dry run (R11). The notebook is
`integrations/fabric/notebooks/shape_profile.ipynb`; `notebookutils.notebook.exit(...)` is
its last top-level statement.

**Next:** "Day 2."

### Slide 17 — Day 2 fails, with reasons (22:00, 1 min) — *verified*

**On the slide:**

```python
today = shape.profile("orders_day2.parquet", name="orders")
r = shape.check(today, "contracts/orders.json")
print(r.passed)
for v in r.violations:
    print(v["column"], v["rule"], v["observed"])
# False
# status allowed_values {'unexpected_values': ['lost']}
# order_total max 7189.882
```

**Say:** "A new status value and an out-of-range amount (N-71, N-72). The gate fails and
says why: column, rule, expected, observed. That's the message the pipeline shows."

**Next:** "The contract only catches what you wrote down. The diff catches the rest."

### Slide 18 — Diff against the last run (23:00, 1 min 15 s) — *verified*

**On the slide:**

```python
d = shape.diff(shape.load("orders_day1.shape"), today,
               thresholds={"mean_shift_std": 0.25})
for c in d.changes:
    print(c["column"], c["kind"], c["severity"])
# status new_categorical_values low
# order_total mean_shift medium
# order_total new_categorical_values low
```

Callout: "Default `mean_shift_std` = 0.5; this +40% shift is 0.43 σ. **The default misses
it.**" (N-74)

**Say:** "Diff yesterday's shape against today's. Here's the honest part: amounts went up
40%, but these amounts are so spread out that it's only 0.43 standard deviations, and the
default threshold is half a standard deviation. So I tune it to 0.25 for this column. The
contract caught it anyway. Diff also works across whole schemas: the columns come back as
`table.column`."

**Next:** "Any orchestrator can use this."

### Slide 19 — Exit codes (24:15, 45 s) — *verified*

**On the slide:**

```bash
shape check day2.shape contracts/orders.json          # exit 1
shape diff day1.shape day2.shape --fail-on-drift      # exit 1
```

`0` pass · `1` failed check, or drift · `2` usage or input error.

**Say:** "Fabric, ADF, Airflow, GitHub Actions: anything that reads an exit code can gate
on a shape."

(Don't show exit 2 with a missing `.shape` until R10 is READY. It exits 1 today, which is
finding F1.)

**Next:** "Now the part everyone forgets. Is that `.shape` file safe to hand to dev?"

### Slide 20 — Raw vs safe (25:00, 1 min) — *raw claim verified; safe ⟦PENDING R1–R2⟧*

**On the slide:** two files.

| `retail_prod.shape` (raw) | `retail_safe.shape` ⟦PENDING R1–R2⟧ |
|---|---|
| top-500 value counts per column **with real values** | small cells suppressed; minimum cohort enforced |
| real min/max strings (e.g. an email) | winsorised bounds |
| stays **inside** production | the only file that leaves production |

```bash
shape profile validate --safe retail_prod.shape      # ⟦PENDING R1⟧ fails: raw values
<safe export, command set by P7-01> retail_prod.shape -o retail_safe.shape   # ⟦PENDING R2⟧
shape profile validate --safe retail_safe.shape      # ⟦PENDING R1⟧ passes
```

**Say:** "A raw profile is not anonymous. It keeps the top 500 values of every column. That's
what makes it match Spindle's profiler exactly, and it means real email addresses are in
that file. I checked our stand-in: they're there. So the raw shape stays in production. What
leaves is the safe profile: rare values suppressed, a minimum group size enforced, bounds
clipped, and a validator that fails if anything slips through."

(Verified today: `verify_snippets.sh` asserts that the raw `retail_prod.shape` contains real
customer emails. Replace the safe-export line with the shipped command at P7-01.)

**Next:** "Before we generate from a shape, let's look at generation itself."

## Section 5 — Generation at scale (26:00–31:00)

### Slide 21 — Generate a schema, not columns (26:00, 45 s)

**On the slide:** the dependency order: store → customer → product → order → order_line
(parents before children). "Every foreign key points at a real primary key."

**Say:** "Quick callback to the last talk. You generate a schema in dependency order, never
column by column, so every order points at a real customer."

**Next:** "At scale."

### Slide 22 — Two million rows → Live C3 (26:45, 45 s + demo ~2 min) — *verified*

**On the slide:**

```bash
python benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42
# wrote .../reference_port/retail/medium/seed42 (1,965,400 rows, …s)
```

Label: "**Reference generator**: equivalence-verified against Spindle (T-21, 60/60
columns); not in the pip package." **⟦PENDING R6⟧**: at delivery, replace it with the product
engine (`shape generate retail --scale medium --seed 42 --format parquet -o prod/`, per
P4-10) if R6 is READY, and drop the label.

**Say:** "Nine tables, just under two million rows, with a fixed seed so rehearsal equals
live. This is the generator that made our 'production'. Then, the loop: I profile what I
just generated with the same call as slide 10." → **`DEMO.md` C3.**

Notes: the screen shows a live time. Say "that's my laptop, not a benchmark", and quote only
slide 23.

**Next:** "The measured numbers."

### Slide 23 — Generation at scale, in numbers (28:30, 1 min 15 s)

**On the slide:** "4 cores · not Fabric · equivalence verified first (60/60 columns, T-21)".

| Retail | Rows | Spindle 3.0.1 | Port | Shape ⟦PENDING R7⟧ |
|---|---:|---:|---:|---:|
| medium | 1,965,400 | 5.29 s | 1.26 s (4.2x) | TBD |
| large | 19,625,400 | 102.64 s | 14.89 s (6.9x) | TBD |

Footer: "Target ≥10x (N-50). Vectorising sped up the generate step about 8x, but Parquet
writing didn't get faster (N-42)."

**Say:** "Nineteen and a half million rows in under 15 seconds, against 103 for Spindle, on
four cores, after the output passed the equivalence test. That's still under our 10x target,
because writing Parquet became the bottleneck. So the engine pipelines writing with
generation."

(Delete the Shape column unless R7 is READY.)

**Next:** "How do you test random data?"

### Slide 24 — Testing random data (29:45, 1 min 15 s)

**On the slide:** "Is Shape's output as close to Spindle's as Spindle is to itself?" KS,
TVD, null rates, vocabulary, FK integrity, business rules; tolerance scaled by Spindle's
seed-to-seed spread (0.14–0.37 KS on one column, N-05); baseline seeds fixed at 43–46 (N-06).

**Say:** "You can't compare random data bitwise. So the test asks: is our output as close to
the reference as the reference is to itself with a different seed? And the seeds are fixed
in the rules, so nobody can shop for a lucky one."

**Next:** "Now combine the two halves: generate from a profile."

## Section 6 — Rebuild dev from the shape (31:00–38:30) ⟦PENDING R1–R5, R9⟧

> Owner: this whole section waits on READINESS items. If any of R1–R5 isn't READY, use the
> cut line in `OUTLINE.md`: keep 25 and 26 as "how it will work", with no demo.

### Slide 25 — The dev rebuild (31:00, 1 min)

**On the slide:** slide 5's diagram, with the right half highlighted: `retail_safe.shape` →
`shape plan` → `shape generate --from` → `dev/` → `shape.profile` → compare.

**Say:** "Dev gets one file: the safe shape. It plans, generates, profiles what it made, and
compares that with production's shape. No production rows cross the boundary."

**Next:** "Step one: find out what you'll get."

### Slide 26 — `shape plan`: what's preserved and what isn't (32:00, 1 min 15 s) ⟦PENDING R3⟧

**On the slide:**

```bash
shape plan retail_safe.shape       # ⟦PENDING R3⟧ paste real output at delivery
```

Expected content, per P4-08: preserved: marginals, correlations (Gaussian copula),
missingness, seasonality, FK structure. **Not modelled**: every field listed explicitly.

**Say:** "Before generating anything, Shape tells you what the output will and won't
preserve. If a property isn't modelled, it's on this list. That's the difference between
'looks real' and knowing what real means for your tests."

**Next:** "Generate."

### Slide 27 — Generate dev from the shape → Live D2 (33:15, 45 s + demo ~1 min 30 s) ⟦PENDING R4⟧

**On the slide:**

```bash
shape generate --from retail_safe.shape --seed 42 --format parquet -o dev/   # ⟦PENDING R4⟧
```

(Flags from P4-08/P4-10's text; confirm the shipped syntax.)

**Say:** "Same nine tables, same keys, generated from a file of kilobytes. The seed makes it
reproducible: every developer gets the same dev database." [Only say "kilobytes" if the
shipped safe shape's size is measured and recorded in `NUMBERS.md`.]

**Next:** "Now prove it."

### Slide 28 — Prove the twin → Live D3 (35:30, 1 min 30 s) ⟦PENDING R4, R5⟧

**On the slide:**

```python
dev = shape.profile({f.stem: str(f) for f in Path("dev").glob("*.parquet")}, name="retail")
d = shape.diff(shape.load("retail_safe.shape"), dev)     # multi-table diff works today
print(d.drifted, len(d.changes))
```

```bash
shape fidelity ...     # ⟦PENDING R5⟧ per-table scores; exits non-zero below threshold
```

Callout: "P4-08's acceptance test: profile → generate → profile is within the T-22
tolerances for every modelled field."

**Say:** "Profile dev with the same call we used on production, then compare the two
shapes. Diff reports anything that moved. The fidelity report scores every table and fails
the build if it's under threshold. And that's also how the feature is tested before it
ships: generate from a profile, profile the result, and it has to land within the same
tolerances we use for profiler parity."

(At delivery, paste the real output. If `d.drifted` is True for unmodelled fields,
show that honestly and point back to slide 26.)

**Next:** "Put it on a schedule."

### Slide 29 — The dev-refresh pipeline (37:00, 1 min 30 s) ⟦PENDING R1–R2, R9⟧

**On the slide:** two pipelines, separated by a boundary line.

- **Prod workspace** (slide 16): profile → check → diff → gate → publish `retail_safe.shape`.
- **Dev workspace** (PF-06): read `retail_safe.shape` → generate → profile → check the same
  contract → dev lakehouse.

Only the safe shape crosses the line.

**Say:** "Two pipelines. Production publishes one safe file per run. Dev regenerates from it
on a schedule and checks the result against the same contract production uses. When
production's shape changes, dev follows the next morning, and nothing sensitive ever moved."

**Next:** "Everything I've shown rests on one rule."

## Section 7 — How we know it's right (38:30–40:30)

### Slide 30 — Equivalence before timing, and the bug it caught (38:30, 2 min)

**On the slide:** top: "No number counts until that workload's equivalence check passes on
the timed output." Bottom: the stale-cache story in four steps (N-07): cached Spindle output
keyed by dataset name → D1 regenerated every run → 41 mismatches per D1 file → key the cache
by SHA-256 of the input. "A stale cache can fail you, and it can just as easily pass you."

**Say:** "Every number today came with an equivalence check on the same output. And the
harness checks itself too. Once, a cached reference result went stale and 41 fields
'mismatched'. The profiler was fine; the check was wrong. We fixed the cache key. The scary
version is the one where the stale answer happens to agree, and you get a pass you didn't
earn."

**Next:** "Where this goes."

## Section 8 — Status and call to action (40:30–42:30)

### Slide 31 — What's next (40:30, 1 min)

**On the slide:** chips: stream profiling (Kafka, Event Hubs) · plugins (first-party
features use the same API) · Synapse and ADF pipelines · streaming generation. The status of
each comes from the tracker on the day.

**Say:** "Next come streams, plugins, and the other Azure orchestrators. As always, the plan
and its status are public."

### Slide 32 — Three things for Monday (41:30, 1 min)

**On the slide:**

1. **Profile one production table** in its own pipeline, and save the shape every run.
2. **Write a three-rule contract** and gate on it.
3. **Rebuild one dev table from the safe shape**, then profile it and compare.
   ⟦PENDING R1–R4: if not READY, replace this with "Diff this week's shape against last
   week's."⟧

`pip install sqllocks-shape` · `github.com/sqllocks/shape` ⟦check PyPI (finding F2)⟧

**Say:** "Three things. Profile one table you care about, and keep its shape. Gate on three
rules. Then rebuild one dev table from the safe shape and prove it matches."

## Q&A (42:30–45:00)

### Slide 33 — Questions

**Likely questions:**

- *Is the safe shape really safe?* "It suppresses small cells, enforces a minimum cohort and
  clips bounds, and a validator enforces that. It's a privacy control, not a legal
  guarantee. Your governance team should review it, and the rules are in the repo." (Answer
  only once R1–R2 are READY, using the shipped behaviour.)
- *Differential privacy?* "Spindle had an experimental version; Shape is porting it as
  experimental, with OS randomness by default (D-07). It isn't the default protection."
- *Correlations between columns?* "The generator models them with a Gaussian copula; `plan`
  tells you which ones." (Only if R3–R4 are READY.)
- *How fast in Fabric?* Only `LIVE_TIMINGS.md` rows, with SKU and vCores.
- *Great Expectations, Tonic, Gretel?* "Different starting points. Shape's is a
  committable, diffable profile. Try them on the same table." No comparative claims.
- *Streaming?* "Stream profiling is next on the roadmap; the bounded mode on slide 12 is
  built for it."

---

## Backup slides

- **B1** Full profiling table: `demo/BENCHMARKS.md` profiling section (N-20 to N-25).
- **B2** `results.json` harness run (N-30 to N-35), with `shape: null` or its values once R7 is READY.
- **B3** Drift side effects from `DRIFT.md`.
- **B4** Fabric fallbacks (`DEMO.md` part E).
- **B5** One column's profile JSON: `p.to_dict()["tables"]["customer"]["columns"]["loyalty_tier"]`,
  taken from a real run (verify at build time).
