# Speaker script: Green Pipelines, Changed Data

33 slides plus 4 backups, about 45 minutes. Each slide has: **On the slide** (what is
shown), **Say** (speaker notes, in the speaker's voice, meant to be paraphrased), and
**Next** (the transition). Numbers cite `NUMBERS.md` (N-xx); don't add any that aren't there.

## Snippets on slides were run

Every Shape API and CLI snippet in this script is the §12.2 contract (plan
`docs/plans/COMPLETION_PLAN.md` §12.2) and was **run on 2026-09-30** by
[`verify_snippets.sh`](verify_snippets.sh), which asserts the output each slide shows:

- against `main` @ `764cb37` (`pip install -e ".[dev]"` plus `deltalake`, Python 3.11.15,
  pyarrow 25.0.1, numpy 2.4.6): **exit 0, ALL SNIPPETS OK**;
- against the published **TestPyPI** wheel `sqllocks-shape==0.9.0` in a clean venv: **exit 0**.

The data came from `demo/make_data.py` with the pinned Spindle checkout (`422e78d`), as
`DEMO.md` describes. Slides show the snippets exactly as the script runs them, minus the
`assert` lines. If you edit a snippet, edit `verify_snippets.sh` too and re-run it.

The architecture slides (15–20) show **no code** from the in-progress `build/main-plan`
branch. That code isn't in 0.9.0 and isn't the §12.2 API.

## Wording rules (read once before rehearsing)

- Say "early access". Never say "GA", "production-ready", "certified" or "successor".
- Spindle is "the retired Spindle project" or "the baseline". It is not a predecessor Shape
  "replaces" or "succeeds".
- Every timing is of the **port**, on a **4-core** machine, **not Fabric**, after
  equivalence passed. Say all four every time you show one.
- The Rust kernel has **no benchmark**. If asked "how fast is the Rust version?", answer:
  "We don't know yet. It isn't in the harness, so we don't quote it."

---

## Section 1 — The problem (0:00–4:00)

### Slide 1 — Title (0:00, 30 s)

**On the slide:** "Green Pipelines, Changed Data: Shape as Code for Microsoft Fabric".
Jonathan Stewart, SQLLocks. `github.com/sqllocks/shape`. Small tag: "Shape 0.9.0, early
access".

**Say:** "I'm Jonathan. For the next 45 minutes: why a successful pipeline run tells you
less than you think, a small open-source tool that closes some of that gap, how it's
built, and how we test it so we can believe it. There'll be two live demos, one on my
laptop and one in Fabric."

**Next:** "Let's start with a pipeline that worked."

### Slide 2 — The green run (0:30, 1 min)

**On the slide:** a screenshot-style mock of a pipeline run history: day 1 ✅, day 2 ✅.
Caption: "Both runs succeeded."

**Say:** "Here's a daily orders load. Day 1 succeeded, and so did day 2. Nobody got paged.
But between those two runs, four things changed in the data. None of them broke the
pipeline, and all of them would show up in a report as wrong numbers weeks later."

**Next:** "Here's what changed."

### Slide 3 — What actually changed (1:30, 1 min 30 s)

**On the slide:** table (N-70 to N-73):

| Table | Change | Day 1 | Day 2 |
|---|---|---|---|
| customers | email null rate | 4.97% | 20% |
| orders | status values | 5 values | + `lost` (2% of rows) |
| orders | order_total | mean 110.93 | every value × 1.40 (mean 155.30) |
| products | sku unique? | 5,000 unique | 5,050 rows, 5,000 distinct |

**Say:** "This is our demo data. It's generated, deterministic, and documented in
`demo/DRIFT.md`. One in five customers has lost their email. A status called `lost`
appeared on 2% of orders. Every amount went up 40%. And the product key isn't unique
any more, so every join on it now fans out. Each of these is a real incident pattern."

**Next:** "Why didn't anything catch it?"

### Slide 4 — Why the usual checks miss it (3:00, 1 min)

**On the slide:** three ticks and one cross. Schema unchanged ✅. Types unchanged ✅. Row
count plausible ✅. Behaviour of the data ❌.

**Say:** "The columns, types and row counts are all what we expected. The checks we
usually wire into pipelines look at structure, and the data broke in its behaviour: its
distributions, its value sets, its keys. To catch that you need a description of how the
data normally behaves, and a way to compare against it automatically."

**Next:** "That description is what Shape produces."

## Section 2 — Shape as Code (4:00–8:00)

### Slide 5 — Shape as Code (4:00, 1 min 30 s)

**On the slide:** "Shape as Code: a portable, executable description of how data
behaves, not merely its schema." Three words underneath: **Declared · Versioned ·
Executable.**

**Say:** "Infrastructure as code gave us servers described in files we can review, diff
and run. Shape as Code does that for data behaviour. A profile is a file, a `.shape`
artifact. You can commit it, diff it, attach it to a pull request, and run it in a pipeline
as a gate. It's offline by default, with no service to call, and the reader fails closed on
unsafe or corrupt files."

**Next:** "What's in one?"

### Slide 6 — What a profile contains (5:30, 1 min 15 s)

**On the slide:** a column card for `orders.order_total`: dtype `float`, null rate 0.0,
cardinality, min 0.0, max 5135.63, mean, std, plus labels "distribution family +
parameters · pattern · quantiles · value counts · temporal histograms · PK / FK".

**Say:** "Per column: type, nulls, cardinality, uniqueness, keys, the fitted distribution
family and its parameters, text patterns like emails or phone numbers, quantiles, top
values, and time-of-day and day-of-week histograms for timestamps. Across tables it
detects foreign keys. That's a lot of fields. On slide 22 you'll see why we can say each
one is right."

**Next:** "Before any code, let me be precise about what exists today."

### Slide 7 — Early access: what ships and what doesn't (6:45, 1 min 15 s)

**On the slide:** two columns.

| Works today (0.9.0) | In progress |
|---|---|
| `profile`, `save`/`load`, `check`, `diff` | Rust kernel under the same API |
| `shape profile / check / diff` CLI | Plugin system |
| `.shape` artifacts, HTML report | Stream profiling |
| JSON contracts, drift diff | Data generation with realistic distributions |
| Bitwise parity with Spindle's profiler on 30 datasets | Synapse and ADF integration |
| Fabric: notebooks, Environment, UDF, pipeline gates | |

**Say:** "Shape is early access. Profiling, contracts, diff, the report and the Fabric
integration work now, and that's what you'll see. Generation, plugins, streaming and the
Rust engine are being built in the open, on a public plan, and I'll say 'in progress' every
time we touch them. Early access also means the Fabric integration is new. I've run it,
but you should try it on your own data before you depend on it."

(Owner: if `LIVE_TIMINGS.md` is still empty on the day, change the last sentence to "the
Fabric pieces are built and tested locally; the live runbook is in the repo.")

**Next:** "Three verbs."

## Section 3 — profile / check / diff (8:00–16:00)

### Slide 8 — Install and the three verbs (8:00, 45 s)

**On the slide:**

```bash
pip install sqllocks-shape        # import shape · command: shape · files: .shape
```

`profile` → what the data looks like · `check` → does it meet the contract? · `diff` →
what changed since last time?

**Say:** "One install, one import. Three verbs. The API is small on purpose: this contract
is fixed for 1.0, so what you learn today keeps working."

(Owner: **check PyPI before the talk** (A5). On 2026-09-30, `sqllocks-shape` is only on
TestPyPI. If it still isn't on pypi.org, change the slide to "wheel: GitHub release" or
`pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ sqllocks-shape==0.9.0`.)

**Next:** "Profile first."

### Slide 9 — profile (8:45, 1 min 15 s) — *snippet verified*

**On the slide:**

```python
import shape

p = shape.profile("orders_day1.parquet", name="orders")
shape.save(p, "orders_day1.shape")
col = p.summary()["columns"]["order_total"]
print(col)
# {'dtype': 'float', 'null_rate': 0.0, ..., 'max': 5135.63, ...}
```

Side note: sources are CSV, Parquet, JSONL, Delta table directories, globs, directories,
pyarrow Tables, pandas DataFrames, or a dict of those for multi-table FK detection.

**Say:** "Give it a file, a folder, a Delta table, or a DataFrame. You get a profile back.
`save` writes the artifact, and `summary()` is a small JSON-safe view for logs and
notebooks. The full profile is `to_dict()`, in the same JSON shape as Spindle's profile, so
the two can be compared field by field. That matters later."

**Next:** "Now tell it what 'good' means."

### Slide 10 — A contract, day 1 passes (10:00, 1 min 15 s) — *snippet verified*

**On the slide:** left, `demo/contracts/orders.json` (the real file):

```json
{
  "row_count": {"min": 100000, "max": 5000000},
  "columns": {
    "order_id": {"dtype": "integer", "nullable": false, "unique": true},
    "customer_id": {"dtype": "integer", "nullable": false},
    "status": {"nullable": false,
               "allowed_values": ["cancelled", "completed", "processing", "returned", "shipped"]},
    "order_total": {"dtype": "float", "nullable": false, "min": 0, "max": 6000}
  },
  "required_columns": ["order_id", "customer_id", "order_date", "status", "order_total"],
  "allow_extra_columns": true
}
```

Right:

```python
r = shape.check(p, "contracts/orders.json")
print(r.passed, r.violations)
# True []
```

**Say:** "A contract is a small JSON file. Every rule is optional, and the vocabulary (the
dtype names, pattern names and distribution names) is the vocabulary the profiler
produces. `unique` uses the exact distinct count, not an estimate. Day 1 passes."

**Next:** "Day 2."

### Slide 11 — Day 2 fails, with reasons (11:15, 1 min 15 s) — *snippet verified*

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

**Say:** "It fails, and the result says why: which column, which rule, what was expected,
and what was observed. The new status `lost`, and a max of 7189.882 against a limit of 6000
(N-72). A pipeline can print that straight into its failure message, which you'll see in
Fabric shortly. Notice that the 40% jump itself isn't a contract rule. The v1 contract
format has no rule on the mean. The `max` bound is what catches it here."

**Next:** "Contracts need you to know the rules in advance. Diff doesn't."

### Slide 12 — diff, and an honest caveat (12:30, 1 min 30 s) — *snippet verified*

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

Call-out box: "Default `mean_shift_std` is 0.5. A +40% shift here is 0.43 σ, so **the
default doesn't flag it**. The demo uses 0.25." (N-74)

**Say:** "Diff compares two profiles: yesterday's artifact against today's data. With
the defaults it reports the new status. I want to be straight about the defaults: a 40%
jump in this column is only 0.43 standard deviations, because order totals are very
spread out, and the default threshold is half a standard deviation. So with defaults,
diff misses it. That's why I pass 0.25 here, and it's documented in the repo. The
contract caught it anyway. The third line is a side effect: the profiler keeps a value
list for this float column, and every day-2 value is new. Low severity, but you'll see it."

**Next:** "Humans want a picture."

### Slide 13 — The report and the artifact (14:00, 45 s) — *snippet verified*

**On the slide:** screenshot of `p.to_html()` / `--html` output (per-column table,
distributions, patterns). Beside it: `orders_day1.shape` → "one zip: `manifest.json` (format, version,
content hashes, content ID) + `profile.json`".

