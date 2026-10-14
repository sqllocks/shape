# W-claims lane status

Status: resumed under the Owner's explicit resolution; draft-only claim tables prepared.

## Original question (resolved)

How should W-claims record claim tables for the five currently experimental pages in docs/AVAILABLE.md while keeping scripts/check_available.py passing and leaving every page status unchanged? Should the ledger/checker gain an explicit draft-table state in a separately authorized change, or should these tables wait outside the ledger until a later promotion lane?

## Evidence and scope at the initial stop

The prompt requires claim tables for CANARIES, REPRODUCIBILITY, DETERMINISM, QUICKSTART, SIGNING and VERIFY, says “Do not mark any page `Status: available`” and “tables only”, and requires scripts/check_available.py to pass. On origin/lane/W0-available, five of these pages say `Status: experimental`; only QUICKSTART says available.

`scripts/check_available.py:315` iterates every parsed ledger section and rejects a page whose status is not available, emitting “a page once available is not demoted”. This rejection applies even to an `open W-canary-2` row. The promotion plan's section 1.3 likewise specifies one section per available page and rejects ledger pages saying experimental. Its section 4.1 splits canary claim tables (W-canary-2) from promotion (W-canary-3); sections 4.4–4.6 split claim tables from their other promotion prerequisites. The requirements therefore cannot all be met by a tables-only change on the specified base.

No feature, test, ledger, page status, checker, release note, or plan file was changed. Only this blocker report is committed. The lead confirmed there is no existing authorization resolving the contradiction and instructed this lane to stop and push the exact question.

## Commands and results before the Owner resolution

- `cd /workspace/shape && git remote get-url origin && git config user.name && git config user.email` — exit 0; verified origin https://github.com/sqllocks/shape.git and copied repository author identity.
- Read the cloud environment runtime skill and networking reference; environment status reports connected/running. `cat /etc/codex/network-policy.json` — exit 0.
- `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-claims` — exit 0.
- Set isolated clone origin and repository author identity with `git remote set-url` and `git config` — exit 0.
- `git fetch origin` — exit 0.
- `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093` — exit 0.
- `git checkout -B lane/W-claims origin/lane/W0-available` — exit 0.
- Read CLAUDE.md, CONTRIBUTING.md, docs/AVAILABLE.md, scripts/check_available.py, Makefile, and the fetched plan's Decisions, 1.3, 4.1, 4.4, 4.5 and 4.6 sections with `cat`, `rg`, `git show` and `sed` — exit 0. No AGENTS.md was found by the tracked-file search.
- `rg -n '^Status:|claim:' docs/CANARIES.md docs/REPRODUCIBILITY.md docs/DETERMINISM.md docs/QUICKSTART.md docs/SIGNING.md docs/VERIFY.md` — exit 0; five experimental target pages confirmed.
- `python3 scripts/check_available.py` on the unchanged ledger — exit 0, `AVAILABLE: 0 problem(s)`.
- The same check after temporarily appending one syntactically valid CANARIES section with an `open W-canary-2` row — exit 1, `AVAILABLE: docs/CANARIES.md: P-BAR 1: a page once available is not demoted`; one problem. A Python `finally` restored the original ledger. The reproduction wrapper exited 0.

Red evidence above demonstrates the requirement contradiction, not a new behavior test. Setup, lint, mypy, focused tests and `make check` were not run because the prompt explicitly requires stopping rather than guessing when the plan contradicts code. No implementation was authorized after discovering this blocker.

Finish verification: `git diff --check`, `git status --short`, `git add docs/plans/lane_status/W-claims.md`, `git commit -s -m 'W-claims: record draft ledger contradiction and stop question'`, `git push -u origin lane/W-claims`, `git rev-parse HEAD`, and `git ls-remote origin refs/heads/lane/W-claims` all exited 0. The initial pushed report commit was `44f59c4dbde19ed0ea77574bb0e087b87b6b7bd1`; local and remote SHAs matched. This finish-results addendum is committed separately, then the lane push and remote SHA comparison are repeated; their definitive result is included in the final agent report.


