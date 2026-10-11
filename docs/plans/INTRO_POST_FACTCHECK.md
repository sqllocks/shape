# Fact-check of the Shape introduction post against 0.9.1

Written 2026-10-11 on `plan/launch-review`. Report only: nothing in `sqllocks/shape-marketing`
was changed.

**What was checked.** The current introduction draft and its channel adaptations in
`sqllocks/shape-marketing`, branch `content/shape-introduction` at `4b4d872`:
`content/inbox/shape-introduction-original.md` (the post, status `owner-working-draft`,
`approved: false`), `content/drafts/{x,linkedin,community}-introduction.md`,
`content/drafts/fifty-word-blurb.md`, and the site copy the launch kit asks the website session
to publish, `planning/website-requirements-launch.md` (branch `claude/shape-launch-kit` at
`e62b215`, merged into the content branch). `MESSAGING_AND_PRODUCT_STATUS.md` (approved
website copy) was read too; its "Coming: Industry Profile Packs" copy makes no engine claim.

**What it was checked against.** `release/0.9.1` at `f49aa94` (`sqllocks/shape`), read from
the code and docs, and run: the pure wheel and the plugin wheels were built from that commit
with the exact lines of `runbooks/live/README.md` (branch `plan/post-launch`) and installed in
a clean virtual environment (`shape --version` prints `shape 0.9.1`). Every command the post
prints was run there, on the post's own evidence files (`content/claims-log/evidence/
shape-introduction/customers.csv` and `customers_next.csv`). `pip index versions` against
PyPI was run on 2026-10-11. The W9 items cited as "coming next" are the §13 rows of
`docs/plans/COMPLETION_PLAN.md` and the issues `sqllocks/shape#2` to `#17`.

Ratings: ✅ true · ⚠️ true with a qualifier · ❌ false at 0.9.1.

---

## ❌ False at 0.9.1

### F1. The quick-start install lines (post, "Quick start", both blocks; website R2 install line)

> `pip install "sqllocks-shape[domains,pandas]==0.9.1"`
> `pip install "sqllocks-shape-databases[duckdb]==0.9.1"`

PyPI serves `sqllocks-shape` **0.9.0 only**, and no `sqllocks-shape-*` plugin at all
(`pip index versions sqllocks-shape` → `0.9.0`; `sqllocks-shape-domains` and
`sqllocks-shape-databases` → "No matching distribution found", 2026-10-11). `v0.9.1` has no
tag or GitHub release. Both lines fail for a reader today; the marketing claims log already
records the same result ("exact pinned PyPI install fails"). The extras themselves are right:
`domains` and `pandas` exist in `pyproject.toml` `[project.optional-dependencies]`, and
`duckdb` is an extra of `plugins/shape-databases/pyproject.toml` (it installs the `duckdb`
package the sink needs).

**Suggested replacement.** Publish before the post goes out (POST_LAUNCH item 0: tag
`v0.9.1`, publish core and the plugins), then keep the lines as written. If the post must go
out first, replace both blocks with the source build from `runbooks/live/README.md`:

```bash
git clone https://github.com/sqllocks/shape && cd shape && git checkout release/0.9.1
python -m venv .venv && . .venv/bin/activate && pip install -U pip
python scripts/build_pure_wheel.py --out wheels
pip wheel --no-deps -w wheels plugins/shape-domains plugins/shape-databases
pip install --find-links wheels "sqllocks-shape[domains,pandas]==0.9.1" "sqllocks-shape-databases[duckdb]==0.9.1"
```

and say: "0.9.1 is being published to PyPI; until it lands, build from the release branch."

### F2. "Shape Free is the open engine you can install today." (post, "Status")

Same fact as F1: today it can be built from the repository, not installed from PyPI at the
pinned version. **Replacement:** "Shape is the open engine; 0.9.1 is on the `release/0.9.1`
branch today and on PyPI as soon as the release is tagged." (Once published, the sentence is
✅.) Note that the repository never uses the name "Shape Free": `docs/BRANDING.md` names the
product "Shape by SQLLocks", the distribution `sqllocks-shape`. The name is the owner's
marketing decision (HANDOFF 2026-10-09), not a repo fact.