**Say:** "`to_html()` gives you a self-contained page with no external assets, so it works
offline and you can attach it to a ticket. The `.shape` file is what you version. When you
save one from the CLI you get its content ID, so you can tell whether two profiles are the
same."

**Next:** "And for pipelines, the CLI. Let me show you."

### Slide 14 — CLI and exit codes → Live demo 1 (14:45, 1 min 15 s + demo ~3 min) — *snippets verified*

**On the slide:**

```bash
shape profile orders_day1.parquet -o day1.shape --html day1.html --json day1.json
shape profile orders_day2.parquet -o day2.shape
shape check day1.shape contracts/orders.json          # exit 0
shape check day2.shape contracts/orders.json          # exit 1
shape diff day1.shape day2.shape --fail-on-drift      # exit 1
```

`0` = pass · `1` = failed check, or drift with `--fail-on-drift` · `2` = usage or input error

**Say:** "Every command returns an exit code a pipeline understands. Zero means pass, one
means the data failed, and two means you called it wrong or a file isn't there. Let's run
it." → **`DEMO.md` part A.**

Notes: the CLI `diff` uses the default thresholds (there is no threshold flag), so day 2 is
flagged for the new status values, not the mean shift. Don't demo a missing `.shape` file
for exit 2. That case currently exits 1 (finding F1 in `STATUS.md`). Use a missing contract
file if you want to show exit 2.

