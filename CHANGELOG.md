# Changelog

Status: available.

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

## 0.9.1 — [Owner: release date]

- `shape share-bundle verify` bounds every member of a bundle before reading it (#684): at most
  10,000 members, 8 MiB for `attestation.json` and `manifest.json`, 2 GiB per data file and 8 GiB
  in total, and no member or total that inflates more than 100 times once past 16 MiB (the Excel
  reader's zip-bomb guard). The limits are checked against the declared sizes and again while
  inflating, so a header that understates a size is caught too. A bundle over a limit exits 2
  naming the member and the limit and leaves nothing extracted; before, a 40 KB bundle could
  inflate to more than a gigabyte. See `docs/SHARE_BUNDLE.md`.
- `shape-databases`: a `credential` option (Microsoft Entra sign-in to Azure Database for
  PostgreSQL or MySQL) now asks for a token for `https://ossrdbms-aad.database.windows.net/.default`,
  the scope Microsoft documents for both servers. The default was
  `https://ossrh-postgresql.azure.com/.default`, which Entra does not know, so `credential=` failed
  unless `token_scope` was also given (#339). `token_scope` still overrides the default, for
  example for the US Gov or China clouds.
- The leak scan (`shape registry ROOT commit`, `shape profile validate --safe`) no longer reads a
  whole value that is a valid ISO 8601 date or timestamp, such as a datetime column's minimum or
  maximum (`2026-07-08 00:01:07`), as a phone number. Daily profiles of a timestamp column were
  refused when the hour made the digits look like a phone number (F-12). A phone number anywhere
  else, including after a timestamp in the same value, is still refused.
- Release engineering (P8-04, `docs/RELEASE_CHECKLIST.md`): `scripts/check_versions.py` checks
  that every place a release version is written agrees (core and plugin versions, every
  first-party `==` pin, `__version__`, the kernel's `Cargo.toml` and `Cargo.lock`; `--tag`,
  `--expect`, `--release`), and `scripts/set_version.py` changes them all at once.
  `scripts/build_release_dist.py` builds the pure wheel, the core sdist and every plugin's wheel
  and sdist, and checks a full release set (every T-04 platform wheel). `scripts/release_sbom.py`
  writes a CycloneDX 1.6 SBOM per archive (with the kernel's Rust crates for the archives that
  carry it), `SHA256SUMS`, and the resolved SBOM of `sqllocks-shape[all]`.
  `scripts/release_index_check.py` checks that an index serves exactly the built files, and
  `scripts/release_smoke.py` installs `sqllocks-shape[all]` from TestPyPI, PyPI or a directory
  and runs the smoke suite. `scripts/check_release_workflows.py` enforces the release
  workflows' trusted publishing, SBOM and attestation rules. The packaging lockstep test now also
  checks the `sqllocks-shape-healthcare-*` pins, which its pattern had missed.
- Generated values are the same bits on every CPU and platform (W8-04b, #768), so W1-15's pinned
  fixtures (#92) and W8-04's byte-identical files (#567) hold on Linux, macOS and Windows alike.
  numpy's float64 `log`, `exp`, `log1p`, `power` and `cos` round differently on AVX-512 CPUs and
  between the C libraries of each platform; every logarithm, exponential, power and cosine that
  makes a generated value is now computed with `shape.kernel.pmath` (fdlibm's algorithms with IEEE
  basic operations only; native kernel functions `pm_log`, `pm_exp`, `pm_pow` and
  `pm_cos_turns`, with numpy twins that give the same bits; `docs/GENERATION_KERNEL.md`,
  "Portable math"). This covers the `normal` draw itself (`philox_normal`, which now agrees
  between the kernels bit for bit), every continuous distribution family, the tables of the
  discrete ones, `empirical`'s interpolation, fan-out weights, skewed foreign keys and lead times.
  The distributions are unchanged; the last bits of some values changed once: the pinned ids of
  `distribution`, `normal` and the `beta`, `exponential`, `gamma`, `log_normal`, `normal`,
  `pareto`, `power_law_cutoff` and `weibull` families, 77 files of the golden byte corpus, and the
  hr and healthcare domain outputs, and the vault test's profile-fitted CSV digest
  (the implementation tests lists old and new ids). Those ids already differed between
  CI's machines before; now they are the same on Linux, macOS (arm64) and Windows.
  `formula` expressions keep numpy's `np_log`, `np_exp` and float powers (they equal numpy's own
  evaluation), and specs fitted to a profile are not covered (`docs/GENERATION_STABILITY.md`).

- `shape pack run` (`PackRunner.run`) refuses a domain name that is a path (a separator, `..`, a
  drive, `:` or another name `is_safe_name` refuses) with `unsafe domain name '<name>': it must be
  a plain name, not a path`, before anything is generated or written, not even the output
  directory; it no longer turns such a name into a safe run id (lead decision E2, #281). A scale
  name keeps the earlier behaviour: its part of the run id is made plain. The registry's
  raw-profile check (`is_raw_profile`, `profile_capture`) refuses a `.shape` container whose
  manifest no Shape reader accepts, for example one above the manifest size limit, with
  `RegistryError: not a Shape container: ...`, without inflating it (lead decision E1, #283, #396).

- Registry pruning (#566, `docs/REGISTRY.md` "Pruning"): `shape registry ROOT prune --before DATE
  [--name NAME ...] [--keep-last N] [--dry-run] [--json]` and `LocalRegistry.prune(before, *,
  names=None, keep_last=1, dry_run=False)` remove log entries committed strictly before the cutoff
  and then the objects no remaining entry, ref or tag of any name points at. Every entry a ref
  (`latest`, promoted refs) or tag points at, and the newest `keep_last` entries of each name, are
  kept; files under `objects/` that are not content ids are left alone and reported. The report
  (`format` `shape-registry-prune`, `version` 1) gives the entries removed and kept per name and why,
  the objects removed and the bytes freed (under `--json`, the `payload` of the `shape-result`
  document); `--dry-run` changes nothing. Logs are replaced atomically
  and objects deleted only after every log, so an interrupted prune leaves a working registry and a
  second prune finishes it. A prune holds `prune.lock`; commits, `tag` and `promote` refuse to run
  while it is held, and a prune waits for commits already writing. Exit 0 done, 2 bad input.
  Pruning cannot be undone.

- Test suite (AUD-tests): tests no longer depend on the order they run in (shared Spark session
  and JVM, shared generation case schemas, a cached test module, the process-wide `sys.modules`
  and `PYSPARK_PYTHON`; #77, #328, #329, #330, #335); the Python-kernel Spark executor test now
  runs the Python kernel (#328); the suite passes on pyarrow 19.0.1 (#333); a benchmark-harness
  test no longer writes `$BENCH_OUT_DIR` (#332); only the delta-fallback tests that read through
  DuckDB need its downloaded extension (#331). New tests cover `shape.quality.evaluate`,
  `shape.privacy.assess_summary`, contract save/load, the GeoNames and Gazetteer loaders and
  `DatetimeProfile` merge.

- Test coverage counts code the tests run in a child Python process (#337): `[tool.coverage.run]
  patch = ["subprocess"]`, and the `dev` extra requires `coverage>=7.10`. `shape.quality.evaluate`
  fails a range rule whose observed value cannot be compared with the bound, instead of raising
  `TypeError`.

- Changed (#721): the Fabric User Data Functions withhold the raw values of classified columns by
  default, as the bridge's `profile`, `check` and `diff` do. In the results of
  `profileLakehouseFile`, `profileLakehouseTable` and `profileDataFrame` a column the safe-profile
  gate classifies (`pii_gate_fires`: a personal-data pattern such as email or SSN, or nearly every
  value distinct) has `"min": null, "max": null, "redacted": true`; the violations of
  `checkProfile` and the changes of `diffProfiles` about such a column have `observed` (or
  `baseline` and `current`) withheld the same way, in the result and in the raised error. A new
  optional parameter `includeRawValues` (default `false`; `include_raw_values` in
  `shape.integrations.fabric.udf`) returns the raw values. Unclassified columns, existing calls
  and the `.shape` file written to `outputPath` are unchanged.

- One switch for realistic identifiers (W8-06, #766, `docs/GENERATION_STRATEGIES.md`). Reserved
  identifiers stay the default; `shape.generate(..., identifiers="realistic")`, `--identifiers
  realistic` on `shape generate` (with `--from` and `--scale-mode` too), `shape composite`, `shape
  demo run` and `shape pack run`, a scenario spec's `scenario.identifiers`, the bridge `generate`
  command's optional `identifiers` argument (1.2) and a schema's top-level `"identifiers"` turn
  realistic e-mail addresses, URIs, phone numbers and SSNs on for a whole run. A column's own
  `domains` or `range` wins, then the run switch, then the schema, then `reserved`; any other value
  is refused. A realistic run says so once on standard error, and `pack run` records the mode in the
  run manifest (`reproducibility.identifiers`; a manifest without it reads and replays as
  `reserved`). The same seed and mode give the same values in both kernels and in the multiprocess
  and Spark workers; the default output is unchanged. The `faker` package's e-mail, URL, host and
  phone providers (the `faker` strategy) are reserved by default too: `faker` is at generator
  version 2, and a spec pinned at `faker` 1 keeps the package's values (`docs/GENERATION_STABILITY.md`).

- `shape seed --mode append` refuses, before anything is written, a generated primary key that is
  already in its table (exit 1, the table and the key named; use `--mode truncate` to replace the
  rows). It used to write the tables before the clashing one and fail on the database's key
  (`Duplicate entry '1' for key 'customer.PRIMARY'` on MySQL). The shape-databases sinks gain
  `keys_sql`.

- A floating column declared `integer` (the DDL makes it `BIGINT`) reaches the PostgreSQL and
  MySQL sinks as int64, and a value that is not whole is refused with the column named
  (PostgreSQL's COPY refused `3.0` for `order_line.quantity` in `shape seed`).

- The `sql` writer's `format_version` is 2 (it was 1): a PostgreSQL literal with a backslash is now
  an `E'...'` literal (#285), which changes the bytes of such a script for the same input
  (`docs/GENERATION_STABILITY.md`); the golden byte corpus is rewritten.

- Security fixes (SEC-high, the high-severity findings open at Gate G7): SQL scripts cannot gain a
  statement from data: in the contract and design DDL a table, schema or column name with a control
  character is refused and a T-SQL text value with a line break is written as
  `N'a' + NCHAR(10) + N'b'`; the SQL sink keeps the T-SQL rule above (a name with a line break is
  refused, a value is split only at a `GO` line); a PostgreSQL literal with a backslash is an
  `E'...'` literal that reads the same whatever `standard_conforming_strings` says (#724, #285); the
  single-table Excel sink stores `=...` and `#N/A` text as text, drops characters XML cannot hold
  and makes the sheet name valid (#629); the Fabric API, OneLake and Eventhouse transports follow a
  redirect only on the request's own origin, so the bearer token never reaches another host (#275);
  an `abfss://` URI gets a storage credential only on an Azure Storage or OneLake host (#276); the
  bridge withholds the values, `message` and `detail` of a `diff` change or `check` violation about
  a classified column, joint-analysis entries included, and leaves the actual extreme of a
  classified column out of `verify` messages (#533, #535; a column that is not classified keeps the
  frozen, documented 1.1 message text); the demo comparison page shows no value
  of a classified column (#663); the shape-fabric tape scrubber redacts quoted, braced and JSON
  secrets, and a tape that holds a secret is refused on load as well as on save (#411); `release_for`
  suppresses small cells inside the `joint` block when nothing is above the target (#650); and a
  safe-profile column with fewer non-null rows than its `k` releases no value statistic, which
  `shape profile validate --safe` reports as `small-cohort-statistic` (#395); the safe-profile
  parity harness names that difference and checks it on both sides (the column is below `k` by the
  raw counts, the product holds null, the baseline's `mean` and `std` are the raw profile's).

- **`shape diff` on the terminal is bounded; `--json FILE` writes the file only (#308).** On a
  terminal, a change's `baseline`/`current` list or mapping of more than 20 entries (a
  `new_categorical_values` change of a column with thousands of values) prints its first 20, with
  the count left out under `values_omitted` and a line on stderr; a pipe still gets the complete
  result. The demo's day-1/day-2 orders diff shows about 2 KB on a terminal instead of 1.1 MB.
  `--json FILE` writes the complete result to the file and prints nothing on standard output (it
  used to print the same document as well); `--json -` and the `ci.json` of `shape.yml` still
  carry the complete document. Capture diffs with `--json OUT` also write only the file. A diff's
  `notes` are also printed on stderr (`shape: note: ...`). See `docs/CLI.md` and `docs/DRIFT.md`.

- **`[faker]` extra (#309).** Learned schemas use fake-data providers, so generating from one can
  need `faker`. It is the core's new `[faker]` extra (also in `[all]` and in the pure wheel's
  extras), the domains plugin depends on it (the `healthcare` demo inference now works in a clean
  install from the wheels), and the error without it names `pip install 'sqllocks-shape[faker]'`.

- **The Fabric plugin's Event Hubs and SQL Server parts are extras (#310).**
  `sqllocks-shape-fabric` now needs only the core; `[sqlserver]` brings `sqllocks-shape-sqlserver`
  (and `pyodbc`) for the SQL database, Warehouse, Synapse and SQL Server targets, and
  `[eventhubs]` brings `sqllocks-shape-eventhubs` for the Eventstream emitter and writer. Without
  them every entry point still loads, and the part that needs one fails with the line to install
  it. `pip install sqllocks-shape-fabric` alone no longer brings either plugin: ask for
  `'sqllocks-shape-fabric[sqlserver,eventhubs]'` to keep the old set (the `shape demo notebook`
  install cell does). `docs/INSTALL.md` and `docs/DEMO.md` give the install from the release's
  wheels (`--find-links`), since the plugins are not on PyPI.

- Container image (PF-05): the image is now under the 500 MB limit measured uncompressed (about
  471 MB, from 523 MB; 168 MB compressed). The build stage installs the wheel with `[azure]` into
  a prefix, strips its shared objects (`strip --strip-unneeded`, not the auditwheel `*.libs`
  folders) and restores the library symlinks pip unpacks as copies; the runtime stage copies only
  that prefix and fails the build if any shared object no longer loads. `scripts/image_size.py`
  measures an image's uncompressed size from `docker save`, independent of the image store
  (`docker image inspect .Size` is the compressed size under the containerd store).

- Fabric platform inventory (`docs/FABRIC_PLATFORM.md`, W7-01): one table of every Fabric and OneLake
  REST path, item type, Spark runtime and storage endpoint that Shape calls, with its release stage,
  Microsoft Learn source and check date. `tests/docs/test_fabric_platform.py` (guard in
  `scripts/fabric_platform.py`, also runnable by hand) fails when the code builds an unlisted Fabric
  path or item type, or one listed preview or retired, and when a doc or plugin README names an
  unlisted Fabric runtime or one past its end of support. New live check
  `plugins/shape-fabric/tests/test_live_git_sync.py` (Fabric Git sync with a `shape/` folder of
  `.shape` files; needs the O-02 secrets, not yet run) and the `shape-live-check` result format
  (`shape_fabric.livecheck`, format version 1), which declares `format`, `version`, `shape_version`
  and `min_shape_version` and is read through `shape.compat.check_readable` (state and
  compatibility policy).

- Security fixes: the Fabric clients (`shape.scale` Spark router, the shape-fabric plugin's Items
  API) follow a `Location` or `continuationUri` only on the Fabric API host, so the bearer token
  is never sent elsewhere (#631, #434); `shape plugins` signatures refuse a RECORD path with a
  line break, which could make two file lists share one signed message (#579);
  `shape share-bundle verify` refuses a data member whose extension is not lower case, which the
  dataset id did not cover (#683); `redact_sensitive` removes every value-bearing key of a
  classified column, not only `topk` and `examples` (#394); `release_for` withholds the `joint`
  block when a column is classified above the target, and treats `placeholders` as values (#650).
  `bandit -r src -ll` is clean again: the DDL constraint-name suffix marks its SHA-1 as
  `usedforsecurity=False` (names are unchanged).

- Documentation site (`mkdocs.yml`, `pip install -e ".[docs]"`, `mkdocs build --strict`): a
  navigation over every page in `docs/`, a home page, a CLI reference generated from the `shape`
  parser and a performance page generated from the committed benchmark results (a workload's time
  is shown only after its equivalence verifier passed). Links to files outside `docs/` point at the
  repository on the site. `scripts/check_doc_links.py` checks every link in the sources and, with
  `--site`, every link and anchor of the built site, offline; `scripts/check_user_facing.py --site`
  also scans the built site.

- Byte-identical files (`docs/GENERATION_STABILITY.md`, "Byte-identical files"): for the same spec,
  seed, scale, writer options and Shape version, the CSV, TSV, JSON Lines and SQL (every dialect)
  writers give identical bytes on Linux, Windows and macOS, in both kernel modes, whatever the
  locale, time zone, `PYTHONHASHSEED` or thread count. Fixes: the JSON Lines writer wrote `\r\n`
  line ends on Windows (its file was opened without `newline="\n"`), and so did the
  `_shape_provenance.json` sidecar; Linux and macOS bytes are unchanged, and the existing golden
  writer files and pinned dataset ids are unchanged. The `csv`, `tsv`, `jsonl` and `sql` sinks
  declare `format_version = 1`; the run manifest records the versions it used in
  `reproducibility.writers` (additive; the manifest version stays 1);
  `shape.generation.output.writer_format_versions` lists them. New
  `shape.repro.file_hashes(run_dir)`: the SHA-256 of every file a run wrote. The reference pools
  are frozen by `src/shape/builtins/strategies/pools/MANIFEST.json` (`format:
  "shape-pool-manifest"`, version 1; `python -m shape.builtins.strategies.pool_manifest [--write]`):
  changing a pool needs the generator version of every strategy that draws from it raised in the
  same change. The golden byte corpus `tests/generation/golden_bytes/` (`format:
  "shape-golden-bytes"`, version 1; `python scripts/golden_bytes.py [--update]`) holds the hash of
  every file written for every built-in strategy, distribution, `native` provider and column type,
  checked in both kernel modes and under a non-UTF-8 locale and `TZ=Pacific/Chatham`.
  `.gitattributes` keeps a checkout from changing the line ends of the pools and the corpus.

- Healthcare code sets (`sqllocks-shape-healthcare-codes`): two risk-adjustment tables built from
  the CMS "2027 Initial Model Software" with provenance in the catalog and
  `THIRD_PARTY_NOTICES.md`: `hcc_hierarchy` (`model`, `hcc`, `drops`, in the published order)
  and `hcc_coefficients` (`model`, `segment`, `variable`, `coefficient` kept as the published
  decimal text, `payment_years`, `software`) for CMS-HCC V28 and V22, ESRD V24 and V21 and RxHCC
  V08 (its three calibrations). `shape healthcare-codes fetch hcc_hierarchy hcc_coefficients`
  builds them; `byo` reads the CMS zip or a delimited file. Both declare `format` and an
  integer `version` (1); a newer version is refused. `risk_score` (`shape_healthcare_codes.risk.score`)
  is a documented reference score (mapping, hierarchies, coefficients) for testing generated
  data, not a certified implementation (`docs/plugins/healthcare-codes.md`).

- Healthcare standards writers: the `tables` companion-table option is documented as a stable
  option of `x12-837p`, `x12-837i`, `x12-835`, `x12-834`, `fhir-ndjson`, `fhir-bundle`, `omop`,
  `ncpdp` and the `fhir` emitter (`docs/plugins/healthcare-standards.md`, `docs/plugins/stability.md`):
  name, accepted types, precedence and `ContractError` are fixed within 1.x. New
  `shape_healthcare_standards.companion_tables(sink_name)` returns the required and optional
  contract tables of each writer; a contract test pins the behaviour with committed golden
  outputs. The 834 writer now raises `ContractError` naming `eligibility` when it is missing
  (it wrote no file before); nothing any writer emits changes.

- Profile depth III and rule strength (`docs/PROFILING_NOTES.md`, `docs/CONTRACTS.md`, #226). With the
  opt-in univariate depth (`shape profile --univariate`), each numeric column with at least 200
  finite values gains `mixture` (Gaussian mixtures with 1 to 4
  components fitted by EM from a deterministic quantile start, chosen by BIC: `k`, `components`,
  `bic_by_k`, `multimodal`), and, for a table with a date or timestamp column (`shape profile
  --time-column COL`, `shape.profile(..., time_column=)`), `seasonality` (mean per day or hour,
  candidate periods 24, 7, 12 and 52 with at least three full periods, autocorrelation and the
  strength of a moving-average decomposition; `seasonal` at strength 0.6). Identical under both
  kernels; both are left out of the share-safe profile. `shape diff` reports `mixture_change` and
  `seasonality_change` (thresholds `mixture_weight` and `seasonality_strength`). Contract v1 gains
  the optional key `strength` (`hard`, `soft`, `learned`) on a column's rules, `row_count` and each
  `fd`, `implies` and `reference_pair` entry: a broken `soft` rule is a warning, `learned` is a
  warning unless `shape check --enforce-learned`, `--strict` fails on every broken rule, and
  `CheckResult` and `--json` carry `strength` on violations and a `warnings` list. A contract with
  no `strength` gives the result it always did; profiles and contracts written before this change
  load, display, diff and check as before.

- Chaos, fidelity tiers and the HTML report (audit lane AUD-chaos): `orphan_keys` never writes a
  key a parent row has, also without a declared reference (#397), and handles narrow integers,
  infinite floats and non-key types (#399); `negative_amounts` refuses unsigned columns (#401); a
  corruption that fits nothing it is aimed at is an error (#403); input errors name the seed, batch
  or option, and `duplicates` refuses a column, since it copies whole rows of a table (#408);
  `read_ground_truth` checks `log_version`
  and names a malformed line (#410); out-of-range anomalies and value chaos no longer overflow on
  huge or infinite floats (#406); `ChaosConfig.validate` lists bad weights, seeds and override
  categories (#414); anomaly report details always carry the same keys (#418); CHAOS.md says
  `future_date` applies to `date32` (#421); PSI drift fails closed on infinite values (#404); the
  tiers read tables with repeated column names (#423) and refuse timestamps outside the nanosecond
  range (#555); `bootstrap_table` names a negative `n_rows` (#427); report bar charts stay valid
  SVG for NaN or negative shares (#430).

- Fixed (streaming audit, #153 to #165): `shape stream-profile` writes complete windows after
  Ctrl-C and a resume (windows Ctrl-C closed early are marked `"partial": true` and replaced), cuts
  a half-written last line of `--windows` on restart, and reads a CSV whose column changes type
  after its first rows (the misfits are rejected rows); a numeric event time outside the timestamp
  range no longer ends a stream read; `TumblingWindow` and `AggregateTumblingWindow` place events in
  the window that holds them for sub-second sizes; the default anomaly mutator changes only the
  value it targets (nulls, large integers and the int64 limit kept); `KeyedSketches` starts a fresh
  sketch for a key that returns after its TTL; `Deduplicator` accepts integer keys after an empty
  or all-null batch; `deduplicate_ids` keeps `1` and `"1"` apart; the emit file sink leaves the
  file as it was when a write fails part way; `FaultSink` does not resend a delivered batch when a
  duplicate copy fails; checkpoint and snapshot counters cannot set other attributes; the platinum
  batch helpers skip missing coordinates and keys.
- Fixed (streaming audit, #753, #754): a window profiler resumed from a snapshot taken before
  per-partition watermarks keeps its watermark when the partitions become known, so the events
  after the resume that belong to closed windows are counted late instead of being lost;
  `shape stream-profile --max-partition-skew` applies to a run resumed from `--checkpoint`.

- Fixed: abfss sources and sinks accept only Azure storage and OneLake hosts before a credential is
  attached (#276); abfss JSONL is depth-checked before it is parsed (#280); a T-SQL script refuses
  a name with a line break and writes a string value that would put `GO` at the start of a line
  with `NCHAR`/`CHAR` line breaks, so neither can start a batch (#285, #724; the PostgreSQL
  backslash handling is still open); the faker strategy calls only provider
  methods and bounds size arguments, and a too-deeply nested formula is a `ValueError` (#287); file
  sinks write a temporary file and replace the target, so a planted hardlink is not written
  through (#297).

- `temporal` with `pattern: "seasonal"`: the probability of the (month, weekday) buckets a range has
  no day in is spread over every day of the range, the end day included (the end day used to get
  almost none of it); a `month`, `day_of_week` or `hour_of_day` profile that is not a mapping of
  names to numbers is an error naming the column instead of an `AttributeError` (#219).
- `shape demo run <scenario> --mode streaming` streams the first table that has an event time, so
  its events are in event-time order and carry `_shape_event_time`; it used to stream the first
  table even when that table had no date or timestamp column (#312, in part).

- Streaming checkpoints: compressed state fields are inflated within a bound taken from the state's
  declared size, and an oversized or damaged field is refused as a corrupt checkpoint (#298).
- `shape stream --json`: a run whose events fit in one batch no longer reports `elapsed` and `rate`
  as 0.0 (#473).

- Security (#294): the PostgreSQL and MySQL sinks verify TLS by default for a host that is not
  loopback (`sslmode=verify-full`; MySQL with the system CA store or `ssl_ca`), matching the SQL
  Server sink. `localhost`, `127.0.0.0/8`, `::1` and Unix sockets are unchanged. Opt out only in the
  URI: `sslmode=disable`/`prefer`, or `ssl=false`/`ssl-mode=DISABLED`. A TLS failure names the host
  and the opt-out.

- `shape export-model`: measure names are unique across the model, so the model deploys. A name
  that two measures would share (retail: `Total Unit Price` and `Avg Unit Price`, from
  `product.unit_price` and `order_line.unit_price`) is qualified with its table, for example
  `Total Unit Price (product)`; every other name is unchanged (#425).

- Pull request check, badge, plugin templates and notifications (W6-01, `docs/PR_BOT.md`,
  `docs/NOTIFICATIONS.md`). `uses: sqllocks/shape@<tag>` (`action.yml`) profiles and diffs every
  source of a project, posts the result as one pull request comment, writes it to the step summary
  and fails the job as `fail-on` says; its steps are hardened (actions pinned to a commit SHA,
  inputs only through `env:`, the token never echoed) and a self-test runs it on fixture projects.
  `shape ci comment RESULT.json...` renders the Markdown (escaped names, `--max-findings`, no data
  value) and `shape ci post-comment` creates or updates the one comment through the GitHub API
  (token from `GITHUB_TOKEN`, HTTPS only, no redirects, a 403 or 404 prints a notice and exits 0).
  `shape badge RESULT.json... -o badge.svg` writes a deterministic, self-contained SVG with the
  states passing, drift, failing and unknown (`docs/CI.md`). `shape plugins new NAME --group GROUP`
  creates a plugin package from a template for any of the 15 groups (`shape.behaviors` included), each passing the conformance
  kit (`docs/plugins/authoring.md`). `shape.yml` gets an optional `notifications:` list (an additive
  key of `shape-project` version 1) and `diff`, `check`, `verify` and `fidelity` take `--notify REF`:
  after the result is decided they POST a `shape-notification` document (names and counts, no data
  value; HMAC-SHA256 in `X-Shape-Signature-256` with a `secret`; HTTPS only, three attempts on a
  connection error or 5xx); a failed delivery is a warning and never changes the exit code.
  `shape notify test` sends a test notification to every target.

- Schema importers (`docs/IMPORTERS.md`, #82). `shape import-schema FILE -o OUT.gen.json` reads a
  JSON Schema (draft 2020-12 and 7), OpenAPI 3.0 and 3.1, Avro, Protobuf (`proto3`, parsed without
  `protoc`), Pydantic v2 (`--allow-import`, since it runs the named module) or TMDL source and
  writes a generation spec that validates against the published schema, choosing each column's
  generator from its type and constraints. `--report` writes a `shape-import-report` (version 1)
  of every element and what it became or why it was not imported; `--strict` exits 1 and writes no
  spec when anything was not imported; a malformed input exits 2 with the file, line and element.
  `shape design --mode star --tmdl DIR` (`shape.design.tmdl.write_tmdl`) writes a star or
  snowflake design as a TMDL folder with relationships, a date table and default measures (a row
  count per fact, a sum per additive measure), byte-identical for the same design.

- Environment parity and consumer contracts (`docs/PARITY.md`, `docs/CONSUMER_CONTRACTS.md`).
  `shape parity A B` shows that a reloaded environment still looks like production: the same
  tables, columns, types, keys and relationships, null rates and distributions within the
  `shape.yml` drift thresholds, and table sizes within `--row-tolerance` (or, with `--scaled`, in
  the same proportions). A and B may be data, profiles, exports or share-safe profiles; what an
  input cannot support is `not measured`, never a pass. Report `shape-parity-report` v1.
  Consumer contracts (`shape-consumer-contract` v1, JSON Schema shipped): a consuming team states
  the tables, columns and rules it depends on, and the producer's CI runs
  `shape contracts check-consumers PROFILE.shape` (report `shape-consumer-check` v1, with consumer
  and producer-column owners; `--baseline` marks the violations a change introduced).
  `shape contracts validate FILE` lists every problem with its key path. `shape init` creates
  `contracts/consumers/` and adds the check to its workflow example. `shape.yml` is unchanged.

- Reference packs and column validators (`docs/REFERENCE_PACKS.md`, W3-12, #105). A reference pack is a
  directory with `pack.json` (`format` `shape-reference-pack`, `version` 1, source, retrieved, license,
  attribution, transformation version, sensitivity and, per dataset, fields, rows and the SHA-256 of its
  Arrow IPC file; JSON Schema in `src/shape/schemas/reference-pack-v1.schema.json`). `load_dataset` finds the
  datasets of the packs in the search paths and of the shipped packs, so `reference_pair`,
  `--reference-pair` and the strategies that take a dataset name read them unchanged; a file that does not
  match its checksum is exit 2. `shape reference list|show` print what was found. Shipped: `us-zip-city` in
  `sqllocks-shape-domains` (GeoNames, CC BY 4.0; ZIP as five-character text, city, state, county),
  and in core `iso-3166-1`, `iso-639-1` (Unicode CLDR 48.2, Unicode License v3) and `iban-lengths`
  (schwifty, MIT). `iso-4217` and `iso-639` are not shipped (no redistribution licence could be confirmed);
  `scripts/build_reference_packs.py` builds them locally from your own copy of the list, and rebuilds and
  checks the shipped packs from their pinned sources. New contract column rule `valid_as`
  (`{"kind", "min_valid_rate"}`; kinds `iban`, `iso3166_alpha2`, `iso3166_alpha3`, `iso4217`, `iso639_1`,
  `us_zip`), `shape profile --validate COLUMN=KIND` and `shape.profile(..., validators=)`, which store
  `validators: {KIND: {checked, valid, valid_rate}}` on the column (counts and a rate, so the share-safe
  profile carries them). `shape.validation.iban` checks length and ISO 7064 MOD 97-10.

- Multivariate depth (`docs/JOINT.md`, #232). Opt-in: `shape profile --multivariate`,
  `shape.profile(..., multivariate=True)` (off by default for the benchmark gate; the two-column
  determinants are always computed). The `joint` entry of a table profile gains, where it
  applies: two-column determinants in `dependencies` (`"determinant": ["a", "b"]`, the fields of a
  single-column one, capped per budget by `max_fd_multi`; `shape diff` reports `dependency_broken`
  for them and the `fd` contract rule reads them), `multivariate_outliers` (robust Mahalanobis
  distances from a fixed-seed FAST-MCD, chi-square 0.999 cut), `pca` (loadings to 95% of the
  variance, effective dimension at 90%), `cohorts` (k-means++ with `k` from 2 to 8 by silhouette,
  stored from 0.25) and `copula` (the latent correlation matrix of a Gaussian copula over the
  numeric and categorical columns, a persisted block with `format` and `version`). `shape diff`
  reports `multivariate_outlier_rate_change`, `structure_change` and `cohort_shift` (thresholds
  `multivariate_outlier_rate`, `structure_angle`, `cohort_tvd`; each held to sampling noise).
  `shape generate --from PROFILE --mixed-copula` (and `shape plan`, `shape.generate(...,
  mixed_copula=True)`) links numeric and categorical columns by the copula, reordering each
  column's own generated values so every marginal stays exact; it is off by default and
  generation without it is unchanged. The entries are bounded by the joint analysis's sample and
  column limits, identical in both kernels, left out of the share-safe profile, and shown by
  `shape show` and in the `--html` report. A profile written before this change loads, displays,
  diffs and generates as before. Also fixed: `shape generate --from` wrote no file for a table the
  numeric copula reorders.

- Univariate depth (`docs/PROFILING_NOTES.md`, #103). Each numeric column of a profile gains, where
  it applies: `distribution_candidates` and `distribution_by_bic` (maximum-likelihood fits of the

- Sampling controls, recorded in the profile (W2-07). `shape profile --sample N|P% [--sample-method
  random|systematic|head] [--sample-seed S]` and `shape.profile(src, sample=, sample_method=,
  sample_seed=)` profile a seeded sample (default seed 42; the same rows in both kernel modes and for
  every source); nothing is sampled without `--sample`. Every table profile has `sampling` (method,
  seed, what was requested, `population_rows`, `sampled_rows`, the `internal` samples the statistics
  take of their own, and `adequacy` with its reason) and every column `adequacy` (values seen, the
  standard error of the null rate with the finite population correction, the smallest share seen with
  95% probability). A sampled `shape profile` prints a note to stderr; `shape show`, the summary and the
  HTML report show the record; `shape diff` notes profiles sampled differently (`notes`) and compares
  `row_count_change` on `population_rows`. A dataset sample keeps foreign key detection.
- Type inference confidence (W2-07). Every column has `type_inference`: the type, its source
  (`declared`, `inferred`, `option`, `identifier_rule`), the `confidence` (the share of values that
  parse as it) and the parse share of `integer`, `float`, `boolean`, `date` and `datetime`; a declared
  column also says what its values hold. `shape types PROFILE.shape [--contract] [--min-confidence]
  [--json]` (and `shape.types_report`) lists declared types that differ from the values, integer
  identifier suspects, low-confidence inferred types and contract types that differ, with the option
  that would change each. `shape proposals propose --kinds type` turns the findings into `type`
  proposals; accepted ones apply to `generate --from`, `plan` and `shape profile --decisions`
  (read as `--types`). Profiles and decision files written before this load and diff unchanged.

- **Value vault (W5-03).** An optional, separate, encrypted file of the values a safe capture
  withheld, for users who need the exact values back at generation time without `--capture full`.
  `shape vault keygen|inspect|verify|rekey`; `shape profile --vault OUT.shapevault --vault-policy
  POLICY.json --kek REF` and `shape.save(profile, path, vault=..., vault_policy=..., kek=...)`;
  `shape generate --from X.shape --vault VAULT --kek REF [--verify PUBKEY]` draws `categories`
  columns from the vaulted values and frequencies and bounds `extremes` columns by the raw minimum
  and maximum (`generation_mode: "shape+vault"`, one warning on stderr; `shape plan` and `--dry-run`
  mark the columns `vault`). AES-256-GCM envelope encryption through `cryptography` (a fresh data
  key per vault, the header as associated data, the key-encryption key from `env://` or `file://`
  and never on the command line); the profile manifest gains the additive `vault` field
  (`vault_id`, `sha256`), so `shape sign` covers the vault hash; formats `shape-vault` and
  `shape-vault-policy` (version 1) are in `shape.compat` and the time-capsule corpus.
  **Without `--vault`, generation output is byte-identical to before.** `shape git-setup` adds
  `*.shapevault` to `.gitignore`. See `docs/VAULT.md`.
- **Behaviour change: profiles are written safe by default (W1-11).** `shape profile -o OUT.shape`,
  its `--json` summary and `--html` report, `shape.save(profile, path)` and `shape profile
  registry save` now write the **safe capture**: a sensitive column (declared `CONFIDENTIAL` or
  higher with `--classify COLUMN=LEVEL`, or pattern-only by the safe profile's own rules) keeps
  statistics and formats only, and a category is kept only if every released category has at
  least `k` rows (`--k N`, default 5; `--column-k COLUMN=N`), the rest folding into `__OTHER__`.
  Before, these held real values (up to 500 per column, raw minimum and maximum). **To keep real
  values, ask for them: `--capture full` / `shape.save(profile, path, capture="full")`**; the file
  says so (`capture`), a warning goes to standard error, and `shape profile validate --safe`
  reports it (`full-capture`, exit 1); the default passes (exit 0). The in-memory profile that
  `shape.profile()` returns is unchanged. The profile artifact is version 2 (`capture`,
  `redaction_manifest`); version 1 files still load and read as full (`shape migrate` stamps
  them). `shape diff` lists comparisons a safe capture cannot make under `not_evaluable` (never
  drift); `shape check` reports a rule that needs left-out values as `not evaluable: COLUMN was
  captured safe ...` and exits 2; `shape generate --from` and `shape plan` mark such columns
  `approximate`. `shape registry` commits a safe capture without `--allow-raw`. The bridge's
  `profile` still writes a full capture. See `docs/PRIVACY_MODEL.md`.

- Streaming II (W2-09, #97), in progress. `shape emit` and `shape stream` take `--event-format
  json|avro|protobuf|json-schema` for `kafka://` targets: the schema is derived from the table's
  Arrow schema, registered with a Confluent-compatible registry (`--sink-config
  kafka.schema_registry_url=URL`, credentials as references, `kafka.subject_strategy`
  `topic|record|topic_record`) and messages are written in the Confluent wire format; the message key
  stays `<table>/<seq>`. Encoders are the extras `sqllocks-shape-kafka[avro]` and `[protobuf]`
  (`docs/EMIT.md`, the plugin README). `--dead-letter URI` routes an event the destination rejects
  for good, or that cannot be encoded in the chosen format, to any `--sink`/`--to` destination as
  a `shape-dead-letter` record (version 1) instead of stopping the run; `--max-dead-letter N` stops it
  (exit 1) past N; new `shape.streaming.emit.RejectedEvents`, an extended emitter contract, and
  `dead_lettered` counts by reason in the run report. `--arrivals constant|poisson`: Poisson
  arrivals with exponential gaps keyed by the seed and the event position (the same schedule on every
  run and after a resume), combined with `--burst` and `--max-rate`. `--ramp START:DURATION:FROM:TO` and `--daily-curve
  flat|business-hours|FILE` (the `shape-rate-curve` format, version 1): linear rate ramps and a
  24-hour multiplier following the wall clock (`--realtime`) or the event time (`--speed`).
  `--drift-plan PLAN.json` (with `--rows TABLE=N`, `--day-seconds S`): the days of a `generate-drift`
  plan emitted in order with a continuing `_shape_seq`, `kind: "drift"` answer-key records, and the
  plan's SHA-256 in the checkpoint fingerprint. `--dry-run` (`--json`: `shape-emit-plan`, version 1):
  prints the target, tables, destinations and their plugins, credential references, checkpoint
  state, rate schedule summary and drift plan days, opening nothing, writing nothing and sending
  nothing; `--progress/--no-progress`: a one-line progress report on standard error.

- CI outputs (W1-14, `docs/CI.md`, `docs/EXIT_CODES.md`). `--junit FILE` and `--sarif FILE` on
  `shape diff`, `check`, `verify`, `fidelity` and `profile validate --safe` write JUnit XML (one
  test case per evaluated check; an observe-mode gate is skipped; a planned change passes) and
  SARIF 2.1.0 (rules per drift kind or contract rule, `TABLE.COLUMN` logical locations, stable
  `shapeFinding/v1` fingerprints); messages never carry a value from the data. `shape.yml` gets an
  optional `ci:` block for the report paths. One exit-code registry, `shape.cli.exitcodes`,
  generates `docs/EXIT_CODES.md` (`python scripts/gen_exit_codes.py --check` runs in `make check`)
  and the end of every `--help`; no command's codes changed. `--json` on every core command prints
  one `shape-result` document (envelope keys `format`, `version`, `command`, `exit_code`; the
  command's own keys are kept; a list or text is under `payload` or `output`), and `--dry-run` on
  every command that writes prints a `shape-dry-run` plan of `write`, `create`, `delete` and `send`
  actions (secrets removed) without writing or opening a connection. Commands whose `--json` was
  a switch that printed a list now print it under `payload`; a coverage test walks the parser.

- `x12-277ca` sink in `sqllocks-shape-healthcare-standards` (W7-06, #143): X12 005010X214 claim
  acknowledgments (277CA) from a new `claim_acknowledgment` contract table, one interchange per
  acknowledgment date, with accepted and rejected totals that balance with the claim rows.
  The action code (`WQ` / `U`) is derived from the status category when empty. Documented in
  `docs/plugins/healthcare-standards.md`.

- Fixed (benchmark harness, AUD-perf): `run.py --only profile|generate|stream` keeps the other
  families' records in `results.json` instead of dropping them (#357); STREAM-PROF refuses a ratio
  when the replay and the batch profiled different row counts (#358); a failing timed baseline run
  in `run.py --full` leaves that record empty instead of ending the job, and waits for the load
  gate (#359); `measure_product.py` holds the benchmark lock and writes nothing when the output
  differs between runs or has the wrong rows (#360); `kernel_bench.py` compares the timed output
  itself with the reference twin (#361); the live-fidelity overhead and realtime timings hold the
  benchmark lock (#362); peak RSS is megabytes on macOS too (#363); `datasets.py` rejects unknown
  names and row counts below 1 (#364); `bench_cli.py` names `verify.py --cli` (#365).

- Plugins audit (AUD-dbplugins), with a regression test for each:
  - `shape-databases`: a password containing whitespace no longer leaks into write errors (#338);
    a bad URI parameter value names the parameter (#344).
  - `shape-sqlserver`: alias-type columns are read and profiled as their base type (#340);
    `profile-db --tables`/`--schema` that name nothing is an error, not an empty profile (#341);
    tables named like `factory` or `dimension` keep their own key (#342).
  - `shape-domains`: changing a returned retail definition no longer changes later loads (#343).
  - `shape-simulation`: the file drop's backfills and restatements of a table without a time
    column re-drop that partition's rows (#345); the financial simulator refuses an unbounded
    window (#413) and settles every transaction when there is no time column (#417); text times
    with `Z` or an offset are read (#422); clickstream refuses `bot_pages_per_session` below 1
    (#426); SCD2 refuses repeated or null keys, tracking the key or a version column, and rates
    outside [0, 1], and a rate of 0 changes nothing (#431); pattern settings that cannot work
    are refused with their name, exit 2 (#433); `StreamEmitter.emit(config=...)` sizes the
    replay window from that config (#435); operational-log error bursts stay inside the window
    (#437); IoT baseline alerts follow one per device per eight hours (#439); the workflow
    summary lists every entity when none moves (#441).

- Quality, validation, contracts and drift (AUD-quality audit): the utility gate reads
  sub-second timestamps (#457); `shape.drift.compare` paths name the row count (`rows`) and each
  joint change (`joint.zip -> city`) instead of `tables.None` (#461); `shape.check` refuses a
  non-number `row_count` or `max_null_rate`, a non-boolean `nullable`, `unique` or
  `allow_extra_columns`, and table rules beside `tables` in a dataset contract, with
  `ContractError` (#462, #463, #464); gate schema values are validated, never coerced
  (`"nullable": "false"`, `"primary_key": "id"`) (#465); `referential_integrity` checks composite
  keys as a whole (#466); a `no_future` entry or a `ranges` column that checks nothing is a warning
  (#467, #472); the chi-squared enum test matches integer and boolean columns (#468); the drift
  engine no longer divides by zero with `min_rows: 0`, refuses NaN thresholds and malformed
  policies with `ValueError`, names an empty document, and reads naive times as UTC (#469, #470,
  #471, #478); the verify configuration refuses NaN bounds, `min` above `max` and `start` after
  `end` (#474); `validate_rows` (`shape quality`) and `quality.evaluate` treat values that cannot
  be compared as violations (#475, #476); clearer contract errors (#477); `load_tables` skips
  sub-directories and matches upper-case extensions (#479); the verify report names the
  configured α (#480).

- Performance page (`docs/PERFORMANCE.md`), generated from the benchmark results by
  `scripts/gen_performance_page.py` (`--check` fails when the page is stale): a workload's numbers
  are published only when its equivalence verifier exited 0 (and its re-run on the timed output,
  where there is one); nothing is published from a modified tree. The nightly parity suite
  (the local workload tests) runs the full workload validation, now including Shape's generation
  of every baseline domain at medium and of retail at large, in shards; merges them; marks a night
  green only when every workload of the full suite is recorded with a passing verifier; and keeps
  the nightly history and the green streak.

- State and compatibility policy (`docs/specs/STATE_AND_COMPATIBILITY.md`): every persisted file
  declares `format`, an integer `version`, `shape_version` and `min_shape_version` (the old key
  names `format_version`, `schema_version` and `pack_version` are still read, and still written
  beside `version` in the 1.x series); every 1.x release reads every format version ever released;
  a file from a newer release fails naming the minimum Shape release that reads it; unknown
  optional fields are ignored on read and kept on rewrite; `SHAPE_STRICT_FORMATS=1` (or
  `shape.compat.strict_formats()`) is the strict reader; deprecations warn with
  `FormatDeprecationWarning` and are announced here under "Deprecated" (none today). Covers
  `.shape` artifacts, safe profiles, models, generation schemas and specs, scenario packs,
  registry layouts (`layout.json`, `_layout.json`), run manifests, contracts, signatures and
  profile exports. Run manifests, registry logs and receipts write UTC ISO 8601 times with `Z`.
- `shape migrate SRC DST` (and `shape-migrate`, `shape.migrate`): offline migration that never
  rewrites in place, keeps the original, records `migrated_from` and `source_content_id`, has a
  dry run, refuses downgrades, checks its result through the content id, and writes a receipt
  (signed with `--sign-key`; a signed source needs it). Language-neutral test vectors for the
  canonical forms and content ids (`docs/specs/vectors/state_vectors.json`) and a time-capsule
  corpus loaded in CI (`tests/timecapsule`).
- Reconciliation and time-series quality checks (`docs/VERIFY.md`): `shape verify --config` takes
  `reconcile` rules (row counts per table and partition, aggregates per key or group, with
  tolerances, both sides read through the source layer) and `timeseries` rules (gaps in a regular
  series, stuck values, daylight-saving missing and repeated local hours with an explicit time
  zone). The contract accepts the same optional rules and `shape check` takes `--data`;
  `shape.quality.reconcile` and `shape.quality.check_timeseries` are the Python API.
- Fixed (#223): the Eventhouse writer made a KQL table and wrote to it at once, so the engine answered `Entity ... of kind 'Table' was not found` and the write failed with 0 accepted requests; its first request to a table now waits (backoff, `ready_timeout`, default 120 s) for the table to be ready, as the emitter already did. A column the engine refuses (`BadRequest_EntityNameIsNotValid` for `say "hi"`: quoting cannot help, only letters, digits, `_`, space, `.` and `-` are valid in a Kusto column name) is now created under a valid name (other characters become `_`, collisions get a numeric suffix) while the ingestion mapping's JSON path keeps the event's own key. Details in the implementation tests.
- Fixed (#78): the SQL emulator test helper `rows_of` returned pyodbc `Row` objects, which no longer compare equal to tuples, so `test_awkward_names_cannot_break_out_of_their_quotes` failed in the Nightly `sqlserver-e2e` job. It now returns plain tuples; every assertion is unchanged. The Nightly `fabric-emit-e2e` job failed at install because `shape-fabric` requires `sqllocks-shape-sqlserver==0.9.0`, which the job did not install (workflow diff in the implementation tests).
- Fixed (#462, #463, #464, follow-up): `row_count` bounds must also be finite (an infinite bound was accepted); every table's contract in a dataset contract is validated before any table is evaluated, and its errors name the table (`table 'orders': unique for column 'id' must be true or false, not 'true'`); a `tables` that is not an object and a table name that is not a text are reported as such; `x_` extension keys beside `tables` are accepted again (they were refused as table rules).
- `semantic-model://` source and `shape profile-model` (`sqllocks-shape-fabric`, `docs/plugins/
  cloud-sources.md`, `docs/plugins/fabric-commands.md`). `shape profile semantic-model://<workspace>/
  <model>/<table>` reads a table of a Power BI / Fabric semantic model through `sempy` (options
  `columns`, `batch_rows`, `max_rows` as a DAX `TOPN`, `mode`), with a documented type map to Arrow;
  `shape profile-model WORKSPACE/MODEL -o OUT.shape [--tables] [--max-rows] [--json]` profiles every
  table and records the model's relationships as declared (`evidence: "declared"`, `active`,
  `type`). `sempy` is the plugin extra `semantic-link`, imported only when a semantic-model URI is
  opened. A `many_to_many` relationship in a profile is recorded and no longer becomes a generated
  foreign key or a proposal's profiler-detected key.
- Sinks II, part a (W2-08, #96): `shape generate --to` and `shape emit --to` write to Snowflake
  (`snowflake://user@account/database/schema?warehouse=WH&role=ROLE`: Parquet files `PUT` to the
  table stage, one `COPY INTO` whose loaded row count must equal the staged rows, staged files
  removed also on failure; key-pair or password sign-in) and to Databricks
  (`databricks://host/http_path?catalog=CAT&schema=SCH`: Delta tables in Unity Catalog through a SQL
  warehouse, batched bound multi-row `INSERT`; token or OAuth machine-to-machine sign-in). Both are
  in `plugins/shape-databases` (`shape_databases.SnowflakeSink`, `DatabricksSink`; drivers are the
  plugin extras `[snowflake]` and `[databricks]`, none in core), take the same `write_mode`,
  `schema_name`, `table_prefix`, `commit_rows`, `columns` and `primary_key` as the other database
  sinks, check identifier limits (255 characters, names the database would change) before any
  connection, refuse a secret in the URI and redact credentials in every message. Type maps and
  what a failure leaves behind are in the plugin README.
- Sinks II, part b (W2-08, #96): `shape generate --to synapse://<workspace>.sql.azuresynapse.net/<pool>`
  and `shape emit --to` write to a Synapse dedicated SQL pool (`plugins/shape-fabric`,
  `shape_fabric.SynapseSink` and `SynapseWriter`, `docs/plugins/fabric-writers.md`): the table is
  prepared as the Warehouse writer does, the rows are staged as Parquet under the required
  `staging_path` (an ADLS Gen2 `abfss://` folder), one `COPY INTO ... FILE_TYPE = 'PARQUET'` loads them
  with the managed identity or the signed-in identity (`copy_identity`), the loaded row count must equal
  the staged rows, and the staged files are deleted also when a step fails. Table options
  `distribution` (`ROUND_ROBIN`, `HASH(column)`, `REPLICATE`) and `index` (`CLUSTERED COLUMNSTORE INDEX`,
  `HEAP`). Sign-in is the plugin's `--auth` modes.
- Bridge API 1.2 (`docs/BRIDGE.md`, W7-05): the analysis commands of the open engine reach the JSON
  bridge, and the registry gives drift with severity between two share-safe versions. New commands:
  `report_card` and `report_card_read` (`shape report-card`; the card holds no value of the real
  data), `rules_mutate` (cancellable between mutants), `rules_backtest`, `proposals_contract` and the
  kind `rule` of `proposals_propose` (with `rule` and `stale` in `proposals_list`), `bisect`,
  `bisect_layers` and `timelapse` (the values of a classified column are withheld unless
  `options.include_raw_values`), `registry_diff` and `chaos` (the input check of `shape chaos`
  unchanged: an input not marked as Shape-generated is `policy.unverified_input` before anything is
  written, `allow_real_input` adds the warning `real_input_corrupted`; local output only), and
  `suite_list` and `suite_run` (`shape pack list --library` and `shape suite run`: per scenario the
  answer key's expectation, the observed outcome and `met`, and `passed`; cancellable between
  scenarios; local output only). New error
  codes `input.contract_conflict` and `policy.unverified_input`. `format_schema` names the formats of
  the new reports (`mutation-plan`, `mutation-report`, `incidents`, `backtest-report`). A request that
  declares `api_version` `1.0` or `1.1` is answered exactly as that version answers it (a 1.2
  command is `usage.unknown_command`, and the 1.2 values of an enumeration are refused as before);
  the 1.1 schemas and vectors are frozen in `docs/bridge/schema/1.1/` and
  `docs/bridge/vectors/1.1/`, and `tests/bridge/test_compat_1_1.py` replays them against the 1.2
  bridge. The job file `shape-bridge-job` stays at version 1.
- `shape registry ROOT diff NAME REF1 REF2` and `shape.registry.drift` (`docs/REGISTRY.md`): for two
  share-safe profiles the result has `drift` with the changes of `shape diff` (`kind`, `severity`,
  `score`) for every metric both safe forms hold, and `not_measured` for the metrics a safe form
  withholds (the range of a column, the outlier rate, ...). Two raw profiles give the result of
  before. `shape.rules.mutation_test` takes the optional `should_stop` and `on_mutant` (what a
  cancellable bridge job needs); a finished run is unchanged.
- Bridge API 1.1 (`docs/BRIDGE.md`, W7-04): the JSON bridge now reaches the workflows the command
  line has. New commands: `proposals_propose`, `proposals_list`, `proposals_decide` (proposals and
  decision files), `project_validate`, `project_show` and the arguments `project` and `source` on
  `profile`, `diff`, `check` and `verify` (the `shape.yml` project file; the bridge never looks for
  one on its own), `design` and `design_from_data` (schema design, read-only), `format_schema` (the
  JSON Schema of each format Shape reads), `profile_show` (a stored `.shape` profile without
  profiling again), `contract_validate` and `safe_scan` (the leak scanner; a finding's message never
  holds the value it found). `verify` gains `source` (the memorization and utility gates, as
  `shape verify --source`) and `details` on every gate (counts, rates, distances and scores, never a
  data value). Request schemas annotate every path argument (`x-path`: `read` or `write`) and
  `domain` (`x-name-or-path`); `index.json` lists each command's `effects` (`reads_files`,
  `writes_files`, `cancels`, `network`), the version that added it and each argument (`since`), and
  every warning code. New error codes: `input.unknown_proposal`, `input.unknown_source`,
  `input.unknown_format`. A request that declares `api_version` `1.0` is answered exactly as 1.0
  answered it (a 1.1 command is `usage.unknown_command`, a 1.1 argument `usage.unknown_argument`,
  and no 1.1 field is added); the 1.0 schemas and vectors are frozen in `docs/bridge/schema/1.0/` and
  `docs/bridge/vectors/1.0/` and `tests/bridge/test_compat_1_0.py` replays them against the 1.1
  bridge. The job file `shape-bridge-job` and the vector files `shape-bridge-vectors` stay at
  version 1.

- Rule mutation testing and backtesting (`docs/RULES_TESTING.md`, W3-01, #99). `shape rules mutate
  DATA CONTRACT.json` plants the corruptions of `shape chaos` one at a time (every applicable one, or a
  `shape-mutation-plan`), profiles each mutant in memory, checks it against the contract (and, with
  `--diff` or a `drift` section, compares it with the unmutated profile) and reports the mutation
  score overall, per corruption kind and per table, the surviving mutants and the rules that killed
  none; `--min-score` gates it. `shape rules backtest REGISTRY NAME CONTRACT.json` replays a contract
  over every committed version of a registry name by business date, with `--window week|month`
  through mergeable profiles, `not measured` for rules the stored form cannot evaluate, `--incidents`
  (caught, missed, alarms outside incidents, `--fail-on-miss`) and `--compare OLD_CONTRACT.json`.
  Python API `shape.rules.mutation_test` and `shape.rules.backtest`; JSON Schemas for the plan, the
  incidents file and both reports (formats `shape-mutation-plan`, `shape-mutation-report`,
  `shape-incidents`, `shape-backtest-report`, version 1).
- Rule suggestion from a profile (W3-02, `docs/PROPOSALS.md#rules`). `shape proposals propose
  PROFILE.shape [PROFILE.shape ...] --kinds rule` (and `shape.proposals.propose_rules`) proposes
  contract v1 rules from what a profile measured: per column `dtype`, `nullable`, `unique`,
  `range`, `pattern`, `allowed_values` (up to 20 values) and `no_placeholder`; per table a
  `row_count` band, `fd` (confidence 0.99 or more) and `reference_pair`. Each has the exact
  contract fragment as its claim, the profile figures as evidence and a documented confidence that
  rises with the support and the number of profiles; a rule is proposed only when it holds on
  every profile given and never from fewer than 30 non-null values. Personal-data columns get
  value-free rules only. `shape proposals contract -d DECISIONS.json -o CONTRACT.json [--merge
  EXISTING.json]` (and `DecisionFile.to_contract`) writes the accepted rules as a contract that
  `shape check` reads. The decision file is version 2 (`decisions-v2.schema.json`) when it holds a
  rule proposal and version 1, byte for byte as before, otherwise; version 2 adds the status
  `stale` for an accepted rule whose evidence no longer holds (`shape proposals list --status
  stale`). `propose` kinds default to `relationship,pii,semantic` as before.
- `shape contract emit CONTRACT.json --to ddl|jsonschema|pandera|gx` (`docs/CONTRACT_EMIT.md`, W5-04):
  a v1 contract as `CREATE TABLE` DDL for the SQL sink's four dialects (Fabric Warehouse constraints
  `NOT ENFORCED`), a draft 2020-12 JSON Schema for a row, a pandera schema as generated Python text,
  or a Great Expectations 1.x suite. Output is byte-identical for the same contract. Every rule a
  target cannot state is listed in `not_expressed` (`--strict` exits 1 and writes nothing; `--json`
  prints the `shape-contract-emit` version 1 result) and kept as metadata (`x-shape`, pandera
  `metadata`, GX `meta`); `contract_from` rebuilds the expressible part (or all of it with
  `use_meta=True`) from a JSON Schema or a suite. New sample `examples/contracts/orders.contract.json`.
  `shape.schemacheck` now understands `maximum`, `pattern` and `not`.
- Plugin allow-list and opt-in signature check (W1-18, #94; `docs/plugins/trust-model.md`).
  `SHAPE_PLUGIN_ALLOWLIST=PATH` (or `plugins.allowlist` in a `shape.yml` passed to `PluginHost`)
  turns on a `shape-plugin-allowlist` v1 file (JSON or YAML, `src/shape/schemas/plugin-allowlist-v1.schema.json`):
  `PluginHost` blocks, before importing, any plugin whose distribution, version (PEP 440
  specifier) or name is not listed (`status: blocked`; the CLI exits 2), checks the installed
  `RECORD` hash and every file hash when `record_sha256` is given, and with `require_signature`
  needs a `shape-plugin.sig` (Ed25519, `shape-plugin-signature` v1) by a trusted key. New:
  `shape plugins sign`, `verify` and `allowlist init`; `shape plugins list` shows
  allowed/blocked and `doctor` lists blocked plugins separately. The trust model now states what
  each first-party plugin can reach (checked against the source by a test). There is still no
  sandbox (D-09).
- `shape bisect`, `shape bisect layers` and `shape timelapse` (`docs/HISTORY.md`, #101): git bisect
  for data. `bisect REGISTRY NAME --good REF --bad REF` binary-searches the committed versions of a
  name (ordered by `business_date`) for the first one whose `shape diff` against the good version
  reports a change (under the `shape.yml` source's thresholds and ignore lists; `--column`,
  `--kind`, or `--contract FILE` to test "the contract fails"), testing at most `ceil(log2(n)) + 2`
  versions, and reports the first bad version, the last good one and the changes between them.
  `--verify-all` tests every version and reports any that flips back; `--coarse week|month`
  bisects over windows merged with `merge_profiles` first. `bisect layers` names the first layer of
  a pipeline (sources of `shape.yml`, `--map` for renamed columns) where a change between two dates
  appears, and where it persists or disappears (exit 0, 1 when none, 2). `timelapse` gives one
  frame per version or merged window (rows, null rate, distinct estimate, quantiles, mean, std, top
  values), marks the change points where `shape diff` reports a change, and writes JSON
  (`shape-timelapse` v1), text sparklines, or one self-contained offline HTML page. Python:
  `shape.versions.bisect`, `bisect_layers`, `timelapse`.
- `shape report-card REAL SYNTHETIC` (`docs/REPORT_CARD.md`, #102): one local report card for a
  synthetic dataset. Three sections, each `pass`, `fail` or `not_run` with its reason: fidelity (the
  `shape fidelity` scores and tiers 1 and 2), utility (the `shape verify --source` utility gate) and
  privacy (the memorization gate and a new membership-inference test: with `--holdout`, the AUC of
  telling real rows from held-out real rows by the distance to the closest synthetic row, failing
  above `privacy.max_membership_auc`, default 0.6). Output as `shape-report-card` v1 JSON, Markdown
  or a self-contained HTML page (`-o`, by extension); `--require` turns a skipped section into exit 1;
  exit 2 for unusable input. The verify configuration gains an optional `privacy` section. Python:
  `shape.quality.report_card(...)`. The card holds no value of the data.
- Chaos safety and confirmation for non-local targets (W1-17). **Behaviour change for scripts that
  write to remote targets:** `shape generate --to`, `shape emit`/`stream` (`--sink URI`, `--to`) and
  `shape generate --scale-mode --sink` to a non-local target (a database, OneLake, Event Hubs,
  Kafka; not a path, `file://`, `console` or `localhost`/`127.0.0.1`/`::1`) now exit 2 unless you
  pass `--yes` or set `SHAPE_CONFIRM_REMOTE=1` (or answer `y` at a terminal prompt); `--dry-run`
  needs none (`docs/SINKS.md`). `shape.cli.to.run_to` and the emit target setup take
  `confirm_remote`. `shape generate -o`, `continue`, `time-travel`, `pack run` and `chaos` write
  `_shape_provenance.json` (`format: shape-provenance`, `version: 1`) beside the tables, which are
  unchanged; folder readers skip it and `_SUCCESS`. `shape chaos --input DIR` now refuses table
  files that are not listed there with a matching sha256 (or Parquet with the `shape_synthetic`
  marker) unless `--allow-real-input`, logs `input_provenance` (`verified`/`unverified`) in the
  ground-truth `run` record, and refuses an `-o` that is the input folder or inside it
  (`docs/CHAOS.md`, `docs/THREAT_MODEL.md`).

- VS Code extension and data dictionary (W6-04, #87). `editors/vscode/` (TypeScript, MIT) validates
  and completes `shape.yml` (the bundled `shape-project-v1.schema.json`, through the Red Hat YAML
  extension), offers snippets, shows a `.shape` file as the read-only text of `shape cat` and
  diffs it against Git HEAD (`shape.path` setting; no telemetry, no network). CI builds, tests
  and packages a `.vsix`; marketplace publishing stays a manual step (`docs/RELEASE_POLICY.md`).
  `shape dictionary PROFILE.shape [--project FILE] [--source NAME] --format md|html|json -o OUT`
  writes a data dictionary (`shape-data-dictionary`, version 1) with owners and annotations from
  `shape.yml`; example and top values only with `--examples` and never for columns classified
  CONFIDENTIAL or higher (`docs/DICTIONARY.md`). `jsonschema` joins the `dev` extra.
- File-footer fingerprint, safe-to-share bundle and skew rehearsal (W5-10, #86).
  `shape fingerprint embed|show|verify` and `shape generate --fingerprint` store a signed
  `shape-fingerprint` document (synthetic marker, table and run ids, reproducibility tuple, optional
  Ed25519 signature) as the Parquet footer key `shape.fingerprint` and the Delta table property
  `shape.fingerprint`; `verify` recomputes the table digest (`docs/FINGERPRINT.md`, with a table of
  which local tools keep it, generated by the test run). `shape share-bundle create|verify` runs the
  memorization gate and a top-values check against the source and writes a zip with a signed
  `shape-share-attestation` only if both pass (`docs/SHARE_BUNDLE.md`: evidence that checks passed,
  not a privacy guarantee). `shape skew-rehearsal` generates a schema at scale with the key
  concentration a profile measured and writes a `shape-skew-report` comparing top shares against a
  tolerance (`docs/SCALE.md`). The Parquet and Delta sinks take `fingerprint=True`.
- CSV identifier columns keep their text (#46): an integer column of digits with leading zeros, or of
  one fixed width of five or more digits under an identifier name (`zip`, `npi`, `ndc`, `member_id`),
  is read as text by `shape.profile`, `shape.io` and the commands built on them, so `00000` is not 0.
  `shape profile` has `--string-columns`, `--types FILE.json` and `--infer-types off`
  (`shape.profile(string_columns=, types=, infer_types=)`); an integer column that only looks like an
  identifier is reported as a warning. `learn` and `generate --from` build fixed-width digit text
  (`{digits:N}` pattern token) and keep numeric-looking labels as text.
- Sinks check the URI scheme before writing (#42): a cloud or database URI gives a message that says
  what the sink writes and which sinks handle which scheme, instead of an `OSError` (or a directory
  called `abfss:`). `shape generate -f` accepts every installed `shape.sinks` plugin, `ipc` included (#40).
- `shape stream-profile` keeps the watermark per partition (#41): partitions read at different speeds
  lose no events with the default `--allowed-lateness 0s`. New `--max-partition-skew` (10m) and
  `--partition-idle-timeout` (30 s, followed reads); late events are reported with the
  `--allowed-lateness` that would have kept them.
- `generate --from PROFILE` (and any schema with correlated columns) wrote no files while reporting
  success; the table is now handed to the writer after the correlation pass.
- Stable Python modules (`docs/API_STABILITY.md`, "Stable Python modules", W7-07). `shape.generation.spec_edit`
  (`SpecDocument`, `SpecProblem`, `SpecError`, `validate_text`, `Position`) and
  `shape.generation.spec_schema` (`build_schema`, `published_schema`, `render`, `strategy_names`) are promoted
  to Stable: each has an explicit `__all__`, nothing in them is removed or changed incompatibly in 1.x, and a
  deprecated member keeps working with a `DeprecationWarning` until the next major version. The promise
  covers signatures, dataclass fields, exception bases and four documented behaviours (unchanged specs are
  written back byte for byte, edits keep unknown keys and key order, every `SpecProblem` has a JSON Pointer and
  a line and column for text, `published_schema()` is the shipped file). `python scripts/stable_api_compat.py
  --check` compares the modules with `tests/api/stable_api_baseline.json` (`--write` refreshes it after an
  additive change) and runs in `make check`.

- Fairness slices and training-serving skew (`docs/FAIRNESS_AND_SKEW.md`, W3-11, #104).
  `shape scorecard --slice-by COLUMN[,COLUMN] [--min-slice-rows N] [--max-slice-gap G]
  [--reference REF] [--label COLUMN]` scores every dimension per slice with the gap and worst slice,
  each slice's share of rows (and reference share and ratio), the positive rate and disparity ratio
  of a label (flagged below 0.8, a screening heuristic), and null rates; slices under the minimum are
  pooled and never shown alone, classified slice columns show `slice 1`, `slice 2`, ...
  `--max-slice-gap` exits 1 when exceeded. A scorecard with `slices` is `shape-scorecard` version 2
  (JSON Schemas for versions 1 and 2 in `src/shape/schemas/`); one without is still version 1, byte
  for byte; trends compare slice gaps. New `shape skew TRAIN SERVING [--features] [--label]
  [--slice-by] [--threshold KEY=VALUE] [--project] [-o REPORT.json] [--json]`
  and `shape.quality.skew(...)`: schema, null-rate and PSI skew (the PSI of `shape drift --psi`),
  unseen categories and out-of-range values per feature, ranked by PSI, per slice with `--slice-by`,
  on data or profiles (`shape-skew-report` version 1).

- SQL Server write path II and a DuckDB writer (`docs/SINKS.md`, `docs/plugins/fabric-writers.md`,
  `docs/plugins/fabric-auth.md`, W2-10 / #98). `--auth kerberos --keytab REF --principal NAME@REALM`
  signs in to a SQL Server that takes Windows authentication only: `kinit -k -t` runs into a private
  credential cache (a keytab is `file://PATH`, mode 600, or `kv://VAULT/NAME` holding it
  base64-encoded), the connection uses `Trusted_Connection=yes`, `KRB5CCNAME` is set only while a
  connection opens, and the cache is removed when the command ends, also after an error. A
  generation-schema column may carry `"identity": true` (an optional key of the version-1 document;
  `integer` type and `sequence` strategy only); `shape from-ddl` writes it for `IDENTITY`, `SERIAL`
  and `AUTO_INCREMENT`; the SQL Server writer creates `BIGINT IDENTITY(start, step)` and by default
  keeps the generated keys with `SET IDENTITY_INSERT` (`identity=server` lets the server number
  the rows and is refused when a foreign key references the column); the `sql` script sink emits the
  same for the `tsql` dialect. `--sql-constraints disable` loads into existing tables with
  `NOCHECK CONSTRAINT ALL`, then validates (exit 1 naming each constraint left disabled);
  `truncate` of a referenced table uses `DELETE`. `--write-mode upsert` merges each batch into the
  table on its primary key, so a rerun leaves the same rows and finishes a killed load. A new
  `duckdb` sink (`duckdb:///PATH.duckdb[?schema=main]`, extra `sqllocks-shape[duckdb]`) writes Arrow
  batches into a DuckDB file with `create`, `append`, `truncate`, `replace` and `upsert`. A run no
  longer waits forever when a sink fails after its last batch.

- Semantic versioning of diffs (`docs/DRIFT.md`, "Change classes", W1-13). Every drift kind is
  breaking, additive or cosmetic (`shape.drift.semver.classify`); each change in `shape.diff` and
  `shape diff --json` carries `class` and `class_reason`, and the result adds
  `semver: {bump, breaking, additive, cosmetic}` (major, minor, patch or none; planned changes
  counted apart under `semver.planned`). `shape diff --fail-on breaking|additive|cosmetic`,
  `--version-from X.Y.Z` and `shape.diff(..., fail_on=...)` build on it. A widening `dtype_change`
  carries `detail.widening` and stays breaking unless the policy sets `dtype_widening`. The drift
  policy gains `classes` and `column_classes`, `shape.yml` sources gain `classes`, a planned-change
  entry gains an optional `class`, and gates that compare with a baseline gain `fail_on` (the
  schema drift gate uses the classifier; default `breaking`, as before). Optional keys of the
  existing version-1 formats: older files read as before.
- Planned-change registry (`docs/PLANNED_CHANGES.md`, #90). `shape-changes.yml` (format
  `shape-planned-changes`, version 1, JSON Schema included) lists changes you expect, with a
  window and a reason. `shape diff`, `shape check` and `shape verify` read it (`shape.yml` key
  `changes`, `--changes FILE`, `--no-changes`, `--on DATE`): a planned change inside its window is
  reported as planned and does not fail, a suppressed one is not reported, an expired entry stops
  matching and prints a warning. `shape.diff(..., planned=...)` and the `--json` result add
  `planned`, `planned_not_observed` and `expired`. New `shape changes validate|list|add|ack`.
- Nested and standards sources (`docs/SOURCES.md`, `docs/plugins/healthcare-standards.md`, #83). New
  `json` source (a `.json` file holding one document or an array of documents) and `xml` source, and a
  `flatten` option for `json` and `jsonl`: `flatten="struct"` keeps Arrow structs and lists,
  `flatten="tables"` turns each array into a child table `<parent>__<field>` with `_id`, `_parent_id` and
  `_ordinal` (nested objects become `address.city` columns), and the sources add
  `read_tables`, `read_relationships` and `read_nested` (relationships are
  `shape.generation.schema.Relationship` objects). The XML reader takes a `record` path
  (`//order`), reads attributes and child text into columns and repeated children into child tables, and
  refuses any document that declares entities (`UnsafeXml`). JSON depth is checked across the whole
  document before parsing (`shape.security.jsondepth.check_json_document`) and file size against
  `max_bytes`. Healthcare standards plugin: `x12` source (`x12://FILE`, `.x12`, `.edi`; every segment in
  `x12_segment`, envelope checks with `strict=False` reporting `x12_problems`, and `loops="tables"` for
  one table per 835 loop) and `hl7v2` source (`.hl7`, MLLP framing, escapes decoded; `hl7_segment`, and
  `segments="tables"` for one table per segment id). Structural only: no codes are interpreted.

- Behavior primitives (`docs/plugins/behavior.md`, section 11, #79): `event_sequence`,
  `telemetry_series`, `transaction_stream`, `file_arrival` and `entity_lifecycle` as parameterised,
  deterministic `shape.behaviors` modules (`shape_behavior.primitives` builders, registered by
  `sqllocks-shape-behavior`, passing the plugin conformance kit). `shape behave run NAME --params
  FILE.json` takes the new persisted format `shape-behavior-params` (version 1; a wrong format, a
  newer version, an unknown primitive or an unknown parameter exits 2 naming the key) and `run.json`
  records the parameters used. Same seed and parameters give byte-identical event files, also across
  a checkpoint resume; `telemetry_series` for 10,000 devices at a 1-hour interval for a year runs
  through `--window-years` in bounded memory.

- Univariate depth (`docs/PROFILING_NOTES.md`, #103). With `shape profile --univariate`
  (`shape.profile(..., univariate=True)`; off by default, since it adds work for every numeric
  column), each numeric column of a profile gains, where it applies: `distribution_candidates` and `distribution_by_bic` (maximum-likelihood fits of the
  normal, lognormal, exponential, uniform, gamma and Weibull with log-likelihood, AIC, BIC and KS;
  the existing `distribution`, `distribution_params` and `fit_score` are unchanged), `zero_share`
  and `zero_inflation` (observed against a Poisson and a moment-fitted negative binomial),
  `heaping` (multiples of 5, 10, 100 and 1,000 against the share expected from the range and
  resolution), `benford` (first-digit shares, MAD and Nigrini's class) and `tail_index` (the Hill
  estimator with its standard error). Identical under both kernels; computed on all values in
  chunks or on a deterministic sample of at most 50,000 (10,000 for model selection); not part of
  the share-safe profile. `shape diff` reports `zero_inflation_change`, `heaping_change`,
  `benford_change` and `tail_change` (thresholds `zero_share`, `heaping_ratio`,
  `benford_class_steps`, `tail_alpha_drop`, `tail_alpha_max`; each held to sampling noise).
  The `--html` report shows the fields and `shape show` prints them (`Profile.summary()` is
  unchanged). A profile written before this change loads, displays and diffs as before.

- Generator version pinning and a stability promise for pinned fixtures
  (`docs/GENERATION_STABILITY.md`, #92). Every built-in strategy and distribution declares an
  integer `generator_version` (all 1); a plugin may declare it too (optional, missing means 1). A
  generation spec can pin versions in a top-level `generators` map (added to the published JSON
  Schema, kept by `SpecDocument`): `shape generate`, `shape pack run` and `Engine` run a pinned
  name at its pinned version and the rest at the latest. `shape pin SPEC [-o OUT] [--json]` writes
  the current version of everything a spec uses; `shape pin SPEC --check` exits 1 listing the names
  that are not pinned. A pin to a version this Shape does not have exits 2; a pin to a name the
  spec does not use is a warning. The run manifest records `reproducibility.generators` and
  `shape pack replay` regenerates with them. `tests/generation/pinned/` holds a pinned spec per
  built-in strategy and distribution family with its committed dataset id for seed 42, checked in
  both kernel modes in the regular suite.

- OneLake targets in the `abfss` sink (`docs/SINKS.md`, #141). Before the first byte is written, a
  OneLake target is checked once and refused with one line that names the fix: a workspace or item
  name with a space (use the workspace and item IDs), an item that is neither `<name>.<ItemType>` nor
  a GUID, a path outside `<item>/Files/`, an item that does not exist (the storage API cannot create
  Fabric items), a Warehouse (written through T-SQL), and plain files under `Tables/`. ADLS Gen2
  hosts are unchanged and make no extra storage call.

- CLI stability promise, v1.0 definition of done and what is not built (W1-10, #88).
  `docs/CLI_STABILITY.md`: which commands are stable and which experimental (an experimental
  command's `--help` now starts with `(experimental)`), what is stable for a stable command, what
  counts as a breaking change and the deprecation process (a deprecated flag keeps working for at
  least one minor release, prints `shape: warning: --OLD is deprecated and will be removed in X.Y;
  use --NEW` and is listed here). Nothing is deprecated in 1.0. `scripts/cli_surface.py --check`
  (in `make check`) compares the parser with `tests/cli/cli_surface_v1.json`
  (`format: "shape-cli-surface"`, `version: 1`) and fails, naming it, when a stable command, flag
  or choice is removed or renamed; `docs/V1_DONE.md` lists what 1.0 requires for capture or
  profile, diff, gate and replay, each with the tests that prove it, and
  `scripts/check_v1_done.py` confirms those tests exist; `docs/NOT_BUILDING.md` lists decisions
  not to build things. `docs/specs/ONE_ZERO_CONTRACT.md` now states the exit codes the CLI uses
  (`docs/CLI.md`). No command, flag or exit code changed.

- `shape known-answer`, `shape check-answers` and `shape publish-report`, also under `shape fabric`
  (`docs/plugins/fabric-commands.md`, `docs/DRIFT_REPORT.md`). `known-answer DOMAIN|SCHEMA -o DIR`
  writes a dataset (Parquet per table), the semantic model of its schema, `answers.json` (format
  `shape-dax-answers`) with the exact expected value of every measure for the grand total and every
  slice (`Decimal` arithmetic; averages and ratios as exact fractions rounded half-even), and
  `queries.dax`; `--measures` (format `shape-dax-measures`) picks the measures and slices and
  `--plant TABLE.COLUMN=VALUE` sets a decimal column's total exactly inside its generator's bounds.
  `check-answers` compares a DAX client's CSV or JSON export with the answers (exit 0, 1, 2).
  `publish-report PROFILE.shape... | --registry NAME` diffs a profile history with the options of
  `shape diff` and writes `fact_drift_change`, `dim_run`, `dim_column`, `dim_kind` and `dim_date`,
  `drift.bim` with measures, and `report.json` (format `shape-drift-report`). The exporter takes an
  optional `measures` argument that replaces its default measures.

- Failure mode catalog, data detective packs, public dataset library, canaries and game days
  (W6-03, #235). `shape failure-modes list|show` print a catalog of 23 ways data goes wrong
  (`docs/FAILURE_MODES.md`, generated by `python scripts/gen_failure_modes.py`, which `make check`
  runs with `--check`), each with symptoms, causes, the checks that detect it (`drift:KIND`,
  `rule:RULE`, `gate:NAME`) and a library scenario that plants it; `shape suite run failure-modes`
  runs them all and fails when a check an entry names does not fire. Seventeen scenarios were added
  to the starter library, built on the chaos mutators, `DriftPlan` and new defect kinds. `shape
  detective list|start|hint|check` plays six packs that plant problems in generated data and checks
  your answer (`docs/DETECTIVE.md`). `shape library list|show|get` and `shape generate --from
  dataset:NAME` use safe profiles of six public datasets under CC0 or CC BY 4.0, credited in
  `THIRD_PARTY_NOTICES.md` and built by `scripts/build_dataset_library.py`, the only code that
  touches the network (`docs/DATASET_LIBRARY.md`). `shape canary make|check` writes a marked batch
  with planted failures and tells you which expected detections your own `--json` results lack
  (`docs/CANARIES.md`). `shape gameday run` plants failures in copies of your local data, runs the
  `profile`, `diff`, `check`, `verify` and `fidelity` checks you list and reports what was detected
  (`docs/GAMEDAY.md`). New persisted formats at version 1: `shape-failure-catalog`,
  `shape-detective-pack`, `shape-detective-answer`, `shape-dataset-library`, `shape-canary`,
  `shape-gameday` and `shape-gameday-report`. Fixed: `shape generate --from PROFILE` wrote no file
  for a table with correlated columns although it said it had.

- Starter scenarios, suites, a pytest plugin and database seeding (W5-05, #81). `DriftPlan` has a
  `rename_column` event (`{"kind": "rename_column", "column": "orders.status", "to":
  "order_status", "start": ...}`; the answer key records the dropped and added column as one
  rename). `shape pack list --library` and `shape pack run library:NAME` run ten starter scenarios
  on the retail domain (clean baseline, nulls, duplicates, orphaned foreign keys, late data, and
  add, rename, drop and retype of a column on a schedule), each against a written answer key.
  `shape suite run NAME|FILE` runs the `smoke` and `schema-evolution` suites (or your own) and exits
  1 when a scenario missed its key. The `pytest11` plugin (`pip install 'sqllocks-shape[pytest]'`)
  adds a `shape_dataset` fixture, a `shape_scenario` marker with fixture and `--shape-seed`.
  `shape seed SPEC --target URI` writes the tables in foreign-key order through the `sqlserver`,
  `postgres` and `mysql` sinks, or as ordered INSERT scripts with `sql://DIR`. New persisted
  formats `shape-scenario-expect`, `shape-scenario-library` and `shape-suite` (and
  `shape-scenario`) at version 1. `docs/SCENARIO_LIBRARY.md`, `docs/TESTING_WITH_SHAPE.md`.
- `shape bridge` (AUD-bridge): `diff` and `check` withhold the raw values of a classified column
  in joint-analysis entries too (`message`, `detail`, an `fd` violation's `observed`) (#533);
  `verify` withholds the actual minimum and maximum a range gate quotes (#535); concurrent
  `profile` jobs no longer share a scratch file and corrupt each other's artifact (#537); requests
  are read as UTF-8 whatever the locale, and a line that is not UTF-8 is `usage.invalid_json`
  (#539); a Fabric status the bridge has no name for keeps the job active (#542); a job file must
  be the job its name says, with fields of the right types (#544); a very long stream
  `interval_seconds` waits instead of failing (#545); the request size limit counts UTF-8 bytes of
  the request without its line end, result paths are absolute, NumPy values stay numbers,
  `Bridge.handle` never raises and finished job threads are let go (#546).
- `shape.generate` raises `TypeError` for an argument its form cannot use (`n` or `relationships`
  for a domain or schema, `mode` for anything but a domain, `scale` for an evidence document)
  instead of ignoring it (#251).
- `shape.LogicalType` refuses an unknown kind or bit width; an invalid type raises
  `ShapeTypeError`, which is now also a `ValueError` (#260).
- Contracts (`ShapeContract.from_dict`, `shape validate`) are checked against
  `shape-v1.schema.json` and refuse an unknown mandatory capability, as `docs/specs/SHAPE_1_0.md`
  requires (#383); capability lists must be lists of strings (#393).
- Schema design: a star or snowflake whose tables would share a name (an entity called `date`,
  names that differ only in case) is an error instead of one table replacing another (#384); 3NF
  keeps a self-referencing foreign key (#385); a decimal scale above its precision, a repeated
  attribute in a key, dependency or hierarchy, empty names and unknown `not_additive_over` names
  are refused (#386, #387, #390); `--from-data` gives decimals a precision that holds their scale,
  types integers mixed with decimals as decimal and names a row that is not a mapping (#386, #392).
- Decision files: `update` and `decide` refuse what the reader would refuse, so a written file
  always reads back (#388); impossible times such as `2026-02-30T12:00:00Z` are refused (#391);
  `apply_decisions` accepts a `.shape` path (#389).
- Migrating a v1 capture refuses two columns with one name (#393).
- Kafka and Event Hubs stream sources (#351, #353, #354, #355): a bounded read stops at the end
  each partition had when the read began (messages written during the read are left for the next
  run); Event Hubs `max_messages` counts each read on its own; an Event Hubs body that is not
  UTF-8 is counted as undecodable instead of ending the read.
- Event Hubs emitter (#356): an event too large for a batch is a `ShapeError` naming it, wherever it
  falls in the batch.
- Fabric tapes (#411, #442, #455): the scrubber redacts braced ODBC passwords holding `}`, JSON and
  quoted secrets; recording keeps the exception the code under test sees, records a failing
  commit, and gives real values from `fetchone`; a malformed tape is a `RecordingError`.
- Fabric SQL sinks (#415, #443, #446, #449, #456, #458): `sql-database://` and `warehouse://` build
  `Server=host,port` and refuse a user or password in the URI; `mssql://` honours the URI's write
  options with `connection_string`; declared string lengths and decimal scales always give valid
  T-SQL; `Encrypt=Strict` is kept; a zero-column table and a missing table in `order` are refused
  before anything is written; a long table name gets a primary-key name within 128 characters.
- Eventhouse (#419, #444, #445): emitting several tables into one KQL table re-sends each table's
  JSON mapping when another replaced it; each emit's token, credential, retries and timeout apply;
  `uint64` columns are `decimal`.
- OneLake paths and storage (#432, #440, #447, #454, #459): `.` and `..` segments are refused; the
  workspace is decoded and checked and URLs encode each segment once; one filesystem per storage
  account; landing-zone dates and hours are checked; local files get the usual file mode.
- Fabric API and sign-in (#434, #436, #438, #450, #451, #452, #453, #460): the token is sent only to
  the Fabric API; a cancelled operation fails at once and `Retry-After` is honoured; an unexpected
  answer is a `FabricApiError` (exit 1); paging stops on a repeated token; ids are quoted; the token
  is fetched for each request; an empty token is an `AuthError` and a fallback sign-in names both
  failures; `kv://` names cannot end in a newline.
- Fabric notebooks (#448): `generate_notebook` takes only a plain domain name, an integer seed and a
  version string into the code it writes.
- Privacy, security, artifacts and registries (AUD-privacy): `redact_sensitive` removes every
  value-bearing key, not only `topk` and `examples` (#394); the safe profile of a column with fewer
  non-null rows than `k` no longer publishes its mean, std, quantiles, bounds or distribution
  parameters (#395); the JSON depth guard skips brackets inside strings, so a crafted JSONL line
  cannot slip past it and crash the reader (#273); the registry's raw-profile check reads the
  manifest within the size limit (a container whose manifest is too large is refused with
  `RegistryError`, never inflated), does not crash on other odd input and recognises a raw profile with a
  byte-order mark or in UTF-16/32 (#283, #396); `profile validate --safe` fails closed when
  `tables` is not an object (#398); dates are no longer detected as phone numbers (#400); a
  private key named by a plain path is refused when others can read it, as with `file://` (#402);
  `redact_text` masks quoted, dict and prefixed keys (`sasl_password`, `api_key`, `account_key`),
  `Authorization: Basic` and whole URI passwords containing `@`, in linear time (#291); the secret
  scanner's Event Hubs pattern is linear (#292); `is_safe_name` refuses `:`, control characters,
  trailing dots and spaces and Windows device names (#243); signing keeps the artifact's file mode
  (#405); the reader refuses `content_hashes` naming `manifest.json` or `manifest.sig` (#407);
  damaged registry logs, refs, objects and profile-registry indexes give errors that say what to
  repair (#409, #420); non-finite numeric enum keys no longer crash the safe profile (#412);
  differential privacy takes the range over finite values (#416); `suppress_shape` withholds a
  column with an unknown count (#424); a malformed secure envelope is one `ShapeSecurityError`
  (#428); `shape profile safe|validate` print the not-signed notice as a `shape: note:` line (#109).
- Profiling engine audit (lane AUD-profile): zoned timestamps in every unit (#128), nanosecond timestamps keep their
  nanoseconds (#318) and enter the joint analysis (#151); the joint sample never repeats a row (#150) and integers
  past 2**53 stay in it (#151); CSV header names are made unique (#167); each fork pool reads its own columns (#216);
  impossible ISO dates, NaN words and zoned date text are handled as the baseline does or refused with the way out
  (#221, #224, #269); zoned instants that share a wall-clock time stay apart (#225); mixed pandas object columns and
  duplicate column names (#228, #229); integers wider than 64 bits are float (#236) and CSV integers of up to 76
  digits are read (#320); the date tokenizer is linear (#270); folders skip hidden and underscore folders, refuse a
  nested Delta table (#271) and files with no column in common, and compare columns as a set, JSONL included (#321);
  a file name with glob characters is read (#272); reference pairs skip NaN, read a `Path`, work on a workbook, and
  a malformed spec is refused before profiling (#302, #319, #325); NaN in the joint analysis (#313); the row evidence
  functions and temporal profiles (#315, #316); binary minimum and maximum are cut to 256 characters (#317); a
  workbook refuses the Delta and CSV options by name (#319); a trailing delimiter and a header-only CSV read as the
  baseline reads them (#320); the engine document is strict JSON when moments overflow (#322); clear errors for
  non-UTF-8 files, empty files, unknown time zones, `PROFILE_THREADS`, `quotechar`, an empty list and undecodable
  bytes, and `file://` and `~` paths are read; `PROFILE_THREADS=1` restores pyarrow's pools afterwards (#324);
  Cramer's V, dictionary columns with a null or repeated value, long durations, `Profile.tables` copies and the
  delimiter warning's location (#325); `infer_column_type` reads a zoned column's wall clock, and the joint analysis
  builds no view for a column it cannot use (#326).
- Scenario packs and `shape demo` (audit AUD-scenario): a pack run's manifest path is contained in
  `--output`: a domain name that is a path is refused, and the scale part of the run id is made
  plain (#281),
  and the landing folder is checked before it is created (#525); the `chaos` section of a pack or
  spec is validated (a wrong type is an error naming `chaos.<key>`, `enabled` must be true or
  false, an unknown key is warned about, and a spec run checks the spec's chaos) (#510); an unknown
  file format is warned about (it is written as CSV) (#513); a same-second run keeps its whole id
  in the `_x2` suffix (#514); a topic listed twice is an error (#515); a stream rate must be finite
  and positive, in hybrid packs too (#516); a list of names refuses `true`/`false` (#526); the run
  manifest reader refuses a bool or sub-1 version and names the file of invalid JSON (#519). An
  interrupted demo run saves its session so `shape demo cleanup` can remove what it wrote (#511);
  the comparison page and semantic model never overwrite a file (#521); streaming refuses more
  than one domain (#523); a new session never takes a saved session's id (#524); session and
  profile names refuse a line break (#517); a session record that is not a JSON object is a
  `DemoError` (#518); `demo_run` settings refuse a flag that is not a bool and a list that is not
  names (#520); `demo/build_benchmark_sheet.py` checks without `assert` and uses UTF-8 (#527).
- Command line and demo fixes (lane BUGS-cli-1). The crash line of an unexpected error is redacted like
  the expected ones (#278). `shape quality` reports a text value in a numeric column as a violation
  instead of crashing with `TypeError` (#152). `shape registry ROOT commit` runs the leak scan and the
  raw-profile check on a JSON document with a UTF-8 byte-order mark or in UTF-16, as `json.loads`
  reads it (#530). Fabric's `Deduped` job status maps to `cancelled`, so the job is final and
  resumable (#543). `shape conformance` no longer prints the signature notices of its own temporary
  artifacts (#311). `shape cat` takes `--verify PUBKEY`, which the note it printed already named, so a
  git textconv can check each file (#126). Two `shape demo init` at once no longer lose a connection
  profile: `connections.json` is written under a cross-platform file lock (#528).
- Integration plugins, `sqllocks-shape-integrations` (`docs/plugins/integrations.md`, W5-08): thin,
  optional adapters, one extra each, none imported until used (a missing library exits 2 with the
  pip command). `shape lineage emit MANIFEST --to URL|file://PATH` sends a run manifest to OpenLineage
  as `START` and `COMPLETE` or `FAIL` events with schema facets and a `shape` run facet (reproducibility
  tuple and dataset id); `shape mlflow log MANIFEST` logs params, gate metrics and artifacts to one
  MLflow run and refuses a second log of the same run id; the `presidio` detector labels text columns
  from a bounded, seeded sample without downloading a model; `shape evaluate sdmetrics|anonymeter`
  writes informational `shape-evaluation` reports (version 1; not gates); the `duckdb` source reads a
  table read-only through Arrow, and `shape_integrations.ibis.connect(DIR)` registers an output
  directory as Ibis views. Known limit: Anonymeter 1.1.0 pins `numpy<1.27`, so its extra cannot resolve
  next to Shape's `numpy>=2` (install note in the guide).

- Joint distributions and plausibility (`docs/JOINT.md`, #47). `shape profile` finds placeholder
  values (`00000`, `99999`, `1900-01-01`, `-1`, `N/A`, ...) with their share and evidence, and
  records approximate functional dependencies, two-column keys, association measures for every
  type pair (Pearson, Spearman, Kendall, Cramer's V, Theil's U, correlation ratio, mutual
  information), conditional probability tables and the share of implausible rows, on a bounded
  sample; `reference_pairs` / `--reference-pair` check that columns hold real combinations.
  `shape diff` reports `dependency_broken`, `placeholder_surge`, `implausible_rate_change`,
  `association_shift` and `reference_match_change`, naming the columns and the value. New optional
  contract rules `fd`, `implies`, `reference_pair`, `max_implausible_rate` and `no_placeholder`.
  Generation: hierarchical sampling (`hierarchy` and `hierarchy_field` strategies,
  `HierarchicalSampler`), categorical joint tables from a profile (`conditional_table`), a Chow-Liu
  joint model with per-row plausibility scores and a report of impossible combinations
  (`fit_joint`), and a joint fidelity check (`joint_fidelity`). The joint analysis is on by default
  for a single table and off for a dataset (several tables): `--joint` / `joint=True` turn it on,
  `--no-joint` / `joint=False` off, `SHAPE_PROFILE_JOINT` when the call does not choose.

- `shape demo init|list|run|preflight|cleanup|status|notebook|report` (`docs/DEMO.md`): four scenarios in three
  modes (inference, seeding, streaming); seeding writes to a folder, a Lakehouse, a Warehouse, a SQL database
  or an Eventhouse and records a session that `cleanup` removes exactly; the operations are plain functions
  (`shape.demo`) the JSON bridge calls too. A scenario runs its own domains, a failed run is rolled back,
  `preflight` checks each target, a profile never stores a secret and reports are escaped. Harness:
  the local workload tests (the fidelity report and the metadata exactly, the generated data by
  T-21, an allow-list with probes, negative controls).
- `shape fabric publish|notebook|deploy-notebook|setup|export-model` and the top-level `shape publish`,
  `shape notebook`, `shape deploy-notebook`, `shape setup-fabric`, `shape export-model`
  (`docs/plugins/fabric-commands.md`): publish a domain to a Lakehouse (landing zone and run manifest),
  Warehouse, SQL Database or Eventhouse; make and deploy a Fabric notebook; make a Fabric Environment;
  export a Power BI semantic model (`.bim`). Names that reach M and DAX are quoted, an accepted (202)
  creation is followed to its end, the workspace listing is read across pages, and the notebook part is
  named for its format. Harness: the local workload tests (the `.bim`, the
  notebook, the requests and the landing zone against the baseline, an allow-list with probes,
  negative controls).
- Sinks for OneLake, ADLS Gen2 and databases (`docs/SINKS.md`): `shape generate --to URI` and
  `shape emit/stream --to URI` (repeatable) write to `abfss://` (Parquet, CSV, TSV, JSONL, IPC in
  dated Hive-style folders, rolling files, atomic publish), `delta+abfss://` (a Delta commit per
  micro-batch), `mssql://` (SQL Server, Azure SQL, Fabric Warehouse), `postgresql://` (`COPY`) and
  `mysql://` (`sqllocks-shape-databases`). Local Parquet/CSV/JSONL and Delta sinks take
  `roll_rows`/`roll_seconds`/`commit_rows` so readers see rows while a stream runs. `shape emit`
  gains `--speed 60x` (virtual clock), `--max-rate`, `--duplicate-fraction`, `--poison-fraction`,
  `--answer-key` and the synthetic marker (`--synthetic-header`).
- `shape verify --source DATA`: the memorization gate (exact-match rate and nearest-neighbour
  distance between generated and source rows; fails on a reproduced row in a column classified
  `CONFIDENTIAL` or above, reporting row indices, never values) and the utility gate (train on
  generated data, test on held-out real data, fail below a minimum retention; needs the `[advanced]`
  extra). The verify configuration gains `classifications`, `memorization` and `utility`
  (`docs/VERIFY.md`).
- Run manifest: `format`, `version`, the reproducibility tuple (`reproducibility`) and a
  content-addressed `dataset_id`; `shape pack replay MANIFEST TARGET` regenerates a run and checks
  the id (`docs/REPRODUCIBILITY.md`).
- Air-gap hardening: every test that needs no network runs under a `zero_network` guard that
  fails any connection leaving the machine, and again in CI with networking disabled; pinned,
  hashed lock files for core and each extra (`scripts/offline_lock.py`) are built in CI and
  checked against the declared dependencies; `scripts/check_shipped_data.py` checks that all
  reference data is in the wheel and that nothing downloads at run time (`docs/INSTALL.md`).
- `shape diff`: new `row_count_change` kind (a table with more than twice or fewer than half the
  baseline's rows; thresholds `row_count_ratio_max` and `row_count_ratio_min`). `distribution_change`
  now respects sample size: a changed fitted-family name is reported only when the samples also
  differ by more than sampling noise, so two samples of one distribution no longer trigger it. A
  planted-drift sweep (`tests/diff/test_drift_sweep.py`; fast in CI, full nightly) guards both
  (`docs/DRIFT.md`).
- `shape bridge` (`docs/BRIDGE.md`): a versioned JSON request/response protocol on standard input
  and output (`api_version` `1.0`, request id, `result` and `warnings`, or an `error` with a stable
  code in the groups usage, input, policy, privacy, io, auth and internal). It serves the 17
  commands of the original JSON bridge (the four `demo_*` commands are specified and answer
  `policy.capability_unavailable` until `shape demo` exists) plus `profile`, `diff`, `check`,
  `verify` and `job_status`, `job_cancel`, `job_list`. Long-running commands return a job id
  (`options.async`); job state is a versioned file per job under `--jobs-dir`, so jobs survive a
  restart (a job whose process died reads as `interrupted`); large results come back as a file
  reference with a content id; results never include the raw values of a classified column unless
  `options.include_raw_values` is set. JSON Schemas for every request and result are published in
  `docs/bridge/schema/` (`shape bridge schema --out|--check`) with test vectors in
  `docs/bridge/vectors/`; a compatibility test per command keeps them from changing silently.
  Harness: the local workload tests.

- `shape.yml` project file and `shape init` (`docs/PROJECT.md`): named sources, a baseline per
  source (previous run, same weekday, rolling window, month end or a pinned artifact, resolved
  against the registry), drift thresholds and ignore lists per column, gates with `observe` or
  `enforce` modes, and column owners and annotations. Versioned (`format`, integer `version`,
  JSON Schema `shape-project-v1.schema.json`, a frozen version 1 file in the tests).
  `shape profile`, `diff`, `check` and `verify` read it when present and every flag overrides it;
  `shape init` scaffolds `shape.yml`, folders, `.gitattributes` and an example CI workflow;
  `shape project validate` reports every problem with its key path. PyYAML stays an optional
  extra (`yaml`).

- Mergeable profiles (`docs/PROFILE_MERGE.md`): `shape profile --sketches` keeps an optional,
  versioned sketch state beside the profile (the profile and its content id are unchanged), and
  `shape profile merge A.shape B.shape -o OUT.shape` / `shape.profile.merge_profiles` combine
  profiles of partitions or days without re-reading the data: exact statistics exactly,
  cardinality, quantiles and top values within each sketch's documented error. Merged profiles
  carry their inputs' content ids (`Profile.merged_from`).
- `fabric-mirror` sink (`docs/FABRIC_MIRROR.md`): tables as Parquet or CSV files in a Fabric open
  mirroring landing zone, local or `abfss://` OneLake: `__rowMarker__` last (insert, update, delete,
  upsert; `shape continue` delta types map to them), 20-digit sequential file names, publish by
  rename, `_metadata.json` with `keyColumns`. Format rules cited to Microsoft Learn.
- Basic locale packs (`{"strategy": "locale"}`, `docs/LOCALES.md`): places and postcodes for the US, Canada, the UK, Germany, France, India and Australia (GeoNames, CC BY 4.0), phone numbers only in ranges reserved for fiction (US, CA, FR), French first names (INSEE, Licence Ouverte 2.0), and no national identifiers. Names, phone ranges and streets for the other countries are not shipped yet; each provider says so. Sources and licences: `THIRD_PARTY_NOTICES.md`.

- `sqllocks-shape-simulation`, financial simulator: the default window is now the whole span of
  the transactions plus one settlement batch, not 24 hours, so settlements, fraud bursts and
  clearing cover every month of a multi-month table. `duration_hours` still overrides it
  (`docs/plugins/simulation.md`). Recorded as a named, probed difference (`SIM-9`) in the
  parity harness.
- `shape capture` reads every input `shape profile` reads (Parquet, Delta with `--version` and
  `--as-of`, JSONL, globs, folders, `abfss://`) through the same source layer, and `--dataset`
  captures a folder of one file per table. The model has the same content for the same data in any
  format. `shape compatibility` compares models with several tables per table. `docs/CLI.md` and
  `docs/QUICKSTART.md` show the schema-change check on a Parquet feed.
- `--auth cli|msi|spn|sql|device-code|fabric` and credential references (`docs/plugins/fabric-auth.md`) for
  every Fabric writer, source and sink: `shape generate --scale-mode`, `shape emit`, `shape stream`,
  `shape profile` and `shape jobs`. Secrets are `env://`, `file://` or `kv://` references (one shared
  resolver in core, `shape.security.credrefs`; Azure Key Vault comes from the Fabric plugin), never
  command-line values; `file://` refuses a secret file that group or others can read; connection-string
  passwords, keys and tokens are redacted in errors, job records and logs.
- `sqllocks-shape-dbt` (`docs/DBT.md`, issue #44): `shape from-dbt` (a dbt project's `manifest.json`,
  `schema.yml` or `sources.yml` as a generation schema: keys, foreign keys, enums, types with decimal
  precision and scale), `shape to-dbt-tests` (a contract or a profile as `schema.yml` tests for
  `dbt_utils` and `dbt_expectations`, with `--merge` and a documented round trip), `shape dbt-seeds`
  and the `dbt-seeds` sink (CSV seeds with a `seeds:` block of column types, size guidance),
  `shape dbt-report` (one report for a dbt run and a Shape check and drift comparison), a jaffle-shop
  sample project (`examples/dbt_jaffle_shop`) built against DuckDB in CI, and the Fabric pipeline
  `shape_dbt_gate` with the notebook `shape_profile_dbt` (the dbt job activity is `[VERIFY]`).
  Fix: `shape check` reported every `min` and `max` rule of a decimal column as violated.
- `sqllocks-shape-behavior` and the plugin group `shape.behaviors` (`docs/plugins/behavior.md`):
  declarative state-machine modules run by a simulator on a virtual clock (deterministic per seed,
  resumable, vectorized across entities), an event stream as Arrow tables, an extension point for
  domain events, an importer for Generic Module Framework JSON modules that you download, three
  example modules (subscription lifecycle, equipment maintenance, a small healthcare example) and
  `shape behave run|check|import-gmf|examples`. Plugin API v1 gains the `Behavior` protocol,
  `shape.plugins.kit.check_behavior` and `examples/behavior-plugin`.

- `shape generate --scale-mode local_single|local_mp|fabric_spark` and `shape jobs list|status|cancel|resume`
  (`docs/SCALE.md`): the scale router with sinks (memory, Parquet part files, Lakehouse, Warehouse,
  SQL Database, KQL), a durable job store (submit, status, cancel, resume), the `fabric_spark` router
  with its `shape_spark_worker` notebook, a per-chunk-file process option, `ChunkedGenerator` and
  `MultiStoreWriter`. Row counts are exact in every mode. Harness: the local workload tests
  (T-21 for retail at medium, with negative controls).
- `sqllocks-shape-simulation`: file-drop, SCD2-drop, stream, hybrid and workflow simulators and
  `shape simulate file-drop|scd2|stream|hybrid|workflow` (`docs/SIMULATION_FILES_EVENTS.md`). The
  stream emitter runs on the emit runtime (its pacing, sinks and encoders); the runtime accepts any
  counted, resumable sequence of event blocks (`EventSequence`), and sink selection moved from the
  `shape emit` command to `shape.streaming.emit.open_sink`. Harness:
  the local workload tests (mechanism parity and T-21 per simulator, an allow-list
  of the defects fixed, negative controls).
- `shape-simulation` (`docs/plugins/simulation.md`): the pattern simulators (clickstream, financial
  reversals / fraud bursts / settlements, IoT drift / missing readings / alert storms / fleet status,
  operational logs with distributed traces, pulse rideshare telemetry and marts) as Arrow/numpy
  modules, and `shape simulate clickstream|financial|iot|operational-log|pulse`. A run is
  reproducible from its seed (ids come from the seed; the clickstream window starts at
  `start_time`); the financial `transactions` columns follow the configuration; log events that
  start a trace carry its ids; `latency_spike_enabled` and `outage_enabled` are honoured and a run
  without tracing has no trace ids; fractional durations count; IoT alerts do not depend on the
  storm switch; readings per sensor and the domains' own column names are understood. Harness:
  the local workload tests (parity verifier, negative controls,
  allow-list probes).
- Landing layout (`docs/LANDING.md`): `--path-template`, `--batch-date` and `--table-format` on
  `shape generate`, `shape continue` and `shape chaos` write one file per table per business date
  (`{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}`) with a format per table; the file sinks
  take `path_template` and `batch_date`. Output without the options is unchanged.
- Daily batches (`docs/INCREMENTAL.md`): `shape continue --daily-rows TABLE=N --start-date D
  --batch-date D [--end-date D]` and `shape.generation.batches.BatchGenerator` write one day's new
  rows with stable keys and foreign keys into earlier days, regenerable alone byte for byte.
- `shape chaos` and `shape.chaos.groundtruth` (`docs/CHAOS.md`): named corruptions (`duplicates`,
  `orphan_keys`, `date_shift`, `negative_amounts`, `case_whitespace`, `pii_fill`, `type_change`,
  `null_creep`) with a rate and a seed, and a JSON Lines ground-truth log of every change.
- `shape stream-profile` reads files (issue #33): a path or `file://` URI, a folder, a glob or `-`
  (standard input), as JSON lines (what `shape emit` / `shape stream` write, flat or CloudEvents),
  CSV or Parquet, with the same windows, lateness, event time and checkpoints as a broker;
  `--order event-time` replays a file in time order. `docs/plugins/streaming.md`.
- Delta time travel (issue #36): `shape.profile(path, version=N)` / `as_of=...` and
  `shape profile DIR --version N | --as-of TIMESTAMP` profile an earlier state of a Delta table;
  `as_of` before the first commit is an error, not version 0. The Delta version and commit time
  are recorded as `Profile.provenance` in the `.shape` manifest (not in the profile body).
- Stream API durations (`TumblingProfiler`, `SlidingProfiler`, `SessionProfiler`; issue #34):
  a duration is a `timedelta` or a string with a unit (`"60s"`, `"5m"`). **Breaking:** a bare
  `int` or `float` other than `0` now raises `ValueError` instead of being read as microseconds
  (`60_000` was 60 ms and gave 360 windows for 6 minutes of events instead of 6). Snapshots and
  checkpoints are unchanged and still restore. Spec: `docs/specs/STREAMING_SEMANTICS.md` §2.
- CLI fixes (ISS-cli, `docs/CLI.md`, `docs/REGISTRY.md`): `python -m shape` works; `shape.profile`
  accepts a list of row dicts and `examples/shape_as_code.py` runs (a test runs every example);
  one error policy for the whole CLI: an expected error is `shape: error: MESSAGE` with exit
  code 2 and no traceback (`--debug` or `SHAPE_DEBUG=1` shows it; a bug still raises), and a
  missing file reads the same everywhere. `shape doctor` prints a readable report (Shape version,
  kernel, each package with what needs it; `--json` for scripts; exit 1 when a required package is
  missing). `shape profile` warns on a table with 0 rows (`--fail-on-empty` exits 2). `show` is
  documented as the alias of `inspect`, and `capture` as the model-writing command.
  `shape compatibility` and `shape fidelity` say what they expect instead of failing in a decoder.
- **Registries no longer commit raw values by default.** `shape registry ROOT commit` refuses a raw
  profile (`LocalRegistry.commit(..., allow_raw=False)` raises); `--safe` commits its share-safe
  form, the output of `shape profile safe` commits as it is (leak-scanned first), and `--allow-raw`
  keeps the old behaviour with a warning. `shape profile registry save --safe` stores the safe form
  (`<name>.safe.json`) and a full save says on stderr that it holds real values. `shape registry`
  also gained named arguments, `--meta KEY=VALUE` and `--business-date`, a readable `created` time in
  `log`, `list`, `show`, `diff`, and `checkout -o OUT` (it no longer writes binary to a terminal).
  **Behaviour change:** committing a raw profile to `shape registry` now exits 2.

- Drift (`docs/DRIFT.md`): one engine, `shape.drift.engine`, behind `shape.diff`, `shape.drift.compare`,
  `ShapeMonitor`, `ShapeTimeline.changes` and the stream profiler's windows; `shape.diff` takes
  window profiles. New comparisons with documented defaults: category proportions (`category_shift`),
  pattern, spread, KS distance from the quantiles (`distribution_shift`), min/max (`range_change`),
  string length, outlier rate, boolean true rate (`true_rate_change`; contract rules
  `min_true_rate` / `max_true_rate`), uniqueness, hour of day and day of week. No false drift on
  keys, unique columns of different sizes or date strings. `shape.diff` takes `ignore_columns`,
  `column_thresholds`, `only_columns` and `policy`; `shape diff` takes `--ignore`, `--only`,
  `--policy`, `--threshold` and a flag per global threshold. Change records have a `score`;
  `MonitorEvent.drifts` carry column, kind, severity and score.
- `shape generate-drift` and `shape.generation.drift_plan`: planted drift over time (step, ramp and
  window events on null rates, category weights and new values, distribution parameters, added and
  dropped columns, type changes), one folder of tables per day, each day's schema and an answer key
  (`ground_truth.json`).
- `shape stream` (`docs/EMIT.md`): one table's rows as events in event-time order, on the `shape emit`
  runtime (same options, sinks, formats and delivery guarantees; `--table` required, `-t -s -m`,
  `--rate` 10, `--max-events` is the earliest N events). The flat-event encoder is vectorised
  (Arrow kernels, several threads for large batches) with byte-identical output. Harness:
  the local workload tests (equivalence verifier, bench, negative control; wired into
  `run.py --only stream`).
- Composites (`docs/GENERATION_ENGINE.md`): `shape composite PRESET|DOMAIN+DOMAIN` generates several domains
  as one dataset, tables prefixed with their domain and linked by shared entities (a person, a location,
  an organisation). Six presets (`enterprise`, `healthcare_system`, `smart_factory`, `digital_commerce`,
  `campus`, `telecom_bundle`; `shape presets --composites`); `generate`, `describe` and `presets` take a
  composite as a target, and `shape.api.generate("enterprise")` returns its tables. `retail` is a packaged
  domain like the other thirteen.
- Live fidelity (`shape emit --live-target`, `docs/EMIT.md`): the emitted events are teed into the
  stream profiler and scored against a target as they go, with the score of `shape fidelity`
  (`score_prepared` is now its single scoring function), drift alerts (`score-low`, `score-drop`,
  `column-low`, `live-error`; stderr, a JSON-lines file and the report), `--live-fail` exit code 1.
  `GlobalProfiler.peek()` reads a running profile. Harness: `benchmarks/live_fidelity/run.py`.
- `shape emit --realtime`: a full garbage collection (cost grows with the host process's heap) and
  a slow checkpoint `fsync` no longer stall the pacing, which showed up as a late batch and a short
  second. Realtime runs freeze the existing objects while pacing and write checkpoints on a writer
  thread; the checkpoint still never passes an undelivered event.
- Emitters (`shape.emitters`): `console`, `file` and `jsonl` in core; `kafka` (`sqllocks-shape-kafka`),
  `eventhubs` (`sqllocks-shape-eventhubs`), `eventstream` and `eventhouse` (`sqllocks-shape-fabric`).
  Every message carries the idempotency key `<table>/<seq>`; delivery is at-least-once with
  backpressure and a checkpoint that never passes an undelivered event. The contract each emitter
  meets is `shape.streaming.emit.contract` (`docs/EMIT.md`).
- `shape pack run|validate|list` and `shape.scenario` (`docs/SCENARIO_PACKS.md`): scenario packs (YAML that
  bundles a domain, a `file_drop`, `stream` or `hybrid` simulation, chaos, validation gates and landing
  paths) and generation specs (GSL, `*.gsl.yaml`), with a run manifest written next to every run.
  Shape ships no packs of its own. Landing paths cannot leave the output directory; unknown gates fail
  instead of passing; chaos in a pack or spec is applied.
- `shape emit`: the emitter runtime (`docs/EMIT.md`). Streams a domain's or schema's rows as
  JSON-lines events with the idempotency key `(_shape_table, _shape_seq)`: realtime pacing
  (`--rate`, `--burst START:DURATION:MULT`) or as fast as possible (the default), `--out-of-order`,
  `--anomaly-fraction` (through the `shape.chaos` mutator protocol), `--max-events`, `--duration`,
  a CloudEvents envelope, backpressure, at-least-once delivery and a checkpoint on shutdown
  (`kill -9` then restart, deduplicated on the key, equals an uninterrupted run).
  `shape.streaming.emit` holds the runtime.
- Chaos engineering (`docs/CHAOS.md`): `shape.chaos` injects deterministic data-quality faults in six
  categories (schema, value, file, referential, temporal, volume) through a seeded `ChaosEngine` and
  as `shape.chaos` plugins, and `shape.chaos.inject_anomalies` corrupts a chosen fraction of the
  rows of a batch without changing its schema (the entry point for `--anomaly-fraction`).
- `shape mask PATH -o DIR` (and the `mask` built-in of `shape.transforms`): replaces personal data
  in CSV or Parquet files with synthetic values of the same format (`docs/MASK.md`). Columns are
  found from their names and from the value patterns of Shape's profile engine; null positions,
  types and every other column are kept, the same value gets the same replacement everywhere so
  keys and the columns that refer to them still match, and no original value is written back.
- Incremental data (`docs/INCREMENTAL.md`): `shape continue DOMAIN --input DIR -o OUT` writes the next
  batch of inserts, updates and soft deletes for existing data (`--inserts`, `--update-fraction`,
  `--delete-fraction`, `--transitions`, `--seed`, `--as-of`; rows tagged `_shape_delta_type` and
  `_shape_delta_timestamp`), and `shape time-travel DOMAIN -o OUT` writes monthly snapshots of a
  dataset that grows, churns and changes with seasonality (`--months`, `--growth-rate`,
  `--churn-rate`, `--update-fraction`, `--seasonality`, `--start-date`). Python:
  `shape.generation.incremental`. Zero rates change nothing, no row is both updated and deleted,
  inserted rows never reference a parent deleted in the same delta, and every snapshot keeps all its
  foreign keys.
- Profile files and the profile registry (`docs/PROFILE_REGISTRY.md`): `shape profile export|import|list|validate` and
  `shape profile registry list|save|delete|tag|diff|reindex|validate` (named, tagged `.shape` profiles under
  `system/table/name`; `shape registry` keeps its meaning).
- Generation start-up: `import shape` now selects Arrow's system memory pool for the whole process (`ARROW_DEFAULT_MEMORY_POOL=system` when `pyarrow` is not loaded yet; otherwise transparent huge pages are switched off for the process on Linux), which removes a 10 to 13 ms stall at the first allocation and about a fifth of the time of a medium run on a virtual machine; `SHAPE_MEMORY_POOL=default` turns it off (`docs/GENERATION_ENGINE.md`). The text providers build their name pools from the file bytes (10 to 20 ms less on the first name column). Generated values are unchanged.
- Fixes (PF-06b): `shape check` / `shape.check` no longer passes a multi-table contract
  (`{"tables": {...}}`) against a single-table profile without testing it: that is now a
  `ContractError` (exit 2), and a table the contract names that the profile lacks is a
  `table_exists` violation (exit 1). A contract may still check only some of a dataset's tables. `shape profile FOLDER --dataset` profiles one table per file (named by the file
  name); a folder whose files do not share their columns is refused without it. Artifact folders
  of the Fabric and Synapse notebooks are named to the microsecond and claimed exclusively
  (`20260930T120000123456Z`, then `_2`, `_3` ...), so two runs in one second no longer overwrite
  a baseline.
- Fidelity tiers 1 to 3 (`docs/FIDELITY_TIERS.md`): `shape fidelity REFERENCE SYNTHETIC --tier 1|2|3`
  (tier 1: Gaussian-mixture fits, conditional profiles, adversarial AUC, temporal profiles and
  periodicity; tier 2: format preservation, string similarity, cardinality and anomaly-rate checks;
  tier 3, experimental: Chow-Liu dependency trees), `shape drift REFERENCE CURRENT [--psi]`, the
  `bootstrap` generation strategy, `shape.privacy.dp.DifferentialPrivacy` (Laplace and Gaussian
  noise from OS entropy unless a seed is passed), and `shape ctgan` with the `[ctgan]` extra. New
  extra `[advanced]` (scikit-learn). Without scikit-learn, tier 1 still runs and names what it left
  out.
- Profile to generation: `shape generate --from X.shape` fits a generation schema to a profile and
  generates it at the profile's row counts (`--rows N` for a one-table profile; `--scale`, `--seed`,
  `--format`, `-o` and `--dry-run` work as for a domain). It rebuilds each column's marginal
  (exact value weights for enums, the fitted family or the quantiles for numbers, types kept),
  missing values, a Gaussian copula over the numeric columns (the profile's Pearson correlations,
  corrected for the marginals' shapes) and the month, weekday and hour profile of timestamps.
  `shape plan X.shape` lists every field of the profile as `preserved`, `approximate` or
  `not_modelled`, with the reason (`--status`, `--rows`); it no longer rejects profiles. Python:
  `shape.generate(profile)` and `shape.plan(profile)`; `shape.generation.fit.fit_schema`.
- `shape learn PATH [-o SCHEMA.json] [--format csv|parquet|jsonl] [--domain NAME]` profiles data
  files (a directory is one table per file) and writes the generation schema that reproduces them
  (`shape.generation.learn.SchemaBuilder`).
- Fixes: `missingness` and `gaussian_copula` (`shape.generation.future`) now apply the missing
  values and the marginal distributions (G5); `plan_reconstruction` checks each item instead of
  reporting everything as preserved (G6).
- Generation: a generator may set `output_type` (`int64`, `float64`, `bool`, `string`); `temporal`
  takes `granularity: "day"` and one weight per hour in `hour_of_day`; the `ipv4`, `postcode` and
  `zip_plus4` providers are built in; the copula's `generation.output.copula_nulls` and
  `copula_threshold` options.
- `shape generate` starts faster and ends sooner: the generation path never imports pandas
  (`shape.generation.arrowkit`), sinks and sources load on first use, Parquet row groups are 256k
  rows (were 1M), one core is left to the writer threads, and two passes run while tables are still
  being made (summed children, leading business rules). The data is unchanged.
- Generation commands: `shape generate DOMAIN|SCHEMA.json` (`--mode 3nf|star`, `--scale`, `--seed`,
  `--format summary|csv|tsv|jsonl|parquet|excel|sql|delta`, `-o DIR`, `--dry-run`, the SQL options
  `--sql-dialect`, `--schema-name`, `--batch-size`, `--sql-ddl`, `--sql-drop`, `--sql-go`, and for
  Delta `--delta-mode`, `--partition-by`), `shape describe`, `shape list` and `shape presets`.
  `shape from-ddl` writes a schema file that `generate` and `describe` read. The retail domain has
  a `star` schema next to `3nf`. `shape generate --rows N` with no target still prints demo rows;
  Python:
  `shape.api.generate("retail", scale="medium", seed=42, mode="star")` returns the generated
  Arrow tables (`result.tables`, `result["order"]`).
- Run logging and metrics for every command: `shape --log-json --log-level LEVEL --metrics FILE
  COMMAND ...` (or `SHAPE_LOG_JSON`, `SHAPE_LOG_LEVEL`, `SHAPE_METRICS`) logs JSON lines to stderr
  and writes the run's metrics (command, exit code, seconds, rows, tables) to FILE. In Python:
  `shape.runlog` (`configure_logging`, `RunMetrics`).
- `shape validate FILE` dispatches on what the file holds: a generation schema goes through the
  schema validator (JSON Schema, then keys, relationships, rules, scale presets and strategy
  keys; exit 1 when invalid), a contract through the contract validation, and any other document
  exits 2. A contract that does not validate now exits 1 (it raised before).
- `shape fidelity REFERENCE SYNTHETIC` (alias `compare`): scores synthetic tables against reference
  tables, per column, per table and overall, on a 0-100 scale, and writes JSON, Markdown or HTML
  reports (`-o`, repeatable; the `shape.reports` plugin group). Pass marks `--min-score`,
  `--min-table-score` and `--min-column-score`; exit 0 on pass, 1 on failure, 2 for bad input. The
  scoring equals the one the generation equivalence standard (plan T-21 clause h) asserts per
  table, to within 1e-9, and a missing column or table scores 0 and an empty reference fails. See
  `docs/FIDELITY.md`. `shape fidelity PROFILE.json DATA.csv` certifies as before.
- Fixes: `certify` scored a column missing from the generated data 1.0 and passed a profile that
  describes no columns (a missing column now scores 0; an empty profile fails); `shape
  certify-shapes` always exited 0 (it now exits 3 when the score is below `--threshold`, default
  0.9).

- Enum rule: a profiled column is an enum (`is_enum`, with every value in `enum_values`) only if,
  besides the existing size limits (fewer than 200 distinct values, or a distinct ratio under 0.30
  with fewer than 50,000), its values repeat: distinct values are at most half of the non-null
  values, and a unique column is never an enum. Before, every column of a table under 200 rows
  was an enum, unique keys, e-mails and free text included, so generation from a profile
  resampled only those exact values. Same rule in both kernels and in the SQL Server plugin's
  sampled profile. `value_counts_ext` (the top 500 values) is unchanged.
- `shape from-ddl FILE`: reads SQL `CREATE TABLE` DDL (SQL Server, PostgreSQL, MySQL, ANSI; inline,
  table-level and `ALTER TABLE` foreign keys) into a generation schema, with smart inference of
  distributions, key patterns, row ratios, seasonality, correlations and business rules
  (`--smart`, the default; `--explain` prints each decision). See `docs/GENERATION_ENGINE.md`.
  Fixes in the import: a column-level `REFERENCES parent(col)` is a foreign key to that column
  (it was ignored, and the guessed key could name a column that does not exist); `VARBINARY(MAX)`,
  `BINARY(MAX)` and the `BLOB` types are binary and left out; names match whole words
  (`discount_pct` is a percentage, `state` is a state and not a status, `model` is not a category,
  a `catalog` table is not a log); `gender CHAR(1)` and other one-character codes get a value set;
  a parent's total is the sum of its child rows (CR-08); a key the DDL does not declare, guessed
  by name (`customer_id`), points at the parent's primary key (it pointed at
  `customer.customer_id`, which usually does not exist) and is not guessed when the parent has no
  single-column key; `CustomerId` and `CustomerID` are read like `customer_id`; generated strings
  never exceed the declared length (`country_code CHAR(2)` got six digits).
- Stream profiling runtime (`shape.streaming`): tumbling, sliding, session and global windows over
  Arrow micro-batches profiled in bounded mode, with watermarks, allowed lateness and a late-data
  policy; windows can be snapshotted and restored exactly. Bounded per-key sketches (LRU, TTL, a hard
  memory cap), a vectorized windowed `Deduplicator`, and a `StreamConsumer` that commits offsets and
  window state in atomic checkpoints, resumes after a restart and reconnects without replaying.
  Fixes: `TumblingWindow` could not be restored, keyed state grew without bound, `deduplicate_ids`
  looped over every row, and a reconnect replayed the batches already delivered. See
  `docs/specs/STREAMING_SEMANTICS.md`.
- `shape verify`: validation gates (schema conformance, nulls, primary keys, foreign keys,
  ranges, temporal consistency, file format, schema drift, distributions), a quarantine for
  failed files and tables, and a Markdown or JSON report. See `docs/VERIFY.md`.
- Relicensed under the MIT license.
- Repository cleaned up for public release: removed internal milestone and
  qualification records.
- README and changelog rewritten to describe the current state.
- Readers (`shape.io`): an existing file whose name has `[`, `*` or `?` is read instead of
  treated as a glob (#489); a row iterable keeps a key first seen after the first batch, and a
  column with only nulls so far takes the type of its first values (#492); several files read as
  one table work when a column is empty in the first file (#493); a `RecordBatchReader` source
  read twice raises instead of yielding nothing (#498); a damaged Parquet/IPC file or an unknown
  `columns` name is a `ReaderError` for every file kind (#499); a workbook sheet whose name
  contains `#` can be selected (`book.xlsx#Q#1`, #497).
- Workbooks: the zip-bomb rule (more than 256 MiB uncompressed and more than 1,000 times the
  compressed size) also applies to the archive total, not only to each member (#563).
- Readers (`shape.io`): a one-file source is named after the file without its recognised format
  suffix and any compression suffix, no longer up to its first dot (#506). **Tables of file names
  with more than one dot are renamed** in profiles and models: `my.data.csv` is `my.data` (was
  `my`), `sales.2024-01.csv.gz` is `sales.2024-01` (was `sales`), so `sales.2024-01.csv` and
  `sales.2024-02.csv` no longer share a table name. Names with one dot (`orders.csv`) are unchanged.
- Connectors: `DBAPISource` refuses result columns with the same name and says when a statement
  has no result set (#494); Kafka and Event Hubs decoding keep every key and each value's type
  (#495).
- Connectors: `reconnecting_batches` waits before each reconnect (keyword-only `backoff`, 0.5 s
  doubling per failure in a row, capped at 30 s; `backoff=0` reconnects at once; `sleep=` for
  tests), instead of spending `max_attempts` in milliseconds (#562). `ExactlyOnceProjector`
  documents that its id set grows with the number of distinct message ids (kept unbounded so a
  duplicate is never re-admitted).
- Capture: `capture_columns(mode=...)` applies the mode to every column and refuses an invalid
  one (#496); an int beyond the float range is counted as an infinity, and rows that are not
  mappings are a clear `TypeError` (#500).
- Output paths: `MultiStoreWriter` labels stay unique (#501); landing `render_path` refuses an
  extension or drive-letter table that leaves the directory, and `{hhmmss}` is UTC (#502); a
  store file needs a name below the root, and a long valid file name gets a valid temporary name
  (#503).
- `shape.query`: `column("table.column")` works on a one-table model, and a path after a missing
  name says what is missing (#504). `docs/LANDING.md` lists the rolling tokens and the
  `--to abfss://` cloud landing (#505).

- Profiling: a Parquet file of a few kilobytes that declares millions of rows no longer has to be
  read whole to be refused. The optional `SHAPE_MAX_INPUT_ROWS` and `SHAPE_MAX_INPUT_BYTES` budgets
  are checked against the footer before any data is read (`docs/PROFILING_NOTES.md`; #286).
- Profiling: the DuckDB Delta fallback refuses an `abfss://` location whose account name is not a
  valid storage account name, instead of splicing it into an Azure connection string (#296).
- Profiling: `distribution_params` are the same in every process. The first fits of a process,
  run on several threads, could use libm's `exp`/`log` instead of numpy's and differ in the last
  digits (#323).
- `shape.profile.infer.infer_column_type` rules out dates in a text column after one failed parse,
  as the profiler does, instead of parsing every distinct value: uses the same date-classification path on the profiling
  benchmark files, with the same answers (#336).

### Fixed

- Plugin framework, integrations, packs and location (audit lane AUD-pluginfw): the ADF Batch gate
  scripts report any unexpected error as an error gate (exit 2, `gate.json` written) instead of
  exit 1, which means a contract violation (#366); `profileLakehouseTable` stops reading a table as
  soon as it passes the cell limit (#367); Census county and place gazetteer records carry their
  state (#368); `shape.packs.test_domain` reads dataclass rows with slots (#369); a location weight
  must be positive and finite (#370); `redact` hides credentials in a URI's query string (`sig`,
  `password`, `AccountKey`, `SharedAccessKey`, tokens) (#371); a pre-release domain version sorts
  before its release (#372); `load_domain` names the file and the problem (#373);
  `location_from_spec` accepts ZIP+4 with a dash and refuses blank parts and non-ASCII digits (#374);
  `shape plugins info` handles duplicate registrations and the discovery-error record (#375); a
  plugin command's `sys.exit("message")` reaches stderr (#376); the kit's `--samples` is a usage
  error when it cannot be resolved (#378); `parse_run_folder` returns `None` for an impossible
  date (#379); `load_census_gazetteer` reads only the head of a file to find its delimiter and
  refuses an unknown kind (#380); `generate_person` keeps seeds of opposite sign apart (#381);
  `scope_from_specs` says how many weights and locations it got (#382).
- Plugin allow-list (audit lane AUD-pluginfw, second pass): the process-wide plugin host applies
  `plugins.allowlist` of the nearest `shape.yml`, relative to that file, and blocks non-built-in
  plugins when that section cannot be read (#583; the project schema key is still open); a plugin
  the allow-list blocks no longer hides the built-in of the same name (#750); a distribution that
  calls itself `sqllocks-shape` is checked like any other unless its entry point targets the
  `shape` package (#751); the plugin reach statement names the `json-schema.org` identifier that
  shape-kafka writes (#759).

- Generation (second bug hunt): a generation schema written by `shape migrate` loads and
  validates (#651); `ShapeTimeline` interpolation gives the same data in every process (#652);
  a `conditional` fixed text such as `"02134"` stays text (#653); `SpecDocument.save` keeps the
  file mode and a deeply nested spec is a `SpecError` (#654); the locale strategy names the real
  `sqllocks-shape-domains` package (#655); `validate()` reports a NaN `null_rate` and a negative
  `max_length` (#656); `generate --from` a merged profile draws the value sets the merge lists
  exactly (#682); `conditional_table` keeps `output_type: "string"` labels as text (#693); drift
  plans may declare `format`/`version` and their answer key declares its format (#703); an
  unknown scale preset is an error instead of 100 rows a table (#717); a foreign-key cycle is a
  validation error that the dry run reports (#733); a drift plan that fails on a later day writes
  nothing (#737); a changed domain definition no longer changes later loads (#343); an empty child
  of an empty parent generates (#220).

- Fixed (quality hunt, #569-#578, #589, #590, #605-#607): `shape scorecard` no longer rounds a
  failing check up to 100 (capped at 99.99), scores a gate that failed without a row-level cause as
  its own 0 check, scores the reconciliation and time-series gates (consistency and timeliness),
  refuses `--history` without `--name` and says when no schema or config was given; `reconcile`
  treats NaN as equal to NaN, accepts a key column that is also an aggregate column, reports a
  sum or mean of a non-numeric column as a `reconcile.column_type` finding, and sums integers
  without wrapping at 2**63; the time-series stuck check reports a column it cannot compare;
  `psi_report` no longer returns NaN (and "not drifted") for a column that gained infinite values;
  `bootstrap_table` names a negative `n_rows`; the `no_future` check reads timestamps without a
  zone as UTC instead of the machine's local time; quarantine keeps a second item of the same name
  (`-2` suffix), writes strict JSON Lines (non-finite floats as null) and lists past a bad
  metadata file.

- Mergeable profiles (HUNT2-profile): profiles with decimal, time-of-day, binary or duration columns
  merge, including with `--exact-only` (#592); the sketched merge matches columns by name, so
  partitions listing them in another order merge (#593), and an empty or all-null CSV partition
  merges, its null-typed column taking the type of the partitions with values (#594); a newer
  `snapshot_version` in `sketches.json` or a newer `merge` block version is refused with an
  upgrade message (#595); promoted extremes are tagged like the profile of the union (#599).
- Drift (HUNT2-profile): a constant float column no longer shows `mean_shift`/`spread_change` from
  floating-point rounding (#616); a drop to zero (an empty table, no distinct values, no spread)
  scores 1 as documented, so score gates fail (#617); a merged profile's unknown pattern and
  distribution are not read as changes (#618); the joint kinds follow per-column thresholds,
  `min_severity` and the ignore/only lists (#619); `only` keeps table-level changes matched by
  `*`, the table's name or `table.*`, `table.column` matches in a single-table profile, and `*`
  thresholds treat single tables and datasets alike (#620); a dataset's `row_count_change`,
  `table_added` and `table_removed` carry a `table` field (#621).
- Contract emission (HUNT2-profile): a column with no rules is emitted as a required column instead
  of being dropped (#641); DDL constraint names are unique within a script and PostgreSQL names
  respect its 63-byte limit (#642); NaN or infinite numbers, and rates outside 0..1, are refused
  (exit 2) instead of written into JSON Schema/GX output as non-JSON (#644).
- Rule proposals (HUNT2-profile): `shape proposals contract --merge` validates the contract it
  merges into, refusing a newer version or unknown keys (#645); CSV date columns get a `range`
  proposal (#646); a range clamped at zero is `0.0`, never `-0.0` (#647).
- Capture (HUNT2-profile): `capture_rows`, `capture_arrow` and `shape capture` keep integers exact
  past 2**53 (#685); `capture_columns` describes an empty column of any type instead of raising
  `KeyError` (#687); binary values are captured as their UTF-8 text, and bytes that are not UTF-8
  are refused naming the column, instead of storing `b'...'` reprs (#688).

- Privacy second hunt (HUNT2-privacy): a safe-profile `k` below 2 is refused instead of silently
  turning suppression off (#596); `release_for` applies the policy to the `joint` block and to
  `placeholders` (#650); `validate --safe` flags a newer, malformed or foreign format version
  (#657); `ProfileRegistry.save(safe=True)` keeps the `unsafe` stamp (#669); registry refs and
  tags are validated, tag writes are atomic and bad metadata writes nothing (#671); wrongly typed
  documents are a `ValueError` (#679); masking a date at the edge of the calendar is a
  `MaskingError` (#600) and emails that differ by case are one address in `mask_tables` (#601);
  signing a symbolic link in place signs its target (#706).

- Plugin trust, signing and kit fixes (HUNT2-plugins, #579-#588): a RECORD path with a line break
  is refused when building a signed file list (two file lists could share one signed message); a
  malformed RECORD (CSV error, `shake_*` hash) blocks the plugin instead of escaping discovery;
  `shape plugins sign` quotes file names with commas, keeps the wheel's file mode, names the output
  path in errors, and a signature file with version below 1 is refused; the conformance kit treats
  NaN as equal to NaN and reports null behavior ids as conformance errors; a crashing plugin command
  honours `--debug`; `shape plugins info` and `list --group` accept the short group form
  (`commands:name`).

- Kernels (HUNT2-kernels): the first fits of a process made by several threads at once no
  longer run before numpy's `exp`/`log` hooks are installed, so a profile's `distribution_params`
  (and its content id) are the same on every run (#323); the profile kernel's mean of finite values
  near the float maximum is finite and its variance never NaN or negative (#748), and its exact
  quantiles of such values stay between the minimum and maximum (#755); the pure-Python sketches
  reject the non-uint64 keys, lists and chunked arrays that the native ones reject (#749).

- Command line second pass (HUNT2-cli, #604, #608-#614, #670, #672, #680, #681, #692):
  a failed `--json` report no longer truncates the file (and names it); a NaN drift threshold
  (`--null-rate nan`, `.nan` in `shape.yml`) is refused instead of switching the check off;
  `shape init` checks every precondition before writing and quotes number-like names so the file
  validates; `profile NAME` and `verify NAME` no longer say a named source was not selected;
  `shape scorecard` reads owners from `sources.*.columns.*.owner` of a valid `shape.yml`
  (`--project FILE`, `--no-project`, `--source`); `from-ddl` refuses a script with no `CREATE
  TABLE` and reads UTF-16 and UTF-32 scripts; `check`, `diff` and `plan` say a JSON file that is not
  an object is not a Shape document; `project validate --json` always prints JSON;
  `continue -o` the input folder, and `profile`, `capture`, `learn`, `dictionary` and `from-ddl`
  with `-o` the input file, are refused instead of replacing the input; `key`, `fd` and
  `privacy-k` refuse a column that is not in the file; `mask --pii/--exclude`, `scorecard
  --classified` and `diff --only` refuse a name that matches nothing.

- Public Python API documentation (#264): every function `import shape` exports has a docstring
  that matches what it does (`shape.generate` documents each input form, what it returns and the
  arguments it ignores; `timeline`, `view`, `query`, `certify` and `plan` say what they read),
  `view`, `timeline`, `certify` and `plan` declare their return class, `shape.types` passes
  `mypy --strict`, and the new `docs/API.md` lists every exported name with the code's own
  signature, checked by `tests/api/`.
- Fabric Spark, job store and scale router (HUNT2-fabric, #631 #632 #633 #634 #635 #636 #637 #638 #639 #704 #713 #715 #718 #726):
  the Fabric Spark router follows only `https://` URLs on the Fabric API host for `Location` and
  `continuationUri`, ends paging on a repeated continuation URI and reports a cancelled notebook
  creation at once; a job record masks the secrets of `auth` (credential references stay), hides
  every form of password in sink settings (braced, quoted, URL, spaced keys), and a resume notices
  a mask inside a connection string; job records declare `format: shape-job` and an integer
  `version`, refuse a newer version naming the release that reads it, and keep fields they do not
  know; `Retry-After` waits are capped at 60 s and an unreadable or non-object Fabric answer gets a
  clear error; a relative output folder is stored as an absolute path so a resume from another
  folder continues the first run; `scale_generate` checks the types of `chunk_size`, `processes`,
  `max_workers`, `seed`, `domain`, `sinks` and `sink_config` before any job exists;
  `parse_run_folder` returns `None` for a name that is not a time; the router closes the sinks that
  opened when another fails to open; a cancelled or failed run no longer marks the Parquet table
  it was writing complete (`_COMPLETE`) or hands a Fabric sink's writer a clean end for a table
  that was cut short (sinks may define an optional `abort()`); the Spark worker reads a table's
  column types from a chunk with a row, so a `capital_markets` job no longer fails on `industry`.
- `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references` no longer depends on test order (#77): it resolves the references in a fresh interpreter and reports which cloud SDK modules got imported, so `azure*` modules left in `sys.modules` by `tests/demo/fabric` cannot fail it.
- Issue #76. The three tests that failed were not numpy-dependent: they called pyarrow in ways older
  releases reject (float16 from Python floats, `if_else` on half-float, hive partition inference on a
  single file). They now build and read their data in a way every supported pyarrow accepts, with
  unchanged assertions. A minimum-versions run showed the declared floors were unusable: pyarrow 14
  cannot be imported with numpy 2, Shape's own code needs `pyarrow.concat_batches` (pyarrow 19), Delta reads need pyarrow 19.0.1 (19.0.0 raises "Repetition level histogram size mismatch"), and
  Rust-vs-numpy bitwise equality holds from numpy 2.3. The declared floors stay `numpy>=2.0,<3`
  and `pyarrow>=14.0.1` (a fixed decision); raising them is with the project owner.
  `ci/constraints-min.txt` pins numpy 2.3.0 and pyarrow 19.0.1, the oldest versions the
  minimum-versions check runs.
- Second audit of the scenario area (`HUNT2-scenario`, the implementation tests):
  pack chaos runs the referential category once over the mutated tables, not once per table, and the
  manifest's `volume` count is the rows added or removed (#659, #660); the `referential_integrity`
  gate fails, instead of raising, when chaos retyped a key (#673); value chaos works on integer
  columns above 2**53 (#661); a stream pack writes its events in batches of 50,000 rows (#712);
  `shape pack run --json` of an invalid spec has the keys of a failed run, `pack list` survives a
  folder or broken link named `*.yaml`, `pack validate`, `run` and `replay` of a spec use the domain
  `--domain` names (#665, #666, #705); a run manifest field of the wrong type is refused with its
  name (#667); `shape demo` records the absolute session folder so a cleanup from another directory
  finds it, `cleanup --dry-run` applies the safety rules to local files, the comparison page
  withholds the values of personal-data columns, a connection profile refuses a secret in any
  field, and an unreadable input file is a message that names the file (#662, #701, #663, #664,
  #716).

### Fixed (command line)

- `shape diff` of two captures honours `--fail-on-drift` (exit 1) and `--json`, and refuses the
  profile-only threshold and column flags instead of ignoring them (#107); `shape check` of an
  evidence document writes `--json` (#108).
- "Not verified" notices are one `shape: note:` line in every command, never a Python warning;
  `registry diff` and `conformance` no longer name temporary files (#109).
- `shape fidelity` checks `--format` before comparing, and an error message is no longer turned
  into "missing key ... in the input" (#110); a `--tier` report is written to every `-o`, as
  `.json` (#111).
- Commands that take a generation schema file read YAML as `shape validate` does (#112); a schema,
  contract, DDL or transitions file that is not JSON or not text is named in the error (#116).
- `shape learn` never writes `Infinity` or `NaN` into a schema (#113); `shape quality` exits 1 for
  a failed check (#114).
- Help for every command, with the verdict exit codes of `check` and `compatibility` (#115).
- `emit`/`stream` range-check `--poison-fraction`, `--retries` and `--checkpoint-every`, and the
  `--live-report` format before streaming (#118, #119); `--chunk-rows 0` is refused (#124);
  `generate --scale-mode` prints its into-memory note once (#117).
- A closed standard output (`shape ... | head`) ends quietly with exit 141 (#120); `demo
  notebook|report` take `-o` (#121); an unknown `--log-level` and an unwritable `--metrics` path
  are refused before the command runs (#123); `learn`, `mask` and `profile registry save` say
  `file not found: PATH` (#125).
- `shape profile registry delete NAME` of a profile that is not in the registry is bad input: exit
  2 and `shape: error: profile not found: NAME`, as for `tag` and `diff` (it exited 1) (#122).
- Built-in generators (audit AUD-builtins, #129 to #149, #201): seasonal and hour-of-day
  `temporal` values stay inside a start or end that has a time of day (the partial edge days weigh
  their share of the day); hour keys such as `"07"` are hours, unknown month or weekday keys are
  errors, and a bound with a time-zone offset is that instant in UTC; the `truncated` distribution
  fills any table and gives the same values for any chunking (the interval must hold at least 5% of
  the base distribution); `empirical` cubic interpolation stays within its outermost anchors; the
  `digits` provider is uniform up to 18 digits; the hierarchy sampler cache never serves another
  dataset's sampler; an nth-weekday holiday rule stays in its month; a monthly payday without
  `days` pays on the 28th; `sequence` values past int64 are an error; a `foreign_key` into its own
  table that cannot be built is a circular-reference error instead of a `RecursionError`; `faker`
  serves only provider methods; `fit_family` refuses degenerate samples with `FamilyError`; spec
  mistakes in `constant`, `choice`, `uniform`, `normal`, `weighted_enum`, `lifecycle`, `formula`,
  `histogram`, `mixture`, `digits`, `pattern`, `address`, `derived` and `foreign_key` are
  `StrategyError`s naming the column.
- Offline install locks (`scripts/offline_lock.py`) now carry the drivers of first-party plugin
  extras: `postgres`, `mysql` and `databases` lock `psycopg` / `pymysql` (#259).
- The shipped-data check flags `from urllib import request`, `from http import client` and
  `urllib3` as network clients (#261).
- The secret check detects Azure Storage and Event Hubs keys, GitHub tokens, AWS access key IDs,
  `client_secret` literals and private-key blocks of any kind (#262).
- `scripts/fuzz_artifacts.py` rejects `--iterations` below 1 instead of passing having fuzzed
  nothing (#263).
### Fixed (documentation, examples and scripts audit)

- Offline locks: a plugin extra's lock now holds the dependencies of the plugin extras it names
  (`postgres`, `mysql` and `databases` get their database drivers; #259).
- `scripts/check_shipped_data.py` reports `from urllib import request`, `from http import client`,
  `urllib3` and `socket` (#261).
- `scripts/check_secrets.py` finds Azure Storage and Event Hubs keys, GitHub tokens, AWS key ids,
  quoted `client_secret` values and encrypted, DSA and PGP private keys, names the line, and scans
  what git would commit, so an in-tree virtualenv no longer fails it (#262, #350).
- `scripts/fuzz_artifacts.py` refuses `--iterations` below 1 (#263).
- The talk's slide 20 quotes what a raw profile holds today, and its claim verifier passes again
  (#377).
- Docs: the tutorial compares models with `shape compatibility` and profiles with `shape diff`
  (#347); `shape demo notebook ... --output` (#305); `pip install sqllocks-shape-fabric` in
  docs/SCALE.md (#247); INSTALL.md states Python 3.11–3.14 and every offline-lock set (#255); the
  README names the shipped safe profile (#346); dead spec references fixed (#348, #507); the
  contributing guide names `make check` (#349). `tests/release/test_docs.py` keeps documented
  commands, options, extras, links and supported Pythons in step with the code.
### Fixed (generation engine audit, AUD-gen)

- Post-passes: the copula runs before the compute phase and rule repair, so computed sums match
  their rows and repaired rules hold; `remaining_violations` is what the finished tables break
  (#169). Temporal `cross_table` rules with `<=` are repaired instead of raising (#170); a
  numeric repair holds for zero, negative, small and integer bounds (#192); `x BETWEEN a AND b`
  constraints are checked (#327).
- Engine: `null_rate` applies to columns made by multi-column strategies (#185); derived counts
  and row-count overrides are checked (#187); lookup and composite-key parents are generated in
  an earlier level and once, whatever the thread count (#186); timestamps cut to a precision
  before 1970 round down (#188); integer output near the int64 bounds no longer wraps (#181);
  `date` and `time` declared output types (#202); `BatchGenerator` applies declared types (#191).
- `GenSchema`: `generation` is optional in `from_dict` and `to_dict` writes dataclass rows (#205);
  `validate()` reports malformed rules, correlations, computed columns, generator references,
  uneven relationships and unknown tables in counts (#193).
- `shape from-ddl`: case-insensitive foreign keys (#172); bracket-quoted types (#173); `ALTER TABLE
  ... WITH CHECK ADD` and unnamed or key-only foreign keys (#174); one-to-one and composite
  primary keys are unique and never null (#175); `-s` overrides win over inferred counts (#176)
  and refuse negative counts (#217); transaction dates use the model's range (#195); inference
  does not depend on statement order (#196); literals and quoted names are opaque, in linear time
  (#197, #204); `NOT  NULL` with any whitespace (#198); MySQL/PostgreSQL types, `ENUM` and `KEY`
  lines (#199); two schemas' same-named tables and dotted names are errors (#200); a foreign key
  to a missing table is a plain column, one key per column (#203); `*_type` template, CR-02 and
  CR-05 fixes (#218).
- `learn` / `generate --from`: non-ISO and zoned temporal bounds (#177, #207); a real `_row_id`
  column is kept (#180); the plan marks row-count fields approximate with `rows=` (#179); decimal
  scale and undefined correlations (#213); provider guesses match whole words (#214).
- `continue` / time travel: values stay inside small integer and decimal types (#171); integer and
  boolean state transitions (#189); `as_of` in UTC and reusable `TimeTravelEngine` (#190);
  composite keys, all-null keys, empty delta schemas and dotted table names (#208).
- Fidelity: `certify` refuses a reference that is not a capture (#168); `shape fidelity` scores an
  all-empty column (#178); `quantile_fidelity` fails on NaN or missing quartiles (#183).
- Others: drift events on a column absent that day (#184) and ramps longer than their window
  (#215); the shape compiler's keys, correlated targets and errors (#206); reference files that
  are not lists (#209); fan-out, FK index, permutation and timeline input checks (#210);
  `assess_fidelity`, the format message and negative `GenerationPlan` seeds (#211); NaN levels of
  the Chow-Liu model (#212).
### Fixed (kernels, Rust, scale; AUD-kernel)

- Scale sinks: `--sink-config memory.max_memory_gb=0.5` no longer builds a 3 GiB string, and numeric
  settings written as text are parsed and checked (#482). The Parquet sink removes the parts an
  earlier, larger run left in a table directory (#483), writes through fresh `O_EXCL` temp files and
  refuses a table directory that leaves the output directory (#288). A Fabric writer that returns
  before reading every batch is an error instead of a hang (#487).
- `fabric_spark`: the driver no longer regenerates the tables the executors make (#484), every
  table's written row count is read back from Delta before success is recorded (#485), and executor
  and `--processes` chunks carry the declared `decimal`/`timestamp` output types (#509).
- Jobs: `shape jobs cancel` from another process stops a running local job (#486); one malformed job
  file no longer breaks `shape jobs list` (#488); a failed or cancelled run reports its own error, not
  a sink's close error (#490); row overrides must name a schema table and be non-negative (#491).
- Kernel: `Kll.update(NaN)` is skipped instead of panicking (#531); restored sketch state and
  snapshots are validated (#532); native and reference agree on text patterns (#534), temporal
  values outside years 1-9999 (#536), `hash_value` of NumPy, pandas and Arrow scalars and nulls
  (#538), negative-scale decimals (#540), nulls in dense inputs (#550) and edge inputs (dictionary
  nulls, zoned timestamps, saturating counts, self-merge; #552); every native function has a twin and
  a stub (#553).
- Generation kernel: keys, slots and word addresses no longer overflow (#549); oversized calls are
  `ValueError`s ("use smaller chunks") instead of interpreter aborts (#548); very wide or non-finite
  hour peaks, days outside the timestamp range and extreme SCD2 gaps are handled (#551).
### Fixed (packaging)

- The core sdist holds only what a build needs (`pyproject.toml`, `src/shape`, `rust/shape-kernel`)
  and the README and licence files; it no longer ships the plans, tests, benchmarks or CI files
  (#246).
- The platform wheels and the sdist carry `THIRD_PARTY_NOTICES_RUST.md`, the licence texts and
  notices of every Rust crate in the kernel (`python scripts/rust_notices.py write|check`) (#248).
- `sqllocks-shape-domains` carries the GeoNames CC-BY-4.0 attribution for its ZIP location data
  (#250).
- Every distribution declares Python 3.11–3.14 classifiers, and the plugins link the repository,
  the issue tracker and their documentation (#254).
- Files are the same bytes on Windows as on Linux (#237): the local registry (`logs/*.jsonl`, refs
  and tags) is written and read as UTF-8 with `\n` line ends, the profile registry index records
  `/`-separated paths, and the Fabric notebook, `.bim` model and recorded tapes, the simulator
  `stats.json` and file-drop manifests, and `shape profile-db --json` no longer get `\r\n` line ends.
### Fixed (security review)

- A compressed `.jsonl.gz` (or `.bz2`, `.zst`, `.lz4`) is depth-checked after decompression, so a
  deeply nested line is refused instead of crashing the reader (#274).
- A bearer token is never sent to another origin: it is not copied onto a redirect, a redirected
  answer from another origin is an error, and the Fabric `Location` and `continuationUri` URLs are
  followed only on the Fabric API host (#275).
- `shape bridge` redacts error messages, warnings and its internal-error log (#277), and job files
  hold no password inside a URI or an error message (#279).
- `shape pack run` refuses a domain name that is a path, so its manifest stays inside
  `--output`, and checks the landing root before creating it (#281).
- A workbook part that inflates more than 100 times above 16 MiB is refused before it is parsed
  (#282); the registry's raw-profile check reads the manifest within the artifact limit (#283).
- `shape_fabric.notebook.generate_notebook` refuses a domain, seed or version that could become
  code in the notebook (#284).
- The scale Parquet sink writes through `O_EXCL` temp files inside a contained table directory, so
  a planted link is never written through (#288).
- The bridge's jobs directory is made private (0700) before a job is written, one unreadable job
  file no longer breaks `job_list`, and job and session ids are matched whole (#289).
- `--log-json` records redact messages, `extra` fields and exceptions (#290); the Fabric UDF
  redacts the errors it returns (#299).
- The fidelity e-mail format pattern runs in linear time (#293).
- The kql scale sink percent-encodes its database name (#295); Spark job and `COPY INTO` checks
  take whole values, and backticks in Spark column names are doubled (#300).
