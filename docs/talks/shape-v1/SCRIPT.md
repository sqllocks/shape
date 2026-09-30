# Speaker script: Ship the Shape, Not the Data (October 3, 2026)

31 slides plus 5 backups, about 45 minutes. Each slide has: **On the slide**, **Say**
(speaker notes, meant to be paraphrased) and **Next**. Numbers cite `NUMBERS.md` (N-xx).

## Verification

- Every runnable snippet on these slides was run on 2026-09-30 by
  [`verify_snippets.sh`](verify_snippets.sh) (part 1, asserted) against `main` @ `b2dd663`
  (Shape 0.9.0, Python 3.11.15, pyarrow 25.0.1): exit 0, and `REQUIRE_READY=1` exit 0.
  Data came from `demo/make_data.py` and the reference benchmark generator
  (`benchmarks/vs_spindle/domain_1to1/generate.py --impl reference_port`), using the pinned
  Spindle checkout.
- Section 6 ("how it will work") has **no runnable snippets**. It describes the plan's
  design. The only command forms there are labelled "planned syntax (may change)" and come
  from the plan's work-package text. Nothing in that section is claimed to run.

## Wording rules

- "Production" in every demo is a **stand-in**: synthetic retail data. Say so on slide 10.
- Every timing is of the **port** (the vectorised reference code Shape's profiler was
  ported from), on **4 cores**, **not Fabric**, after equivalence passed. Say all four each
  time. No reference-port timing is ever presented as the product's speed.
- The product itself has **no** timings against Spindle yet (`results.json` → `"shape":
  null`). The Rust engine is **being built**: describe it, never give it a speed.
- Targets (≥10x minimum, ≥30x stretch) are said only as **targets**.
- A raw `.shape` holds real values. Never call it "safe" or "anonymised".
- Section 6 is **planned**. Never say "shipped", "available", "you can" for anything there.
  Say "the plan is", "it will", "is being built".
- Spindle is the retired project and the benchmark baseline. Don't call Shape its
  "successor", and don't say "GA", "production-ready" or "certified". Shape is **early
  access**.
- Don't reuse numbers from *Stop Borrowing Contoso* (`STATUS.md`, F7).
- Fabric timings: only rows from `demo/LIVE_TIMINGS.md`, and only after the owner's dry run
  (R11). Until then, none.

---

## Section 1 — Dev is lying to you (0:00–4:00)

### Slide 1 — Title (0:00, 20 s)

**On the slide:** "Ship the Shape, Not the Data". Subtitle: "Profile production. Keep the
shape. Rebuild dev from it." Jonathan Stewart · SQLLocks / SQLBites ·
`github.com/sqllocks/shape`.

**Say:** "Today is about the data in your dev environment: how to know what production's
data looks like, keep that as a file, and, where we're going, rebuild dev from it."

**Next:** "Quick intro."

### Slide 2 — About me (0:20, 20 s)

**On the slide:** the same content as *Stop Borrowing Contoso* slide 2, trimmed to three
lines.

**Say:** "25 years in data, lots of Fabric migrations, and I build Shape in the open."

**Next:** "Every one of those migrations had the same argument about dev."

### Slide 3 — The two bad options (0:40, 1 min 10 s)

**On the slide:** two columns.

| Copy production into dev | Fake data in dev |
|---|---|
| PII in a less-protected environment | Uniform values, no skew |
| Compliance sign-off, masking projects | No relationships, no seasonality |
| Size and cost | Tests pass; production fails |

**Say:** "Every team has this argument. Option one: copy prod. It behaves right, but now
customer data lives in an environment with weaker controls. Option two: fake data. It's
safe, but it's flat. Tests pass because the data never does the strange things production
does. Both options lose."

**Next:** "I've talked about this before, from the demo side."

### Slide 4 — Level 4 (1:50, 1 min)

**On the slide:** the realism spectrum from *Stop Borrowing Contoso*: 1 Random · 2
Plausible · 3 Statistically realistic · **4 Production-mirrored, "can't have it (PII)"**.
Level 4 is circled. Caption: "Step 1: know production's shape (today). Step 2: rebuild dev
from it (planned)."