**Next (after the demo):** "That's the surface. Here's what's underneath, and what's
changing underneath."

## Section 4 — How it's built (16:00–23:00)

### Slide 15 — Layers (16:00, 1 min 15 s)

**On the slide:** the plan §4.1 diagram, simplified:

```
plugins (entry points, API v1)                          ← in progress
shape (Python): io · profile · artifact · diff · contracts · cli · plugin host
        │  Arrow PyCapsule interface (zero-copy)
shape._kernel (Rust): fused profile pass · hashing · sketches · fitting · RNG   ← in progress
Arrow C++ (pyarrow): CSV / Parquet / JSONL / IPC
```

Badge on the Python layer: "0.9.0 ships this, with the pure-Python profiler".

**Say:** "Three layers. Arrow is the only in-memory format, and everything is converted at
the edge. Python orchestrates. A Rust kernel will own the per-batch data path. What you
just used is the Python layer with a pure-Python profiler. The Rust layer is being built
now on a public branch, under the same API."

**Next:** "Why ship the pure-Python one first?"

### Slide 16 — The reference twin (17:15, 1 min 15 s)

**On the slide:** "Every kernel function has a pure-Python reference twin." Two boxes,
`rust` and `python`, with an arrow labelled "differential tests: must agree". Beneath:
`SHAPE_KERNEL = auto | rust | python` (label: `build/main-plan`).