### F3. "email me at contact@shapedata.ai" (post, "Fabric-native. Not Fabric-only.")

`docs/BRANDING.md` on `release/0.9.1`: "Use support@shapedata.ai for public contact." The
launch kit's own website requirement R6 says the site uses `support@shapedata.ai` as the single
public address and "the post is being changed to match"; the post was not changed.
**Replacement:** `support@shapedata.ai`, or delete the address and keep "open an issue on the
repo".

### F4. "Or send the same tables straight to DuckDB: `shape generate retail --scale small --seed 42 --to duckdb:///data/retail.duckdb`" (post, "Quick start")

Run exactly as printed in an empty directory the command **exits 2**:
`shape: error: cannot open the DuckDB database data/retail.duckdb: IO Error: Cannot open file
".../data/retail.duckdb": No such file or directory`. The DuckDB sink does not create the
parent folder. With `mkdir -p data` first it writes `21,750 rows in 9 tables` (exit 0), which is
what the marketing claims log recorded (the builder's directory existed). The screenshot caption
and the row counts are right. **Replacement:** add `mkdir -p data` before the command, or write
to the working directory: `--to duckdb:///retail.duckdb`.

### F5. Website R3, "In the open-source engine" list: "PostgreSQL; MySQL" beside "SQL Server, Azure SQL and Fabric SQL" with no read/write distinction, and only "Snowflake and Databricks are write targets"

At 0.9.1 the engine **profiles none of PostgreSQL, MySQL, Snowflake, Databricks, BigQuery,
Redshift, Eventhouse or Kafka (as a table) directly**, and has **no BigQuery or Redshift
sink**. Evidence, `release/0.9.1`:

- `plugins/shape-databases/pyproject.toml` registers `shape.sinks` only (`postgres`, `mysql`,
  `snowflake`, `databricks`, `duckdb`); no `shape.sources` entry.
- The only database **sources** in the whole tree are `mssql` (`plugins/shape-sqlserver`,
  with `shape profile-db`), `duckdb` (`plugins/shape-integrations`, `duckdb://FILE?table=T`),
  `onelake` and `semantic-model` (`plugins/shape-fabric`). `shape profile postgresql://...`
  stops in `src/shape/profile/reference/sources.py:_remote_table` with
  "no installed source plugin reads ...".
- `docs/KNOWN_LIMITATIONS.md`: "Snowflake and Databricks are write targets. Shape does not
  profile from them yet."
- No `bigquery`, `redshift`, `oracle`, `iceberg` or `eventhouse` scheme exists anywhere
  (`grep -r` over `src/` and `plugins/`); the Eventhouse is an **emitter** only.
- Kafka and Event Hubs are read only through the experimental `shape stream-profile`
  (`shape.stream_sources`), not by `shape profile`.

The post itself is careful here ("Shape generates into Snowflake and Databricks; it does not
profile from them yet") and never claims PostgreSQL or MySQL profiling, so the post is ⚠️
(see W4) and the website requirement is ❌ as written.

**Replacement for R3:** split the engine section into "reads (profiles)" and "writes
(generates into)":

- Reads: CSV, TSV, Parquet, JSON, JSON Lines, XML, Arrow IPC, Excel; Delta tables (local,
  OneLake, ADLS Gen2); DuckDB files; SQL Server, Azure SQL and Fabric SQL (`profile-db`,
  needs the ODBC driver); Fabric Lakehouse (OneLake) and Power BI semantic models; Kafka
  topics and Event Hubs (stream profiling, experimental); X12 and HL7 v2.
- Writes: CSV, TSV, Parquet, JSON Lines, Arrow IPC, Excel, SQL scripts; Delta (local, OneLake,
  ADLS Gen2, Fabric mirroring landing zone); DuckDB; PostgreSQL; MySQL; SQL Server, Azure
  SQL and Fabric SQL; Fabric Warehouse; Synapse dedicated pools; Snowflake; Databricks
  (Unity Catalog); Kafka, Event Hubs, Eventstream and Eventhouse (events).
- "Coming next: profiling PostgreSQL and MySQL directly (W9-01, #2); Snowflake and Databricks
  (W9-02, #3); Fabric Eventhouse tables (W9-11, #12); BigQuery (W9-13, #14), Amazon Redshift
  (W9-14, #15), Oracle (W9-15, #16) and Apache Iceberg (W9-16, #17) as both source and sink."

Also fix the engine's own docs before linking them: `docs/databases/index.md` and
`docs/databases/postgresql.md` on `release/0.9.1` say "The integrations plugin reads a selected
table through its PostgreSQL source" / "`shape-integrations` supplies database reads for
PostgreSQL, MySQL and DuckDB". The integrations plugin has no PostgreSQL or MySQL code
(`plugins/shape-integrations/pyproject.toml` registers the `duckdb` source only; `grep -i
postgres plugins/shape-integrations/src` finds nothing). A reader who follows the website
to those pages will be told the opposite of the truth. This is a 0.9.1 doc defect, not a
marketing error; it should go to the lead as a one-line fix (and W9-01 makes it true).

---

## ⚠️ True, but needs a qualifier

### W1. "There's a native Rust kernel under the generator, with a pure-Python twin that produces the same bits." (post, "Create data")

True of the code: `docs/GENERATION_KERNEL.md` ("Every result of the two is equal bit for bit,
and the same on every CPU and operating system"), and `rust/shape-kernel` is in the tree. Two
qualifiers: the kernel page is marked `Status: experimental`; and the wheel a reader gets
depends on the release. The PyPI 0.9.0 wheel is pure Python (`py3-none-any`, decision log
2026-09-30), so it runs the Python twin only; `.github/workflows/release.yml` builds the
platform wheels (T-04) with the native kernel for 0.9.1. **Wording:** "There's a native Rust
kernel under the generator, with a pure-Python twin that produces the same bits; the pure
wheel runs the twin, the platform wheels run the kernel."

### W2. "Profiling reads files, Delta, DuckDB, Kafka, Event Hubs and SQL Server catalogs." (post, "Fabric-native")

Each is true with an install or status note: DuckDB through `sqllocks-shape-integrations[ibis]`
(`shape profile 'duckdb://data/retail.duckdb?table=order'`, run, exit 0), not the databases
plugin the post installs; Kafka and Event Hubs through `shape stream-profile`, which the CLI
labels `(experimental)`; SQL Server through `shape profile-db` / `mssql://` in
`sqllocks-shape-sqlserver`, which needs the Microsoft ODBC driver. It also reads OneLake and
ADLS Gen2 (`abfss://`), Excel, JSON, XML, Arrow IPC, Power BI semantic models and X12/HL7.
**Wording:** "Profiling reads files (CSV, Parquet, JSON, Excel and more), Delta tables locally
or in OneLake and ADLS, DuckDB files, SQL Server catalogs, and, experimentally, Kafka topics
and Event Hubs streams."

### W3. "Fabric, Snowflake, Databricks, PostgreSQL, MySQL, DuckDB and dbt are first-party integrations." (post)

All seven ship as plugins in the repository (`plugins/shape-fabric`, `shape-databases`,
`shape-dbt`), so "first-party" is right. Qualifiers: none of the plugins is on PyPI yet (F1);
and for PostgreSQL, MySQL, Snowflake and Databricks the integration is **write-only** at 0.9.1
(F5). **Wording:** add one sentence after it: "Today Shape writes into all of them and reads
from Fabric, DuckDB and dbt projects; reading PostgreSQL and MySQL tables directly is the next
release (W9-01)."

### W4. "Shape generates into Snowflake and Databricks; it does not profile from them yet." (post)

True (`docs/KNOWN_LIMITATIONS.md`), but incomplete in a way a reader will trip on: the same
holds for PostgreSQL and MySQL. **Wording:** "Shape generates into PostgreSQL, MySQL,
Snowflake and Databricks; it does not profile from them yet (coming next: PostgreSQL and
MySQL in W9-01, Snowflake and Databricks in W9-02)."

### W5. "Export contracts to Great Expectations, pandera, JSON Schema or DDL. There are MLflow, OpenLineage and Presidio adapters too." (post; website R3)

`shape contract emit --to ddl|jsonschema|pandera|gx` exists (`docs/CONTRACT_EMIT.md`) and
the adapters exist in `sqllocks-shape-integrations` (`docs/plugins/integrations.md`: also
SDMetrics, Anonymeter and Ibis/DuckDB). Both pages are `Status: experimental`, and
`contract` is listed as `(experimental)` in `shape --help`. **Wording:** "Export contracts
(experimental today) to ..."; or leave the sentence and add "experimental" once to the list.

### W6. "Read .shape diffs in VS Code." (post; website R3 "a VS Code extension")

`editors/vscode/` ships the extension and `README.md` describes exactly that ("A readable
`.shape` file", "Compare with Git HEAD"). Qualifier from the same README: "The extension is
not on a marketplace"; it is built with `npm run package` or taken from the CI artifact
`shape-vscode` and installed with `code --install-extension shape.vsix`. **Wording:** "Read
`.shape` diffs in VS Code with the extension in the repo (install it from the `.vsix`; it isn't
on the Marketplace yet)."

### W7. "Use the GitHub Action as a required drift check." / "A failed required check can block a merge." (post; website R3)

`action.yml` and `docs/GITHUB_ACTION.md` (`Status: available`, `fail-on: drift`, "Make the
job a required status check") support it. Qualifier: the action installs `shape-version` from
PyPI (`uses: sqllocks/shape@main` with `shape-version: '0.9.1'`), so it works for 0.9.1 only
after F1 is resolved; and the docs' hosted transcript is still an owner placeholder. **Wording:**
keep, once 0.9.1 is published.

### W8. "A Shape Profile captures null rates, cardinality, distributions, formats, correlations and candidate relationships in a portable `.shape` file." (post, intro; website R2)

All present (`shape cat` of the retail profile shows `correlation_matrix`, `relationships[*]`
with `evidence`, `temporal_histogram`, patterns, `null_rate`, `cardinality`). Qualifier from
`docs/JOINT.md`: for a multi-table dataset the cross-column joint analysis (dependencies,
associations, placeholders) is **off by default** and needs `--joint`; a single table has it
on. Correlations and candidate keys do not depend on it. **Wording:** no change needed in the
post; if the website lists "dependencies between columns", add "with `--joint` for datasets".

### W9. The `shape diff ... --fail-on-drift` quick start "fails when a change crosses a threshold" (post, "Profile, compare and generate from your own data")

The command runs (exit 0) and the sentence is conditional, so it is true. Qualifier: on the
two evidence files the post is built from (`customers.csv` vs `customers_next.csv`, which
differ by one null in one row) the comparison reports `"drifted": false` and exits 0, and
notes that `amount`'s range is "not evaluable" because the safe capture withholds extremes.
A reader who runs the quick start on those files sees no failure. The claims log says the
null-doubling example is "a separate controlled synthetic pair". **Wording:** either ship a
`customers_next.csv` that actually drifts (the controlled 10%→20% null pair) or say "with a
change large enough to cross a threshold, the comparison exits 1".

### W10. "The safe check: clean on the default capture, refused on a full capture." (post, screenshot caption; `shape profile validate --safe`)

Verified on single-table profiles: `CLEAN: no leaks found` exit 0 on the safe capture,
findings and exit 1 on `--capture full`. Qualifier from `docs/KNOWN_LIMITATIONS.md` (and
reproduced here on the retail `--dataset` profile): "`shape profile validate --safe` fails on
multi-table `--dataset` profiles in 0.9.1" (it flags the sampling metadata's column-name lists
as raw strings, exit 1). The post's screenshot uses a single table, so the caption is right.
**Wording:** keep; do not generalise to folders until the dataset validator is fixed.

### W11. "Try the bounded browser demo" link, the SQLBites post URLs in the channel drafts, and the six screenshots

Outside the engine; not verifiable here. The link register marks `shapedata.ai` destinations
"live-unverified" and the SQLBites URL does not exist until publication. The screenshots'
manifest records them as real 0.9.1 output; the one caption detail checked here ("address
table's ten columns") matches (10 columns in the generated `address` table).

### W12. "Shape checks a profile against a contract. ... use those checks in CI." / "the documented defaults, or your own per column" (post)

`shape check`, `shape diff --policy|--column-threshold|--threshold|--ignore` and the README's
"documented default for each" support it. Qualifier only on the Action (W7).

### W13. Website R2: "Status, matching the engine README: profiling, contracts and drift are the supported surface; generation from a profile works and is being hardened."

Matches the 0.9.1 README banner exactly ("Early access 0.9.1. Profiling, contracts and drift
are available and supported. Generation from a profile is available and is being hardened.").
Qualifier: the launch checklist still says "the public engine README banner still says
generation is in progress"; on `release/0.9.1` it no longer does. The checklist item can be
closed.

---

## ✅ True at 0.9.1 (with the file or command that shows it)

| Claim (post unless noted) | Evidence |
|---|---|
| Title/intro: "Profile. Generate. Test." describes the surface | `shape --help`: `profile`, `generate`, `check`, `diff` |
| "December brings in nearly three times February's orders" (and X draft "2.88x", LinkedIn "702 orders; February has 244") | `shape generate retail --scale small --seed 42`, then `SELECT strftime(order_date,'%m'), count(*) FROM "order"`: December 702, February 244 (ratio 2.877) |
| "a fifth of your customers place most of them" | same data: the top 20% of ordering customers place 58.6% of the 5,000 orders (59.8% of customers counted over all 1,000, as the claims log did) |
| "portable `.shape` file" you can "inspect ... keep alongside your code, check it against a contract and compare it with the next capture" | `shape inspect`, `shape cat`, `shape git-setup`, `shape check`, `shape diff` (`docs/CLI.md`) |
| "Open the report" (`--html`) | `shape profile customers.csv -o customers.shape --name customers --html report.html` ran, exit 0, `report.html` written |
| Screenshot 2 caption: month weights "December (index 11) carries 0.1404 of orders, February (index 1) 0.0488" | `shape cat retail.shape | grep order_date.temporal_histogram.month_weights`: `[1]: 0.0488`, `[11]: 0.1404` |
| Screenshot 3 caption: "Nine files in, eight relationships found" and "Candidates to review, not rules to accept" | `shape profile demo/ --dataset -o retail.shape`: 8 `relationships[*]` entries (address→customer, order→customer/store/promotion, order_line→order/product/promotion, return→order), each with an `evidence` field |
| "Shape can identify a change" in a null rate | `shape diff` kinds include `null_rate_change` (`docs/DRIFT.md`; the W9-12 CI log lists every kind) |
| "After `shape git-setup`, Git shows a `.shape` file as one readable line per property" | ran: writes `*.shape diff=shape` to `.gitattributes` and `diff.shape.textconv = shape cat` to git config; `shape cat` prints one `path: value` line per property |
| "Re-profiling unchanged data gives a byte-identical file, so `git status` stays clean" | ran `shape profile customers.csv` twice to two files; `cmp` reports identical |
| "Pick a domain, a scale and a seed. Or point it at a profile of your own data." | `shape generate retail --scale small --seed 42 ...` and `shape generate --from customers.shape --seed 42 --format parquet -o synthetic/` both ran, exit 0 |
| "There are 14 domains: retail, capital markets, education, financial, healthcare, HR, insurance, IoT, manufacturing, marketing, Pulse (ride-hailing), real estate, supply chain and telecom" | `shape list` prints exactly those 14; `shape describe pulse` says "Pulse rideshare" |
| "Shape's `plan` command describes what profile-based generation preserves, approximates and does not model" | `shape plan customers.shape`: `{"counts": {"approximate": 34, "not_modelled": 12, "preserved": 25}, ...}` |
| "I'll publish ratios when the equivalence verifier has signed off on each workload" (no speed claim made) | `docs/PERFORMANCE.md`: "No verified measurement is published yet"; §6.4 of the plan (equivalence before timing) |
| "Shape defaults to a safe capture. Sensitive columns retain statistics and formats rather than raw values, and category values are restricted by minimum counts." | `README.md` "What a `.shape` file contains"; `docs/PRIVACY_MODEL.md` (k default 5, `--k`, `--column-k`); `shape cat` shows `manifest.capture.mode: "safe"`, `manifest.capture.k: 5` |
| "A safe capture is data minimization, not anonymization." | `docs/PRIVACY_MODEL.md` first paragraph, `README.md` |
| "Full capture is an explicit choice." | `--capture full` prints `shape: warning: --capture full keeps real values ...; do not commit or share it` |
| "The safe check scans the artifact for prohibited content." | `shape profile validate --safe` (ran; see W10) |
| "run `shape cat` on the file and read exactly what's in it" | ran |
| "Fabric was the first stack Shape learned" / Fabric support | `plugins/shape-fabric`: `onelake` and `semantic-model` sources; `sqlserver`, `warehouse`, `synapse` sinks; `eventstream`, `eventhouse` emitters; `fabric`, `publish`, `notebook`, `deploy-notebook`, `setup-fabric`, `export-model`, `profile-model`, `known-answer`, `check-answers`, `publish-report` commands |
| "The dbt integration works with project files, contracts, results and seeds; it does not run dbt itself." | `docs/DBT.md`: `from-dbt`, `to-dbt-tests`, `dbt-seeds`, `dbt-report`; "never imports or runs dbt" (page status: experimental) |
| "Load the Parquet files into DuckDB, a Fabric Lakehouse, Power BI, or anything that reads Parquet." | `demo/` holds nine `.parquet` files plus `_shape_provenance.json`; generic Parquet claim |
| Screenshot 6 caption: "writing 21,750 rows in nine tables to a DuckDB file, then a DuckDB query listing the nine tables and their row counts" | `Wrote 21,750 rows in 9 tables to duckdb:///data/retail.duckdb`; `information_schema.tables` lists 9 (with F4's `mkdir`) |
| "In 0.9.1 you can profile, read the report, check contracts and drift, and generate domain data." | README banner; every command ran here |
| "Works, and is being hardened: generation from a Shape Profile. ... selected statistics do not reproduce every dependency in the source." | README banner; `shape plan` output marks `dataset.dependencies`, `missingness_joint`, `row_order` as `not_modelled` |
| "Shape is open source under the MIT license." | `LICENSE`, `README.md` badge, `pyproject.toml` `license = "MIT"` (D-15) |
| "Industry Profile Packs ... are coming in Shape Premium." (future tense) | Not an engine claim; consistent with `MESSAGING_AND_PRODUCT_STATUS.md` |
| "Please don't paste production data in the comments." | Policy, not a product claim |
| Website R2: "profiles a table or a folder of tables" | `README.md`: a folder is one table (partitions) unless `--dataset`, which profiles one table per file |
| Website R2: "generates synthetic data from 14 built-in domains or from a profile" | as above |
| Website R3: "files (CSV, Parquet, JSONL, Delta)" | `docs/SOURCES.md` (plus JSON, XML, IPC, Excel; see F5's fuller list) |
| Website R3: "OneLake and ADLS Gen2" | `abfss` source and sink, `delta+abfss://` (`docs/SINKS.md`, `docs/plugins/cloud-sources.md`) |
| Website R3: "SQL Server, Azure SQL and Fabric SQL", "Fabric Warehouse", "Synapse dedicated pools" | `plugins/shape-sqlserver` (`mssql` source, `profile-db`), `plugins/shape-fabric` sinks `sqlserver`, `warehouse`, `synapse` |
| Website R3: "DuckDB" (write and read) | `duckdb` sink in `shape-databases`; `duckdb` source in `shape-integrations` (two different URI forms, `docs/KNOWN_LIMITATIONS.md`) |
| Website R3: "Kafka and Azure Event Hubs (stream profiling)" | `shape stream-profile` (experimental) and the `kafka`, `eventhubs` stream sources and emitters |
| Website R3: "dbt (project files, tests, seeds and reports; doesn't run dbt)" | `docs/DBT.md` |
| Supported platforms (if the site states them) | `docs/INSTALL.md`: Python 3.11–3.14; `.github/workflows/ci.yml` matrix: ubuntu, macOS, Windows |

---

## What was run (reproducible)

```bash
# build from the release commit (runbooks/live/README.md lines)
git worktree add rel091 origin/release/0.9.1 && cd rel091
python3 -m venv ../venv091 && . ../venv091/bin/activate && pip install -U pip wheel setuptools
python scripts/build_pure_wheel.py --out ../wheels
pip wheel --no-deps -w ../wheels plugins/shape-domains plugins/shape-databases plugins/shape-sqlserver \
    plugins/shape-fabric plugins/shape-kafka plugins/shape-eventhubs plugins/shape-dbt plugins/shape-integrations
pip install --find-links ../wheels "sqllocks-shape[domains,pandas]==0.9.1" "sqllocks-shape-databases[duckdb]==0.9.1"
shape --version                                   # shape 0.9.1
# the post's commands, in an empty directory holding the two evidence CSVs
shape generate retail --scale small --seed 42 --format parquet -o demo/          # exit 0, 21,750 rows, 9 tables
shape generate retail --scale small --seed 42 --to duckdb:///data/retail.duckdb  # exit 2 (F4); exit 0 after mkdir -p data
shape profile customers.csv -o customers.shape --name customers --html report.html   # exit 0
shape profile customers_next.csv -o customers_next.shape --name customers            # exit 0
shape diff customers.shape customers_next.shape --fail-on-drift                      # exit 0, drifted: false (W9)
shape generate --from customers.shape --seed 42 --format parquet -o synthetic/       # exit 0, 50 rows
shape plan customers.shape; shape profile validate --safe customers.shape            # exit 0, CLEAN
shape profile customers.csv -o full.shape --name customers --capture full && shape profile validate --safe full.shape  # exit 1
shape cat customers.shape; git init && shape git-setup                               # textconv shape cat
shape profile demo/ --dataset -o retail.shape --name retail --html retail.html       # 8 relationships, month weights
shape profile validate --safe retail.shape                                           # exit 1 (W10, KNOWN_LIMITATIONS)
shape list; shape describe pulse; shape stream-profile --help; shape contract --help
pip install --find-links ../wheels sqllocks-shape-integrations==0.9.1 sqllocks-shape-sqlserver==0.9.1
shape profile 'duckdb://data/retail.duckdb?table=order' -o order.shape               # exit 0 (W2)
shape profile-db --help                                                              # mssql (W2)
pip index versions sqllocks-shape sqllocks-shape-domains sqllocks-shape-databases    # 0.9.0 only; plugins absent (F1)
```

## Items for the lead (not marketing)

1. `docs/databases/index.md` and `docs/databases/postgresql.md`, `mysql.md` claim PostgreSQL and
   MySQL reads through the integrations plugin; no such source exists at 0.9.1 (F5). One-line
   doc fix on `release/0.9.1` or `main`, or land W9-01 and reword.
2. `shape generate --to duckdb:///dir/file.duckdb` does not create `dir/` (F4). A one-line
   `mkdir(parents=True, exist_ok=True)` in `plugins/shape-databases/src/shape_databases/duckdb_sink.py`
   would match the file sinks, which create their output folder. Not changed here (documents only).
3. The launch checklist's "README banner still says generation is in progress" is stale on
   `release/0.9.1` (W13).