**Say:** "Some of you saw *Stop Borrowing Contoso*. The spectrum ended at level 4,
production-mirrored, and I said you can't have it because of PII. Getting there has two
steps. Step one is knowing exactly how production's data behaves, and keeping that as a
file. That runs today, and it's most of this talk. Step two is rebuilding dev from that
file. That's designed and planned, not shipped, and I'll show you the design near the end."

**Next:** "Here's the whole idea on one slide."

### Slide 5 — Ship the shape, not the data (2:50, 1 min 10 s)

**On the slide:** a left-to-right diagram.

`prod tables` → **profile** → `.shape` → **check / diff / gate** ‖ **safe profile** →
`safe .shape` → **generate** → `dev tables` → **compare** with the prod shape.

Chips: profile · save · check · diff · gate: **RUNS TODAY**. Safe profile · generate from
shape · fidelity report: **PLANNED**. A vertical line separates the two halves.

**Say:** "Left of the line runs today: profile production where it lives, save the shape,
check it, diff it, gate on it. Right of the line is planned: a safe version of the shape,
generating dev from it, and proving the result matches. I'll spend most of our time on the
left, because you can use it on Monday."

**Next:** "First, why a file?"

## Section 2 — Shape as Code, and honest status (4:00–6:30)

### Slide 6 — You've never source-controlled the shape (4:00, 1 min 15 s)

**On the slide:** "You source-control your schema. You've never source-controlled the
**shape**." Beneath: `.shape` is **declared · versioned · diffable · checkable**.

**Say:** "DDL, dbt models, migrations: all in git. But how the data behaves, which is where
'it broke in prod' usually lives, isn't anywhere. Shape as Code means that behaviour is a
file. You can commit it, diff it, and check new data against it."

**Next:** "Honest status first."

### Slide 7 — Status on October 3 (5:15, 1 min 15 s)

**On the slide:** three columns.

| Runs today (0.9.0, early access) | Being built (engine branch, not released) | Planned (work packages) |
|---|---|---|
| profile a table or a whole schema, FK detection across tables | Rust kernel: build and FFI, wheels, hashing, sketches, readers, type inference, fused profile kernel, profile engine (P1-01a..P1-07) | Spindle parity on the engine (P1-08), then product timings (G1) |
| `.shape` save/load, HTML report | | safe profile, k-anonymity (P7-01, P7-02) |
| `check` (contracts), `diff` (single and multi-table), CLI exit codes | | generate from a shape, `shape plan` (P4-08) |
| Fabric: notebooks, Environment, UDF, pipeline gate | | fidelity report (P4-09); product generation engine (P4-07) |
| profiler bitwise-identical to Spindle's on 30 datasets | | distributed profiling in Spark (PF-02); generation pipelines (PF-06) |

**Say:** "Here's exactly what runs today, what's being built right now, and what's planned.
Shape is open source and built against a public plan with a status tracker, so you can
check every line of this slide in the repo. Early access means early access."

(Engine status is from the tracker on `build/main-plan` @ `fefe7a3`, 2026-09-30: P1-01a to
P1-07 done there (lead-verified 2026-09-30 afternoon), P1-08 onward todo. That branch is not merged to `main` and not released.
Re-check the tracker the day before and update this column only from it.)

**Next:** "Let's go deep on profiling, because everything else is built on it."

## Section 3 — Profiling, deep and at scale (6:30–21:30)

### Slide 8 — Anatomy of a profile (6:30, 2 min)

**On the slide:** three nested boxes.

- **Dataset:** tables, and relationships (foreign keys detected across tables).
- **Table:** row count, primary key, detected FKs, correlation matrix.
- **Column (27 of the 31 fields in the parity check, N-02):** dtype · null count/rate · cardinality
  and ratio · unique · enum flag and values · min/max/mean/std · quantiles · distribution
  family and parameters · fit score · pattern · outlier rate · string lengths · top-500 value
  counts · hour/day-of-week/temporal histograms · PK/FK flags.

**Say:** "This is what one profile knows. At dataset level: which tables reference which.
At table level: the key and the correlations. Per column, a lot: the value distribution,
the fitted family, the text pattern, the top values, when things happen in time. It's
everything you'd want to know about production's data before you let anything touch it,
and it's also what generation will need later."