**Say:** "Every Rust function gets a pure-Python twin. The twin is the oracle: differential
tests assert that the two agree. It's also the fallback, and that matters in Fabric, where
User Data Functions only accept platform-independent wheels. The profiler in 0.9.0 is that
twin, and it's the one verified bitwise against Spindle. On the build branch you choose the
implementation with an environment variable. The default is `auto`: Rust if it's there,
otherwise Python."

**Next:** "The boundary between the two has to be free."

### Slide 17 — Zero-copy FFI (18:30, 1 min)

**On the slide:** a `RecordBatch` crossing from Python to Rust and back, with the same
buffer addresses on both sides. Labels: "Arrow PyCapsule interface · pyo3 + pyo3-arrow ·
abi3 wheels". Acceptance test: "1M rows × 10 columns round-trip, identical buffer
addresses" (N-63).

**Say:** "Data crosses into Rust through the Arrow PyCapsule interface, so it's a pointer
handoff, not a copy. The acceptance test is blunt: send a million-row, ten-column batch
through Rust and back, and the buffer addresses must be identical. Then comes the rule that
makes it pay off: one native call per batch, covering every column."

(Status, for Q&A: done on `build/main-plan`, work package P1-01a; wheels for every target
built in CI, P1-01b. Not in 0.9.0.)

**Next:** "Once you can see every value cheaply, you need to name them consistently."

### Slide 18 — Canonical hashing (19:30, 1 min 15 s)

**On the slide:** "Seeded XXH3-64. Never Python `hash()`." Rules (N-62):

- `1` and `1.0` hash equal
- NaN and null are excluded
- strings → UTF-8 bytes
- timestamps → int64, normalised to µs

**Say:** "Distinct counts, sketches and joins all rest on hashing. Python's `hash()` is
salted per process, so it differs between runs and machines. That's fatal for a
reproducible profile. So every hash is seeded XXH3-64, after canonicalising: the integer
one and the float one-point-oh are the same value, NaN and null never count as values,
and timestamps are normalised to microseconds. The Rust version and the Python twin must
produce the same 64 bits for every value."

(Status: done on `build/main-plan`, P1-02. Tests cover every Arrow primitive type and
three `PYTHONHASHSEED` settings.)

**Next:** "Hashing is also what makes the bounded mode possible."

### Slide 19 — Exact vs bounded (20:45, 1 min 15 s)