## Owner resolution and resumed scope

The Owner explicitly resolved the contradiction: keep `docs/AVAILABLE.md` and its checker unchanged, keep page statuses unchanged, and put all six pages' claim-to-test mappings in `docs/plans/lane_status/W-claims-drafts.md`. Use the exact five-column ledger format, show actual execution outcomes in adjacent evidence tables, and list source sentences lacking full proving tests separately. This resolution supersedes the original instruction to edit the public ledger and the original stop condition for that contradiction.

The draft contains 195 one-sentence claim mappings: CANARIES 20, REPRODUCIBILITY 37, DETERMINISM 2, QUICKSTART 7, SIGNING 36, VERIFY 93. Supplemental proving files cover generator-version and identifier replay, file hashes, credential permissions, utility, quarantine and enum auditing. No new behavior was added or changed, so no implementation red test is required. The earlier failed draft-section experiment remains evidence explaining why the Owner selected internal draft storage.

The tables move into `docs/AVAILABLE.md` only with each page's promotion work package: CANARIES with W-canary-3; REPRODUCIBILITY after W-repro-2 and W-repro-3; DETERMINISM after W-det-2 and W-det-3; QUICKSTART after W-qs-1 and W-qs-2; SIGNING after W-sign-1 and W-sign-3; VERIFY after W-verify-1 and W-verify-3. The respective promotion package must add matching claim anchors, declared Covers, experimental Python fences, required release notes and complete missing proof before transfer. Existing QUICKSTART legacy exemption is not removed by this lane.

Unsupported source sentences are listed under each page instead of being presented as proven. Important examples include FirstPerParent scan cost/cache reuse, streaming and sink guarantees, exact internal digest construction, all-platform byte identity, real Key Vault, the full command verification matrix, migration/vault behavior, some precise caps/formulas and contract quality integration. Operational advice is identified as guidance. Fake resolvers never count as real outside-service evidence.

## Resumed setup and command record

All subsequent commands ran in the same isolated checkout and existing lane; neither the checkout nor branch was reset.

- `python3.11 -m venv .venv` — exit 127; that interpreter is absent.
- `python3 --version` — exit 0, Python 3.12.14. CONTRIBUTING.md and package metadata support Python 3.11 or newer, so Python 3.12 is used for this isolated venv.
- `python3 -m venv .venv` — exit 0.
- Setup exports `CARGO_HOME=/workspace/.onboarding/cargo`, `RUSTUP_HOME=/workspace/.onboarding/rustup`, and prepends `/workspace/.onboarding/cargo/bin` to PATH; this uses the installed Rust toolchain without importing source from any other checkout.
- Read the remaining page prose and relevant test assertions with `sed`, `cat`, `rg` and a Python fenced-block filter — successful reads exited 0; several optional glob searches exited 2 because their guessed supplemental file patterns did not exist. The corrected `rg --files tests` search identified the actual files; no nonexistent proof paths were included.
- Temporary draft-building/rendering Python helpers outside the repository — exit 0. AST validation confirms all 195 claims are sentences and every cited proof function exists. Exact parametrized ids are checked by pytest execution.


Completed setup: `make bootstrap` exited 0; `pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` exited 0. `pip install 'scikit-learn>=1.5.1,<2'` exited 0 to execute utility-gate proofs. Installed Shape and native kernel paths were checked with `.venv/bin/python -c` and both resolve inside this isolated checkout (exit 0).

The temporary validation runner records each command, its exit code and its output log. Focused tests include all originally named test files and their nearby gate, quarantine, credential and ledger checks; supplemental replay/writer proofs and only the five relevant reference-page cases are selected by exact node id. The six tutorial cases are run through their full file. No tests are skipped or xfailed by this change, no tolerance/gate is changed, and no golden fixtures are modified.

The pasted-table parser validation succeeded with six sections and no malformed rows (exit 0). An early `git diff --check` detected a new blank line at EOF (exit 2); it was removed before commit. Public availability validation again exited 0. The final checks below supersede preliminary counts/results.

