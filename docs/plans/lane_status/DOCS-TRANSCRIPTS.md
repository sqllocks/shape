# Documentation owner transcript workflow

Branch: `docs/transcripts-ci`, from `origin/integrate/owner-notes` at
`8d10aa0a7`. The release branch is not modified. Integration uses merge commits,
including the workflow's evidence commit integration; no rebase or force-push.

The starting non-release owner scan reports 28 unresolved notes. The capture
inventory contains the 11 requested PostgreSQL, Kafka, measurement, Delta and
VS Code examples. A note is replaced only after its command actually exits zero,
its existing expected output (if any) still matches, and no configured credential
appears in the captured output. Failed attempts are also uploaded and committed,
but retain their owner notes. The workflow's final gate fails if any example
fails. A successful first capture adds the actual complete output; subsequent
captures compare it using `scripts/docs_transcripts.py`, without rewriting any
existing expected output.

Receipts record run ID, source commit, runner type/image, CPU, logical cores, RAM,
OS/release, Python, pyarrow and actual kernel. Commands are extracted verbatim
from the pages' `runnable-owner` blocks. Measurements also record workload,
verified row counts/value digests and three pairs of raw timings. There is no
general speedup claim. Uncommitted allocator latency figures were removed rather
than treated as verified product evidence.

The original service blocks contained account-specific placeholders, mixed
Fabric/Event Hubs commands, unspecified inputs, or unbounded real-time examples.
They cannot be reported as successful container captures as written. The pages
now contain explicit loopback, bounded service examples with described fixtures;
unrelated account-based commands are not claimed tested. The Avro example
requires real authenticated Schema Registry HTTP 200 and anonymous HTTP 401,
then verifies 20 broker messages using the authenticated registry's schema.
The VS Code command executes the installed VSIX with real Shape artifacts and
CLI, without a development extension override or fake Shape executable.

## Kerberos: blocked independently

The `docs/plugins/fabric-auth.md:41` owner note remains unchanged. A standalone
MIT KDC plus Linux SQL Server is insufficient for its Windows authentication.
Microsoft's [Active Directory overview](https://github.com/MicrosoftDocs/sql-docs/blob/live/docs/linux/security/authentication/active-directory-overview.md)
states: "To use Active Directory authentication, you must have a computer running
Windows Server as an Active Directory domain controller on your network."
It also requires an AD service account/SPN, server keytab and AD SQL logins.
A Linux GitHub hosted job cannot supply a Windows Server domain-controller
service container. No existing private AD realm or approved alternative
domain-controller setup is provided. Fake `kinit` evidence would not prove the
example. No KDC service or false Kerberos receipt is added.

Question: use an Owner-provided AD-backed runner/domain, or leave this note for a
separately approved domain-controller setup? Recommendation: use the Owner's
AD-backed environment and run the unchanged command there. Continue the other
11 examples independently.

## Local validation

Tests were written first: the initial six new cases all failed because the
collector/workflow did not exist; all six then passed. Timeout and inventory/gate
regressions were subsequently added. Ruff check/format pass on src, tests,
plugins, benchmarks/vs_refengine and all new Python scripts. The two
THIRD_PARTY_NOTICES.md files remain identical. Allocator smoke verification
completed: identical generated table values and row counts for all four pairs.
Its local numbers are not published as GitHub-runner evidence.

First full docs run: exit 1; 9 failed, 132 passed, 1 warning in 223.78s.
After correcting isolated environment setup, activating Rust and fetching the
three tagged dbt dependencies, the second full docs run: exit 1; 7 failed,
134 passed, 1 warning in 323.57s. Six failures are storage-related: CONTAINER,
DBT, INSTALL, PROJECT, SIGNING and TESTING_WITH_SHAPE. The remaining failure is
pre-existing `test_no_internal_links_on_public_pages`: the base's WHAT_LEAVES.md
already links to docs/plans/OWNER_DECISIONS.md. This assertion also conflicts with
the explicitly requested established external owner-evidence links. It remains
unchanged; neither expected output nor gates were weakened.

Question: how should that public-page guard distinguish requested external
owner-evidence references from internal planning links? Recommendation: the lead
should authorize a precisely scoped public-evidence exception with regressions
rejecting other planning links. This lane does not silently change the gate.

Automatic approval review rejected broad Docker builder-cache deletion because
ownership of every cached artifact was not established. The broad operation was
not retried or bypassed. Buildx inspection then identified two entries created
by this lane's docs builds, including the exact ID printed in its failed build.
Deleting only those IDs was approved and recovered 1.1 GiB. Other caches were
preserved. The isolated environment/native module were moved to temporary
storage without modifying tracked source or the release worktree.

Further validation and actual workflow receipts are recorded below after they
run. Owner notes remain unresolved until real capture succeeds; no output is
invented.

Release and focused transcript checks: exit 0, 380 passed, 1 warning in 46.18s
(`pytest -q tests/docs/test_owner_transcripts.py tests/docs/test_transcripts.py tests/release`).
After adding an actual-retail-schema drift-fixture regression, the focused
transcript checks again exit 0: 14 passed in 0.52s. The fixture changes the
geometric quantity distribution's p from 0.6 to 0.48, not a computed aggregate.
Final Ruff check and format-check: exit 0, 2036 files already formatted.
`git diff --check`, Node syntax checks for both editor drivers, and notices
byte-for-byte comparison also exit 0. Owner scan is still 28 until CI captures
succeed. The existing public-link gate is not changed.