**On the slide:** two columns.

| `exact=True` (default for files and tables) | bounded (`exact=False`) |
|---|---|
| every statistic exact, as Spindle computes it | HLL (p=14), KLL (k=200), SpaceSaving (64) |
| used for **parity and every timing** | for streams and data larger than RAM |
| | bounded memory, mergeable, error bounds tested |

"One output schema; `error_models` records which mode ran." Label: "bounded mode:
`build/main-plan`".

**Say:** "There are two modes and one output. Exact is the default for files and tables,
and it's what we use for parity and for every benchmark, so Shape never does less work
than Spindle while it's being timed. Bounded mode swaps in sketches (HyperLogLog, KLL
quantiles, SpaceSaving top-k) for streams and larger-than-memory data. They're mergeable
and bounded, and each is tested against its published error bound, not against Spindle.
The profile records which mode produced it."

(Status: sketches done on `build/main-plan`, P1-03. Acceptance bounds are N-61. The
`exact=` parameter is not in the 0.9.0 API; don't show it as code.)

**Next:** "The rules that tie this together."

### Slide 20 — Runtime split rules (22:00, 1 min)

**On the slide:**

1. Python never touches individual values on a hot path.
2. One native call per batch, covering every column.
3. Plugins take whole batches (Arrow in, Arrow out).
4. Arrow is the only in-memory format.
5. One profile schema: batch, stream, merged, partitioned.

**Say:** "Five rules. The first one does most of the work: if Python loops over values,
it's slow no matter what's underneath. Plugins follow the same rule, with batches in and
batches out, so a third-party plugin can't quietly add a per-row Python loop to the hot
path."

**Next:** "None of this is worth anything if the answers change. So how do we know
they're right?"

## Section 5 — How we know it's right (23:00–30:00)

### Slide 21 — Equivalence before timing (23:00, 1 min 15 s)

**On the slide:** the rule, large: "No performance number counts until that workload's
equivalence verifier passes **on the timed output**." Beneath, smaller: "Never lower a
gate. Never loosen a tolerance. Never skip a test. Never fabricate evidence."

**Say:** "This is the rule I care most about. Speedups are easy if you're allowed to
compute something slightly different. So the harness checks the output first, and only
then records the time. It checks the same output that was timed, not a separate run. And
the gates and tolerances are written down in advance and don't move."

**Next:** "Here's what that looks like for the profiler."

### Slide 22 — Bitwise parity with Spindle (24:15, 1 min 30 s)

**On the slide:** a thumbnail of the per-field matrix from `DM-01_verify_output.txt`
(rows are fields, columns are datasets, every cell `n/n*`). Big text: **30 / 30 datasets ·
34 fields · every value bitwise-identical** (N-01, N-02). Footnote: "vs Spindle 3.0.1,
pinned at git 422e78d".

**Say:** "The baseline is the retired Spindle project, pinned to one commit. We run
Spindle's profiler and Shape's on 30 datasets: wide, tall, multi-table, and 20 edge
cases: tables of 3 to 3,000 rows, constant and all-null columns, every pattern family,
signed zeros, fractional timestamps, UUID-only keys. Then we compare 34 fields, from null
counts to fitted distribution parameters. The standard allows tiny floating-point
tolerances. We didn't need them: every value is bitwise identical, and the 'within
tolerance but not identical' section of the report is empty."

**Next:** "Profiling is deterministic, so exact is the right bar. Generation isn't."

### Slide 23 — Two standards (25:45, 1 min 15 s)

**On the slide:**

| Profiling (T-22) | Generation (T-21) |
|---|---|
| exact match: dtype, nulls, cardinality, enums, top-500 values, PK/FK, pattern | statistical: KS, TVD, null rate, vocabulary, FK integrity, business rules |
| mean/std/quantiles within 1e-9 relative; distribution params within 1e-6 | tolerance scaled by **Spindle's own** seed-to-seed spread |
| | baseline seeds fixed: **43, 44, 45, 46** |

Footnote: "The generation port meets T-21: 60/60 columns at every scale (N-04)."

**Say:** "Generation can't be compared bitwise, because different random numbers give
different rows. So the generation standard asks a different question: is Shape's output
as close to Spindle's as Spindle is to itself with a different seed? For one column,
Spindle's own runs differ by a KS of 0.14 to 0.37 (N-05). And the seed set is fixed in the
rules, so nobody can shop for a lucky seed. Generation isn't in the product yet. This
standard is waiting for it."

**Next:** "Now the story I promised: the harness catching a bug in itself."

### Slide 24 — The stale-cache bug (27:00, 1 min 30 s)

**On the slide:** a timeline.

1. The verifier caches Spindle's output, keyed by **dataset name**.
2. The benchmark run **regenerates** D1 every time.
3. Old Spindle output vs new D1 → **41 mismatches** on each D1 file; the run exits 1 (N-07).
4. Fix: key the cache by the **SHA-256 of the input files**.

Punchline: "A stale cache can fail you, and it can just as easily pass you."

**Say:** "Spindle is slow, so the verifier caches its output. The cache key was the dataset
name. The benchmark regenerates dataset D1 on every run, so a cache filled from an earlier
D1 was compared against a new one. Result: 41 mismatches per file and a red run. Nothing
was wrong with the profiler. The check itself was wrong. We reproduced it, then keyed the
cache by a hash of the input bytes. The part that keeps me up at night is the other
direction. If the cached answer had happened to agree, we'd have had a false pass and never
known. That's why the verifier's own inputs get the same scrutiny as the product."

(Source: commit `3b7c1f0` on `build/main-plan`, "P0-07 fix: key the Spindle profile cache by
input content".)

**Next:** "So, after all that: the numbers we're allowed to say."

### Slide 25 — The numbers we're allowed to say (28:30, 1 min 30 s)

**On the slide:** header strip: "4 cores · Intel Xeon 2.10 GHz · Linux · Python 3.11 · not
Fabric · equivalence verified first · **port, not product**".

| Profile, Parquet | Spindle 3.0.1 | Port, 4 threads | Port, 1 thread |
|---|---:|---:|---:|
| D2: 1M × 20 | 26.51 s | 1.82 s (14.6x) | 4.83 s (5.5x) |
| D3: 5M × 10 | 47.91 s | 5.35 s (9.0x) | 13.73 s (3.5x) |
| D4: 100k × 200 | 28.31 s | 4.23 s (6.7x) | 13.34 s (2.1x) |

Footer: "**Targets** for Shape: ≥10x minimum, ≥30x stretch, per workload (N-50). Rust
kernel: not benchmarked yet."

**Say:** "Here's the one table I'll show. On a 4-core machine, after the outputs were
checked identical, profiling a 1M-row, 20-column Parquet file took 26.5 seconds with
Spindle and 1.8 with the vectorised port. That port is what `shape.profile` was built
from. These are timings of the port, not of the library you just saw, and not measured
in Fabric. Look at the other rows too. On wide data the port is only 6.7x, which misses
our own 10x minimum. That's what the Rust kernel is for, and it has no number yet, so I
won't give you one."

(Sources: N-21, N-23, N-24, `profile_bench.json`, median of 5. If anyone asks about
variance: the harness run a day later on a similar machine gave 11.6x for D2 Parquet with
3 runs (N-33). That's why the gates are same-job ratios.)

**Next:** "Enough slides. Let's go where the data lives."

## Section 6 — Fabric in practice (30:00–38:00)

### Slide 26 — Four surfaces, honest limits (30:00, 1 min 15 s)

**On the slide:**

| Surface | How Shape gets there | Limit to know |
|---|---|---|
| Python notebook | `%pip install` the wheel | 2 vCores by default; the demo sets 8 (N-80) |
| Environment + PySpark notebook | upload the wheel once | Quick publish ~5 s; Full 3–6 min (N-84) |
| User Data Function | pure wheel as a private library | 240 s per call; wheel < 28.6 MB; Shape caps files at 50 MB (N-81 to N-83) |
| Pipeline | Notebook or Functions activity as a gate | the notebook's exit value drives the If Condition |

**Say:** "Shape runs in four places in Fabric. In a Python notebook you pip install it. For
Spark, you add it to an Environment once. For User Data Functions it goes in as a private
library, which is why the pure-Python wheel matters: it's about 185 KB against a 28.6 MB
limit. And a pipeline can use either the notebook or a UDF as a quality gate. Each has
limits, and the UDF limit is the one people hit: 240 seconds a call. So Shape's UDF refuses
big files and tells you to use the notebook instead."

**Next:** "Here's the notebook." → **Live demo 2 starts (`DEMO.md` part B).**

### Slide 27 — Profile a lakehouse table (31:15, ~2 min 30 s incl. demo) — *snippet verified*

**On the slide:**

```python
import shape

p = shape.profile("/lakehouse/default/Tables/orders_day1", name="orders_day1")
r = shape.check(p, "/lakehouse/default/Files/contracts/orders.json")
```

Caption: "Delta tables are read with `deltalake` (preinstalled in Fabric Python notebooks)."

**Say:** "Same three verbs. The Delta table is just a directory to Shape. In the notebook I
profile it, render the report inline, check the contract, diff against yesterday's
artifact if I pass one, and write everything to the lakehouse. The last cell hands a
small JSON result to the pipeline."

Notes: the slide snippet was run locally against a Delta table written with `deltalake`
(the Fabric path itself is the owner's dry run). The notebook `shape_profile.ipynb` wraps
this: paths resolve under `/lakehouse/default/Files`, and `notebookutils.notebook.exit(...)`
is the last top-level statement.

**Next:** "Now put that in a pipeline."

### Slide 28 — The pipeline gate (33:45, ~2 min 30 s incl. demo)

**On the slide:** the pipeline `shape_gate_notebook`: Notebook activity → If Condition
(`passed`) → True: continue · False: Fail activity. Two runs: `orders_day1` ✅,
`orders_day2` ❌ with the message listing `status allowed_values` and `order_total max`.

**Say:** "The notebook's exit value carries `passed`, the violations and any drift. The If
Condition reads `passed`. Day 1 goes green and the load continues. Day 2 stops at the gate,
and the error message names the rules, not just 'failed'. It's the same contract file you
saw on my laptop."

Notes: the Fabric notebook's diff uses the **default** thresholds, so the exit value's
`changes` shows the new status values but not the mean shift (N-74). The gate fails on the
contract regardless.

**Next:** "And if you don't want a notebook session in the loop at all?"

### Slide 29 — UDF gate and limits (36:15, 1 min 45 s)

**On the slide:** pipeline `shape_gate_udf`: Functions activity `profileLakehouseFile` →
`checkProfile` (`failOnViolation = true`). Day 2 fails at `CheckContract`. Callout: "files
up to 50 MB by default; bigger → notebook". Space for measured timings: "Measured live on
<SKU>, <vCores>: see `LIVE_TIMINGS.md`" (**leave blank or delete until the dry run is done**).

**Say:** "Same gate, no notebook: two function calls. Profile a lakehouse file into a
`.shape`, then check it with fail-on-violation. There's a size cap because of the 240-second
limit, and the cap is deliberate. If you point it at something big, it tells you to use the
notebook. I'd rather it say no than time out halfway through."

(If `LIVE_TIMINGS.md` has been filled in: show only its rows, with its SKU and vCores, and
say "measured on this capacity, once". Otherwise say: "I'm not quoting Fabric timings until
I've measured them properly.")

**Next:** "Where this goes next."

## Section 7 — Roadmap (38:00–40:30)

### Slide 30 — Roadmap (38:00, 1 min 30 s)

**On the slide:** a phase strip, each item with a status chip.

| Phase | What | Status (2026-09-30) |
|---|---|---|
| Demo track | profile / check / diff, CLI, report, Fabric integration | **shipped in 0.9.0 (early access)** |
| 0 | Honest baseline: security fixes, CI, harness, pinned baselines | **done** (Gate G0) |
| 1 | Profiling engine on Rust | **in progress**: FFI, wheels, hashing, sketches, readers, type inference done; fused kernel, engine, Rust parity next |
| 2 | Plugin system (API v1; first-party features built the same way) | planned |
| F | Fabric, Synapse, ADF pipeline integration | planned (after plugins) |
| 3 | Stream profiling (Kafka, Event Hubs) | planned |
| 4–5 | Generation with realistic distributions; streaming during generation | planned |

**Say:** "The whole plan is public in the repo, down to the acceptance criteria for each
work package. Phase 0 cleaned house. Phase 1 is putting the Rust kernel under the API you
saw today, and six of its thirteen work packages are done. Then plugins, where our own features use the
same API as yours, then deeper pipeline integration, streams, and eventually generation
with realistic distributions, holidays and trends. Each of those is 'planned', not
'coming soon'."

**Next:** "And here's how we'll decide when something is done."

### Slide 31 — The gates (39:30, 1 min)

**On the slide:** "**Targets**, not results":

- Profiling and generation: ≥10x Spindle minimum, ≥30x stretch, per workload, same-job ratio
- CLI start-up ≤300 ms
- Stream profiling ≥80% of batch throughput
- Profiling parity (T-22) on every dataset, and generation equivalence (T-21) before any timing

**Say:** "These are the gates. They're targets, and none of them is met by the product yet.
When one is, the number will come from the harness, with the machine and core count next
to it, and after the equivalence check has passed. If you see a Shape speed claim without
those, it didn't come from us."

**Next:** "So what can you do today?"

## Section 8 — Call to action (40:30–41:30)

### Slide 32 — Try it (40:30, 1 min)

**On the slide:**

```bash
pip install sqllocks-shape
shape profile your_table.parquet -o your_table.shape --html report.html
```

- The demo in 5 minutes: `demo/` and `docs/talks/shape-v1/DEMO.md`
- Fabric: `integrations/fabric/RUNBOOK.md`
- Tell us what breaks: issues on `github.com/sqllocks/shape`
- Follow the build: `docs/plans/COMPLETION_PLAN.md`

**Say:** "Profile one table you care about today. Save the artifact. Write a three-rule
contract. Put the check in your pipeline. When it's wrong or slow or confusing, open an
issue, because that's what early access is for."

(Owner: same PyPI check as slide 8.)

**Next:** "Questions."

## Q&A (41:30–45:00)

### Slide 33 — Questions

**On the slide:** repo URL, "Shape 0.9.0, early access", and a QR code for the repo.

**Likely questions, with answers grounded in the repo:**

- *How is this different from Great Expectations or other data-quality tools?* "Shape starts
  from a full statistical profile saved as a portable artifact. You can diff it without
  writing rules, and contracts are optional on top. I'd rather you try both on the same
  table than take my word for it." (No comparative claims: nothing in the repo measures
  other tools.)
- *How fast is it in Fabric?* Only `LIVE_TIMINGS.md` rows, if filled in. Otherwise: "Not
  measured yet, so I won't guess."
- *How fast is the Rust version?* "Not benchmarked yet. It has to pass parity first."
- *Does it send data anywhere?* "No. It's offline by default, and nothing needs network
  access or a cloud service."
- *Why the mean-shift default of 0.5?* "It's the documented default (plan §12.3). Our own
  demo shows a case it misses, so tune it per column. We say so in `DRIFT.md`."
- *Spark-scale data?* "The PySpark notebook profiles on the driver, capped at 5 million rows,
  and samples above that, with `sampled: true` in the result. Distributed profiling is on the
  pipeline-integration roadmap, not in 0.9.0."
- *License?* MIT.

---

## Backup slides

### B1 — Full profiling table (N-20 to N-25)

The six rows of `demo/BENCHMARKS.md`, "Profiling" section, with its header ("4 cores … port
MT / port 1T … not measured on `shape.profile`").

### B2 — The harness run (`results.json`, N-30 to N-35)

Workload, verifier `pass`, Spindle, reference port, ratio. Footer: "`shape`: null, because the
product isn't in the harness yet. Machine M2, 3 runs."

### B3 — Drift side effects (from `DRIFT.md`)

`orders.order_total` `new_categorical_values` (value list kept for a float column);
`products.category_id` `distribution_change`; `products.sku` uniqueness isn't a diff kind,
so the contract catches it.

### B4 — If Fabric misbehaves

The fallback table from `DEMO.md` part C.