Focused validation initially exited 1: **891 passed, 1 failed**, no skips. The failure was `tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]`: the original setup's required plugin list did not include the tutorial's documented DuckDB prerequisites. `pip install --no-deps -e plugins/shape-databases` exited 0; the first targeted rerun exited 1 after successfully generating DuckDB data, because the read-side integrations plugin was still missing. The tutorial prerequisites explicitly require both databases and integrations plugins. The latter is installed from this same checkout, and only the failed tutorial is rerun again. No source, expected transcript, fixture, or tolerance is changed to repair environment prerequisites. The latest outcome is used in the draft's adjacent execution table; initial failures remain in this report.


`pip install --no-deps -e plugins/shape-integrations` exited 0. The final targeted rerun of `tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]` exited 0: **1 passed in 7.30s**. All 195 draft claims now have actually executed passing proofs, including all five relevant reference-page examples and all six tutorial cases. The full focused selection had 892 unique cases; its 891 initial successes plus the repaired tutorial cover them all. Both prerequisite failures are retained above for transparency.

Final format validation extracted the six ledger sections, parsed them with the unchanged `check_available.parse`, asserted exactly 195 rows and every row `done` — exit 0. `git diff --check` — exit 0. `git diff --exit-code origin/lane/W0-available -- docs/AVAILABLE.md scripts/check_available.py docs/CANARIES.md docs/REPRODUCIBILITY.md docs/DETERMINISM.md docs/QUICKSTART.md docs/SIGNING.md docs/VERIFY.md` — exit 0, proving the public ledger, checker and all six public pages are unchanged.


Two non-mutating `.venv/bin/py-spy dump --pid <this lane's pytest PID>` diagnostics exited 0 while make's core stage ran. The first sampled public-API documentation signature checks; the second sampled active scikit-learn mixture-model work in fidelity tests. These show actual full-suite progress and are not evidence of a deadlock. The once-required make run has a 600-second process-group wall-clock bound; if it expires, that result is reported as an unfinished full suite rather than a pass or an invented coverage verdict.

## Final validation and recovery

Recovery inspected the saved validation JSON, focused JUnit XML, rerun logs, the full make log and process list; no live W-claims test process remained. The interrupted runner never wrote a make exit record. `make check` ran once and reached approximately 29% of its core test stage, with failures/errors before pytest reported `INTERNALERROR` and `ValueError('I/O operation on closed file.')`; its last line is `make: *** [Makefile:26: check] Error 1`. The make process numeric exit is **not captured**, and the full suite is **incomplete/failed**, not passed. There is no timeout marker, so this is not reported as a confirmed 600-second timeout. The original log remains `/workspace/prompt-runs/W-claims-make-check.log`. The log has no completed test summary, individual failure explanations or coverage verdict, so those cannot be attributed or invented. The passing focused proofs are the meaningful completed validation for these documentation-only drafts.

Completed commands from the saved validation record:

- Focused pytest selection (full exact argv retained in `/workspace/prompt-runs/W-claims-validation.json`, including all required files and every supplemental proof node) — exit 1 initially, 891 passed and the one tutorial prerequisite failure detailed above.
- `ruff check src tests plugins benchmarks/vs_refengine scripts` — exit 0; log `/workspace/prompt-runs/W-claims-ruff-check.log`.
- `ruff format --check src tests plugins benchmarks/vs_refengine scripts` — exit 0; log `/workspace/prompt-runs/W-claims-ruff-format.log`.
- `mypy` — exit 0; log `/workspace/prompt-runs/W-claims-mypy.log`.
- `python scripts/check_available.py` — exit 0; log `/workspace/prompt-runs/W-claims-available.log`.
- Final DuckDB tutorial rerun after both documented prerequisites were installed — exit 0, 1 passed; combined unique focused cases: 892 passed.

Only `docs/plans/lane_status/W-claims.md` and `docs/plans/lane_status/W-claims-drafts.md` are changed. No public page, checker, ledger, promotion plan, source, test or regression fixture is changed. Open questions: none; unsupported sentences remain explicitly listed for future promotion work.