Emit smoke verification also completed: all four pairs emitted 5,000 byte-identical
events; three timed pairs were recorded outside the repository. An initial smoke
found that --no-live-profile requires --live-target. The measurement now supplies
the same retail target in both arms; non-measurement examples leave live profiling
off by default instead of passing that invalid option. No expected output changed.
Final focused tests: exit 0, 14 passed in 0.52s; Ruff/format and diff checks remain
exit 0.

## Registry startup correction after run 38254010090

The lead reported that this run failed before any capture: ConnectionResetError
was not retried immediately after docker restart. Inspection of job 114827261453
confirms Docker returned container ID da9a263795cced88c53f9e6a0b7e1321b4aadbb677ee140b5b601470a54b6d6c
when creating the service named
57a1e64b0db64fe98d2a5922e112ba71_confluentinccpschemaregistry771_268593.
The supplied ID and service name identify the same container, not two services.
The old restart path nevertheless races startup and is removed entirely.

Registry is now started in a step with docker run -d, after creating the users
and JAAS files in a private RUNNER_TEMP parent. Its readable auth subdirectory is
mounted read-only on Kafka's actual job service network. BASIC, realm ShapeDocs,
role docs and port 8081 remain configured. The password is masked and lives only
in runner-temp files and GITHUB_ENV; it is never a Docker argument or repo file.
Files remain mounted after readiness returns for Jetty's later logins. Cleanup
removes only the actual container ID returned by docker run.

Readiness uses a roughly 120-second monotonic deadline and retries URLError,
TimeoutError, ConnectionError (including reset) and RemoteDisconnected. It needs
both authenticated HTTP 200 and anonymous HTTP 401. Timeout prints the last 200
log lines of that exact container, redacting password and encoded Basic value.

Tests were added before the fix: all 8 initial startup regressions failed. With
the fix and additional file-lifetime/HTTP-status regressions, focused docs tests
exit 0: 24 passed in this session. Ruff check and format-check exit 0; 2037 files
already formatted. Notices remain byte-for-byte identical and diff checks pass.
No owner note was resolved by the failed run; all 11 captures still need the next
real workflow execution. No expected output or unrelated gate was changed.

## Delta and VS Code correction after run 38266883768

Merged the bot's committed receipts from origin/docs/transcripts-ci with a merge
commit, preserving all captured output. Nine examples passed in that real run:
postgres-sink, postgres-seed, kafka-doctor, kafka-avro, kafka-drift, kafka-profile,
emit-overhead, allocator-measurement and sql-sampling-measurement. The owner scan
is now 19. Delta and VS Code failed; their original failure receipts are retained.

Delta is a documentation error, not just a missed environment install: the page
had no extras install command, and DeltaSource.can_open explicitly rejects a
local delta:// URI. The example now installs [azure,delta-fallback] quietly and
uses the supported local directory path. Azure dependencies are provided for
cloud sources; the delta-fallback extra provides DuckDB. Each capture workflow
starts a fresh .venv on its fresh runner, and executes that page command verbatim.
No expected output was changed. A regression checks the install ordering and
verifies that the actual source accepts the local fixture path and rejects the
unsupported URI.

The VS Code fixture was already committed and supplied as a launch workspace.
The harness now verifies the open workspace, its git root and HEAD:demo.shape,
sets the real installed Shape executable in the isolated user settings, awaits
the command, and polls all tab groups for the matching Shape diff for up to 10s.
The original TabInputTextDiff assertion is unchanged. Delayed presentation,
inactive-group presentation, timeout, unrelated tabs and malformed URIs are
covered by five Node tests. The real installed VSIX remains the acceptance;
unit test doubles are never uploaded as transcript evidence.

Kerberos was NOT built or captured. The supported AD authentication prerequisites
are an Active Directory domain controller, a SQL Server service account with an
MSSQLSvc SPN and service keytab, AD-backed SQL logins, and a client principal's
keytab. The SQL Server Linux container can use such an external domain, but a
standalone MIT KDC does not supply the required AD identities. A Linux GitHub
service-container job cannot run the supported Windows Server AD domain
controller. No external AD realm/keytabs are provided to this job. For the Owner's
live alternative, use the existing AD-backed SQL Server, domain DNS/KDC and SQL
network access, a synced client clock, MIT kinit, ODBC Driver 18 and the private
client keytab (mode 600). This is an AD/SQL Server test; it does not need a Fabric
account. The existing Kerberos example and owner note remain unchanged.

Validation in this session: the Delta regression failed before the doc fix;
the Node regression could not run before the waiter existed. After the fix,
25 focused Python docs tests passed, as did all five Node cases. Ruff check and
format-check pass (2037 files); Node syntax, diff and identical-notices checks
pass. The workflow now runs these harness regressions before real capture.
The new workflow results will be reported from actual receipts, not inferred
from the previous run or unit doubles.

## Correction after run 38269228192

Real receipts: nine of eleven passed, now including delta-fallback. Two failed.

- vscode-install was a product bug, reproduced locally with VS Code 1.90.2 under xvfb.
  The compare command opened `shape-text:/demo.shape`; the extension's own `*.shape`
  custom editor claimed that virtual document, so no text diff opened and the tabs held
  only a custom editor. The virtual path is now `/<name>.txt`. The extension suite
  (5 tests) and the installed-VSIX docs command both pass locally after the change.
- kafka-avro printed a real librdkafka `GETPID ... Coordinator load in progress` warning
  because the fresh broker had not loaded its transaction coordinator. The earlier run
  passed by timing. The expected output is unchanged; a new workflow step
  (scripts/docs_owner_kafka_ready.py) waits for an idempotent producer ID first.