**Next:** "How does it decide those things?"

### Slide 9 — How the profiler decides (8:30, 2 min)

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

### Slide 10 — Profile a whole schema → Live C1 (10:30, 1 min + demo ~2 min) — *verified*

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
rows, synthetic. I'll show you where it came from later. One call profiles all nine, and it
finds the foreign keys between them by itself. One file holds the whole schema's shape."
→ **`DEMO.md` C1.**

Notes: `product.category_id` → `product_category` is **not** detected. That's the naming
rule (the column isn't called `product_category_id`), the same as Spindle's. If someone
spots it, say so; it's slide 9's rule in action. Don't quote the demo's run time, because
it isn't a benchmark.

**Next:** "Humans read the report."

### Slide 11 — Reading the report (13:30, 2 min) — *verified*

**On the slide:** the live `retail_prod.html` in the browser (`p.to_html()`). Callouts: a
fitted distribution, the `email` pattern on `customer.email`, the `loyalty_tier` enum with
weights, the relationships table.

**Say:** "Every profile renders as one self-contained HTML page, with no external assets.
It's what I attach to a ticket. Three things I look at first: null rates that surprise me,
columns with no fitted distribution, and relationships I didn't expect." Scroll to
`customer.email`: "Notice what's in here: real top values. Hold that thought until slide
20."

**Next:** "Two design choices make profiling work beyond one laptop."

### Slide 12 — Exact vs bounded, and canonical hashing (15:30, 1 min 30 s)

**On the slide:** left, the exact vs bounded table (N-60): `exact` is what 0.9.0 does
today, and what parity and every timing use. **Bounded** (being built) uses HLL (p=14), KLL
(k=200) and SpaceSaving (64); it's mergeable, for streams and partitioned or
larger-than-memory data. One output schema. Right, hashing (N-62): seeded XXH3-64, never
Python `hash()`; `1` ≡ `1.0`; NaN and null excluded; timestamps normalised to µs.
Chip on the right half and on "bounded": **BEING BUILT (engine branch)**.

**Say:** "Scale needs two things. First, a bounded mode: sketches whose memory doesn't
grow with the data and that merge, so ten partitions can be profiled separately and
combined. Second, hashing that's the same on every machine and every run. Python's `hash()`
changes between processes, so we never use it. Both are being built in the Rust engine
right now, each with a Python twin that must agree with it. They're not in the release
yet. Exact profiling, which you'll see live, is."

(Status for Q&A: hashing (P1-02) and sketches (P1-03) are done on `build/main-plan`, not
merged or released. The `exact=` switch arrives with the profile engine, P1-07, done on
`build/main-plan` but not merged or released.)

**Next:** "So how fast is profiling?"

### Slide 13 — Profiling at scale, in numbers (17:00, 1 min 30 s)

**On the slide:** header strip: "**Reference port**, not the product · 4 cores · Intel Xeon
2.10 GHz · Linux · Python 3.11 · not Fabric · equivalence verified first".

| Profile (Parquet) | Spindle 3.0.1 | Port, 4 threads | vs Spindle |
|---|---:|---:|---:|
| D2: 1M × 20 | 26.51 s | 1.82 s | 14.6x |
| D3: 5M × 10 | 47.91 s | 5.35 s | 9.0x |
| D4: 100k × 200 | 28.31 s | 4.23 s | 6.7x |

Footer: "**Target** for the product: ≥10x per workload, ≥30x stretch (N-50). Not yet
measured for the product."

**Say:** "These are timings of the port, the vectorised reference code that Shape's
profiler was built from, not of the library. On a 4-core machine, after outputs were
checked identical, a million rows by twenty columns took 26.5 seconds with the retired
Spindle and 1.8 with the port. Notice the wide table: 6.7x, below our own 10x target. The
product itself isn't in the benchmark yet, so I have no product number for you, and I
won't make one up. Closing that gap is what the Rust engine is for."

(Sources: N-21, N-23, N-24, N-50.)

**Next:** "And in Fabric?"

### Slide 14 — Profiling in Fabric today (18:30, 1 min 30 s)

**On the slide:**

- **Python notebook** (`shape_profile`): reads the Delta table with `deltalake`;
  `%%configure {"vCores": 8}` (default 2 vCores / 16 GB, N-80).
- **PySpark notebook** (`shape_profile_spark`): profiles on the driver up to 5,000,000 rows,
  then samples and reports `sampled: true` (runbook §5).
- **UDF** (`shape_udf`): files up to 50 MB by default; table reads capped at 1,000,000 rows
  (N-83); 240 s execution limit (N-81).
- One line at the bottom: "Distributed profiling across Spark partitions: **planned**
  (slide 27)."

**Say:** "Today, in Fabric, a table goes to the driver, and above five million rows we
sample and say so in the result. We never silently sample. Each surface has limits, and
they're on the slide because you'll hit them. Profiling across partitions is planned; I'll
show the design at the end."

(If R11 is done, you may add one `LIVE_TIMINGS.md` row here, with SKU and vCores, read
exactly. If not, no Fabric timing at all.)

**Next:** "A profile is only useful if every one of those fields is right. How do we know?"

### Slide 15 — Every field, bitwise (20:00, 1 min 30 s)

**On the slide:** the per-field matrix thumbnail from `DM-01_verify_output.txt`. **30/30
datasets · 31 fields · every value bitwise-identical** to Spindle 3.0.1 (pinned `422e78d`)
(N-01, N-02). Tolerances allowed by the rules: 1e-9 relative (N-03); needed: none.

**Say:** "We profiled 30 datasets with the retired Spindle and with Shape: wide, tall,
multi-table, and 20 edge cases from three-row tables to every pattern family. We compared
all 31 fields. The rules allowed a tiny tolerance; we didn't need it. Every value was
bitwise identical. So when I show you a profile, it's a profile you can check, not one you
have to trust."

**Next:** "Now put profiling where it belongs: in the production pipeline."

## Section 4 — The production pipeline (21:30–31:30)

### Slide 16 — The prod pipeline → Live C2 (21:30, 1 min + demo ~3 min) — *verified locally*

**On the slide:** the pipeline: read prod table → `shape.profile` → save
`Files/shape/<table>/<timestamp>/<table>.shape` (+ HTML, summary) → `check` contract →
`diff` vs last run → **gate** (If Condition on `passed`). The core of the notebook:

```python
import shape

p = shape.profile("/lakehouse/default/Tables/orders_day1", name="orders_day1")
r = shape.check(p, "/lakehouse/default/Files/contracts/orders.json")
```

**Say:** "Every scheduled run profiles the production table, saves a timestamped shape
next to it, checks the contract, diffs against the previous run, and gates the load. After
a month you have a history of how your data behaved every single day, and it's just files."
→ **`DEMO.md` C2** (or **C2-local** if the dry run isn't done).

Notes: the snippet was run locally against a Delta table written with `deltalake`. The
notebook is `integrations/fabric/notebooks/shape_profile.ipynb`. **If R11 (the owner's
dry run) isn't done, don't run C2 in Fabric and don't say "this ran in Fabric": run
C2-local, and say "the same calls run in the Fabric notebook in the repo".**

**Next:** "Day 2."

### Slide 17 — Day 2 fails, with reasons (25:30, 1 min 30 s) — *verified*

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

### Slide 18 — Diff against the last run (27:00, 1 min 30 s) — *verified*

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

### Slide 19 — Exit codes (28:30, 1 min) — *verified*

**On the slide:**

```bash
shape check day2.shape contracts/orders.json          # exit 1: contract failed
shape diff day1.shape day2.shape --fail-on-drift      # exit 1: drift
shape check no-such.shape contracts/orders.json       # exit 2: input error
```

`0` pass · `1` failed check, or drift · `2` usage or input error.

**Say:** "Fabric, ADF, Airflow, GitHub Actions: anything that reads an exit code can gate
on a shape. And a missing or broken input is exit 2, not exit 1, so your pipeline can tell
'the data is bad' from 'the job is misconfigured'."

(The missing-`.shape` case exits 2 since `main` @ `b2dd663` (finding F1, fixed). Asserted
by `verify_snippets.sh` for both `check` and `diff`. If you demo it, use a build that
includes `b2dd663`: the TestPyPI 0.9.0 wheel predates it.)

**Next:** "Now the part everyone forgets. Who can read that `.shape` file?"

### Slide 20 — A raw `.shape` holds real values (29:30, 2 min) — *verified*

**On the slide:**

- A `.shape` keeps **up to 500 real values per column** (the most frequent, with counts),
  plus each column's **min and max**. That's by design: it's what makes the profiler
  match Spindle's exactly.
- In our stand-in, the saved `retail_prod.shape` contains real customer email addresses
  (asserted by `verify_snippets.sh`).
- README: "**Treat a `.shape` file, its HTML report and its JSON summary as you would the
  source data**, and don't share one from a sensitive table."
- Fabric runbook: the notebooks write them under `Files/shape/`; "**Grant access as you
  would to the source tables.**"
- Chip: "Safe profile (rare values suppressed, minimum group size): **PLANNED**, slide 25."

**Say:** "A raw profile is not anonymous. It keeps the top 500 values of every column. I
checked our stand-in: real email addresses are in that file. So today the rule is simple,
and it's written in the README and the Fabric runbook: treat a `.shape` as you would the
source data. Keep it in production, and grant access the same way. A safe profile
that can leave production is planned; that's the first thing on the 'how it will work'
slides."

**Next:** "Before we get to that, let's look at generation, which runs today as benchmark
code."

## Section 5 — Generation at scale (31:30–36:00)

### Slide 21 — Generate a schema, not columns (31:30, 30 s)

**On the slide:** the dependency order: store → customer → product → order → order_line
(parents before children). "Every foreign key points at a real primary key."

**Say:** "Quick callback to the last talk. You generate a schema in dependency order, never
column by column, so every order points at a real customer."

**Next:** "At scale."

### Slide 22 — Two million rows → Live C3 (32:00, 30 s + demo ~2 min) — *verified*

**On the slide:**

```bash
python benchmarks/vs_spindle/domain_1to1/generate.py \
    --impl reference_port --domain retail --scale medium --seed 42
# wrote .../reference_port/retail/medium/seed42 (1,965,400 rows, …s)
```

Label, large: "**Reference benchmark code, not the product.** Equivalence-verified against
Spindle (T-21, 60/60 columns). Not in the pip package. The product's generation engine is
planned (P4-07)."

**Say:** "This is benchmark code from our test harness, not the product, and it's not in
the pip package. It's the generator that made our 'production' stand-in, and it's been
verified against Spindle's output. Nine tables, just under two million rows, fixed seed so
rehearsal equals live. Then, the loop: I profile what I just generated with the same call
as slide 10." → **`DEMO.md` C3.**

Notes: the screen shows a live time. Say "that's my laptop, not a benchmark", and quote only
slide 23.

**Next:** "The measured numbers."

### Slide 23 — Generation at scale, in numbers (34:30, 1 min)

**On the slide:** "**Reference port**, not the product · 4 cores · not Fabric · equivalence
verified first (60/60 columns, T-21)".

| Retail | Rows | Spindle 3.0.1 | Port | vs Spindle |
|---|---:|---:|---:|---:|
| medium | 1,965,400 | 5.29 s | 1.26 s | 4.2x |
| large | 19,625,400 | 102.64 s | 14.89 s | 6.9x |

Footer: "**Target** ≥10x (N-50): not met by the port. Vectorising sped up the generate step
about 8x, but Parquet writing didn't get faster (N-42)."

**Say:** "Nineteen and a half million rows in under 15 seconds with the port, against 103
for Spindle, on four cores, after the output passed the equivalence test. That's still
under our 10x target, because writing Parquet became the bottleneck. That's a design input
for the engine, not a claim about it."

**Next:** "How do you test random data?"

### Slide 24 — Testing random data (35:30, 30 s)

**On the slide:** "Is the output as close to Spindle's as Spindle is to itself?" KS, TVD,
null rates, vocabulary, FK integrity, business rules; tolerance scaled by Spindle's
seed-to-seed spread (0.14–0.37 KS on one column, N-05); baseline seeds fixed at 43–46
(N-06).

**Say:** "You can't compare random data bitwise. So the test asks: is our output as close to
the reference as the reference is to itself with a different seed? And the seeds are fixed
in the rules, so nobody can shop for a lucky one."

**Next:** "Now, where this is going."

## Section 6 — How it will work (36:00–39:30) — PLANNED, no demo

> Every slide here carries a large **PLANNED** or **BEING BUILT** chip. No output, no
> numbers, no timings, no transcripts. Commands only as "planned syntax (may change)".
> Source: `docs/plans/COMPLETION_PLAN.md` §4 and §7 (work packages named on each slide).

### Slide 25 — How it will work: rebuild dev from the shape (36:00, 1 min 30 s) — PLANNED

**On the slide:** slide 5's right half, as a flow, each box tagged with its work package
and **PLANNED**:

1. **Safe profile** (P7-01, P7-02): export and validate a safe profile, in parity with
   Spindle's safe-profile output; an enforced minimum cohort; small cells suppressed in
   value counts, enums and histograms.
2. **`shape plan`** (P4-08): reports what generation will and won't preserve, and flags
   every field that isn't modelled.
3. **Generate from the shape** (P4-08): fits marginals, a Gaussian copula for correlations,
   missingness and seasonality from the profile.

Footer, small: "Planned syntax (may change): `shape profile validate --safe X.shape` ·
`shape plan X.shape` · `shape generate --from X.shape`."

**Say:** "Here's the design for step two. Production turns its raw shape into a safe one:
rare values suppressed, a minimum group size enforced, and a validator that fails if
anything slips through. Only that file leaves production. Dev asks `plan` what it'll get,
including a list of everything that isn't modelled. Then it generates from the shape.
None of this has shipped. The work packages are in the public plan, and the syntax may
change."

**Next:** "How will we know it worked?"

### Slide 26 — How it will work: proving the twin (37:30, 1 min) — PLANNED

**On the slide:** two boxes, both **PLANNED**:

- **P4-08's acceptance test:** profile → generate → profile must land within the T-22
  profiling tolerances for every modelled field. The feature doesn't ship until it does.
- **Fidelity report** (P4-09): per-table scores, scoring equivalent to Spindle's
  `FidelityComparator` (acceptance: within 0.5 points of it on retail), thresholds, and a
  non-zero exit code on failure.

Callout: "`shape.diff` of two profiles runs **today** (slide 18)."

**Say:** "Proof is built into the plan. The acceptance test for generating from a shape
is: profile production, generate, profile the result, and the two profiles must agree
within the same tolerances we use for profiler parity. And the fidelity report will score
each table and fail a build under a threshold. The comparison half, diffing two profiles,
you've already seen running."

**Next:** "And at scale."

### Slide 27 — How it will work: the engine and Fabric at scale (38:30, 1 min) — BEING BUILT / PLANNED

**On the slide:** the plan's §4 layer diagram, simplified:

- **Python** (`shape`): io · profile · artifact · diff · contracts · fidelity · generation ·
  privacy · CLI.
- ↕ Arrow PyCapsule, zero-copy.
- **Rust kernel** (`shape._kernel`): fused profile pass · hashing · HLL/KLL/SpaceSaving ·
  type inference · distribution fitting · RNG and sampling. Chip: **BEING BUILT**. Rule:
  every Rust function has a Python twin, kept in agreement by tests.

Right side, **PLANNED**:

- **Distributed profiling** (PF-02): per-partition bounded profiles through `mapInArrow`,
  merged on the driver; acceptance: equal to the single-process bounded profile within the
  sketch bounds.
- **Generation pipelines** (PF-06): a notebook that generates to lakehouse Delta tables,
  and a pipeline that generates, then profiles, then checks the output against the
  contract.

Footer: "**Targets**, not results: ≥10x Spindle, ≥30x stretch (N-50)."

**Say:** "Underneath, a Rust kernel is being built: one fused pass over each batch, the
sketches and hashing from slide 12, and every Rust function has a Python twin that must
agree with it. I'm not giving you a speed for it, because it hasn't been benchmarked. The
targets are 10x Spindle, 30x as a stretch, and they're targets. On top of that come
distributed profiling in Spark and generation pipelines in Fabric. Put those together with
the safe profile, and you get the full loop from slide 5: production publishes a safe
shape, dev regenerates from it and checks it."

(The dev-refresh loop that combines PF-06 with P4-08 and P7 is the design direction; PF-06
itself, as written, generates a named domain. Don't say it's a single planned deliverable.)

**Next:** "Everything I've shown today rests on one rule."

## Section 7 — How we know it's right (39:30–41:30)

### Slide 28 — Equivalence before timing, and the bug it caught (39:30, 2 min)

**On the slide:** top: "No number counts until that workload's equivalence check passes on
the timed output." Bottom: the stale-cache story in four steps (N-07): cached Spindle output
keyed by dataset name → D1 regenerated every run → 41 mismatches per D1 file → key the cache
by SHA-256 of the input. "A stale cache can fail you, and it can just as easily pass you."

**Say:** "Every number today came with an equivalence check on the same output. And the
harness checks itself too. Once, a cached reference result went stale and 41 fields
'mismatched'. The profiler was fine; the check was wrong. We fixed the cache key. The scary
version is the one where the stale answer happens to agree, and you get a pass you didn't
earn."

(The fix is commit `3b7c1f0` on `build/main-plan`, finding F4.)

**Next:** "Where this leaves us."

## Section 8 — Status and call to action (41:30–43:00)

### Slide 29 — Today, being built, planned (41:30, 45 s)

**On the slide:** one line per column of slide 7:

- **Runs today:** profile (one table or a schema), save, report, check, diff, exit codes,
  Fabric notebooks and pipeline gate.
- **Being built:** the Rust engine.
- **Planned:** safe profile, generate from a shape, fidelity report, distributed profiling,
  generation pipelines; then streams, plugins, Synapse and ADF.

**Say:** "What you saw running, you can run. What's being built and planned is in the
public plan, with a tracker, so you can watch it land."

### Slide 30 — Three things for Monday (42:15, 45 s)

**On the slide:**

1. **Profile one production table** in its own pipeline, and save the shape every run.
   Store it like the table.
2. **Write a three-rule contract** and gate on it.
3. **Diff this week's shape against last week's.**

Install line: **decide on October 2** (owner to-do in `STATUS.md`):

- if `pip index versions sqllocks-shape` finds 0.9.0 on pypi.org: `pip install sqllocks-shape`;
- otherwise: "`git clone github.com/sqllocks/shape && pip install ./shape`" (and don't say
  "on PyPI").

`github.com/sqllocks/shape`

**Say:** "Three things, all with what runs today. Profile one table you care about, keep
its shape, and keep it as safe as the table. Gate on three rules. And diff this week's
shape against last week's: you'll learn something about your data."

## Q&A (43:00–45:00)

### Slide 31 — Questions

**Likely questions:**

- *Is a `.shape` safe to share?* "No. Today a `.shape` holds real values, up to 500 per
  column plus min/max, and the README says to treat it like the source data. A safe profile
  is planned (P7-01/P7-02); I'll talk about its guarantees when it ships."
- *When does generate-from-shape ship?* "It's in the plan as P4-08, after the profile engine
  and the product generation engine. The tracker is public. I won't give a date on stage."
- *How fast is Shape itself?* "The product isn't in the benchmark yet; the numbers I showed
  are the reference port's. Product numbers come at gate G1, after equivalence passes."
- *Differential privacy?* "Spindle had an experimental version; the plan ports it as
  experimental (D-07). It isn't the default protection."
- *How fast in Fabric?* Only `LIVE_TIMINGS.md` rows, with SKU and vCores. If R11 isn't done:
  "I haven't published Fabric timings yet; the platform limits are on slide 14."
- *Great Expectations, Tonic, Gretel?* "Different starting points. Shape's is a
  committable, diffable profile. Try them on the same table." No comparative claims.
- *Streaming?* "Stream profiling is planned; the bounded mode on slide 12 is being built
  for it."

---

## Backup slides

- **B1** Full profiling table: `demo/BENCHMARKS.md` profiling section (N-20 to N-25), with
  "reference port, not the product" in the title.
- **B2** `results.json` harness run (N-30 to N-35), showing `"shape": null` as it is.
- **B3** Drift side effects from `DRIFT.md`.
- **B4** Fabric fallbacks (`DEMO.md` part E).
- **B5** One column's profile JSON: `p.to_dict()["tables"]["customer"]["columns"]["loyalty_tier"]`,
  taken from the rehearsal run.
