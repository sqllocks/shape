# Independent persistent-runner audit

Audit scope: read-only worker.py, collection-proof.py, test-env.sh and W9-08-91ef6200266b/collection/proof.json. No full tests, Docker, bootstrap or source mutations were performed.

## Corpus selection: APPROVED

The exact selected node-ID sets match: full-core18867 = make-light18768 + make-heavy52 + extra-joint-heavy1 + content46. Each parsed count also matches pytest’s own selected-count summary; missing/unexpected sets are empty. Native coverage is therefore complete when unchanged make check and all three native supplements pass. Full Python core explicitly ignores only Fabric and includes content/heavy/nightly offline cases; Fabric’s whole directory runs separately in each kernel with its locked compatible dependency group. Default testpaths is tests; plugin Wanted suites remain separate focused both-kernel and hosted-plugin evidence.

The shell forces SHAPE_KERNEL=rust for native jobs and overrides it for the two Python commands. Docs/benchmark groups remain separate from Fabric. make check retains its existing coverage86, heavy/native/Python kernel and Cargo gates. All test subprocesses are under the required outer reaper; collection commands also use it. Temporary retention is failed-only, already used by existing docs CI, and changes no test selection/assertion.

## Binding gap: correction required for subsequent loaded worker

The audited snapshot helper checked only tracked diffs in src/tests/plugins/scripts/rust/lock/Makefile and ignored full status. That could miss uncommitted mkdocs.yml, product documentation transcript, normative requirements or other configuration changes. Lead acknowledged and is extending to all tracked paths except explicit lane-evidence allowlist; current metadata acceptance will additionally require clean recorded before/after status at the same head.

Untracked guards should also reject project-root executable/config files such as conftest.py, pytest.ini, tox.ini, .coveragerc and sitecustomize.py, or use an explicit harmless-generated-artifact allowlist. Current metadata recorded before status was clean.

## Control boundary and retry preservation

Audited worker reads pause/stop/lane only before its for-loop, so a mid-lane pause request is not honored before the next job. Re-read control at job boundaries, or coordinate via lock/explicit idle boundary. No current pytest interruption is needed.

Same-head retries overwrite the named job entry/log/cache log. Preserve previous attempts in versioned directories or archive the failed/interrupted entry and logs before unpausing. Current initial interrupted attempt was manually preserved by the lead; do not infer it passed.

## Additional setup provenance

The old empty W9-10 not-json.tmp comes from existing tests/streaming/emit/test_curves.py::test_unknown_names_and_unreadable_files. The test creates this relative file and unlinks in finally. A stopped attempt can leave it behind; archive/quarantine the known generated file outside checkout before clean binding, with no test edit.

Persistent workspace storage, per-job head/tree/exit/zombie logs, failure pause and the corpus decomposition otherwise meet the proposed plan. Overall acceptance remains contingent on actual zero exits, clean binding and the above orchestration corrections, not collection alone.

## Updated worker review — APPROVED

Re-audited updated on-disk worker.py, SHA256 `256500dea0342c7a8e9e90568fec69a4bb0f1a11ccc20a9208ddd119699a1e56`. Parsed its AST without executing it. The tracked guard now covers all paths except explicit lane evidence. Untracked paths are rejected except lane evidence and the known empty not-json.tmp artifact. Control is re-read at each job boundary. A retry preserves the previous entry, logs and collection outputs in a unique attempt directory before replacement. These corrections resolve the three prior findings for the next worker load.

Current PID5354 remains untouched; it still runs previous loaded code. Metadata acceptance separately requires its recorded clean before/after status at the frozen head. No tests, Docker jobs or source changes were performed for this review.

Minor traceability note: preserved entry.log retains the original log pathname subsequently reused by retry; the archive directory plus basename identifies the preserved bytes. An explicit archived-artifact path map would make automated consumers safer. This is not evidence loss and does not block the updated plan.
