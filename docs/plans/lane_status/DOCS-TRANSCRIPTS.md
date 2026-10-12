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
