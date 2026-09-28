# Shape by SQLLocks 1.0.0 baseline qualification

The authoritative tree uses the torture snapshot as the functional base and the final branding snapshot for naming. All source archives remain unmodified. This report records this baseline session; root-level manifests and earlier qualification documents are historical evidence, not current-tree checksums or new external qualification claims.

## Four-way reconciliation

| Snapshot | Files (excluding directory entries) | Non-cache files |
|---|---:|---:|
| GA workstreams 1–6 | 465 | 465 |
| Future A–AJ | 781 | 490 |
| A–AJ torture | 789 | 496 |
| SQLLocks branding | 498 | 498 |

465 paths occur in all four archives. 301 paths vary in content across snapshots, including 291 cache paths and 10 source/documentation paths. No files from either earlier snapshot are missing from the torture snapshot, so no historical recovery was needed. The torture archive has two unique cache files; the branding archive adds NAMING_MIGRATION.json and docs/BRANDING.md. There are 293 cache paths absent from the branding archive; these are intentional exclusions, not lost implementation. All 498 non-cache source paths in the union are retained.

The nine torture-to-branding content changes are exclusively branding/packaging and were applied. The tenth conflict is src/shape/privacy/__init__.py, which adds the newer A–AJ privacy exports in the future/torture snapshots; those exports are retained. Individual archive inventories include path, size, SHA-256 and snapshot name. comparison.json and exclusions.json list every difference and cache exclusion. Historical logs had random temporary and checkout paths replaced; sanitized.json records the affected files. Earlier manifests retain their original snapshot hashes and are not rewritten to claim they describe this final tree.

## Real fixes made during this session

- Declare NumPy; use pytest importlib collection to support duplicate test basenames.
- Replace Linux-only plugin test commands with the active Python interpreter.
- Correct the requirement-reference regex and register 16 requirements already present in the specifications (89 total). Registration is not certification.
- Apply Ruff formatting and safe lint fixes; make wildcard imports and intended public exports explicit, retaining the final existing binding for duplicate exports. Preserve enum string behavior and immutable policy defaults. Use precise test exception assertions and explicit zip truncation semantics.
- Harden fuzz runners so unexpected archive exceptions and streaming failures cannot silently report success.
- Repair release workflow YAML, Event Hubs secret gating, missing release test dependencies and dependency auditing of the installed product. Manual release workflows remain undispatched.
- Add the standard Apache-2.0 license text consistent with the archive metadata; include specification resources in wheels and source/test/docs/workflow/evidence files in sdists.
- Update vulnerable dependency ranges to PyArrow >=23.0.1,<24 and Cryptography >=50,<51. Upgrade the qualification environment's pip. No vulnerability ignores were added.
- Add the optional YAML extra, installation command, and generated-cache ignore rules.

## Feature presence and validation

GA 1–6 source/tests/specifications/workflows, A–AJ local implementations, all torture infrastructure and evidence, and the final SQLLocks identity are retained. feature-presence.json lists actual source paths and test imports for each implementation area; docs/FUTURE_ROADMAP_A_AJ.md distinguishes implemented primitives from unfinished product/integration work. It is not a claim that the entire roadmap is production-complete.

Python 3.12 on Windows: **593 collected, 593 passed, 0 failed, 0 skipped, 2 expected duplicate-ZIP warnings**. The earlier 586 count differs by seven cases: the new environment executes the seven PyArrow kernel/type cases that previously could be skipped. The torture subset is **370 passed**, covering 134 module imports plus 134 public-surface introspections and 102 cross-feature/replay/deep-lineage cases. The deep-lineage test exercises 100,000 nodes. All deterministic and security tests in the complete suite pass.

Security fuzz: 500 structure and 50 archive trials; zero failures. Streaming fuzz: 100 trials; zero failures. Connector resilience: 1,020,000 deliveries, 1,000,000 unique projections, 20,000 suppressed duplicates/stale deliveries, eight partitions and four reconnect attempts. Kafka/Event Hubs simulated decoding: 100,000 rows each and zero drift. Installed SDK availability does not mean a live broker was tested. The local 200,000-row benchmark ran successfully; timings are in local-results.json and do not establish endurance or hardware-independent throughput.

Requirement registry, secret checks, compilation, Ruff, dependency consistency and built-in artifact/capture/generation conformance pass. Expanded credential scanning found only reviewed synthetic markers in security tests. The final dependency audit reports no known vulnerabilities among audited dependencies; the unpublished sqllocks-shape distribution itself is not present in the advisory service and is covered by repository checks instead.

Wheel and sdist builds and Twine metadata validation pass. Wheel inventory contains all source modules and specification resources; sdist includes tests, workflows and LICENSE. A fresh environment installs the wheel and passes import shape, shape version (1.0.0, specification 1.0, artifact format 1), shape doctor and shape conformance. YAML is optional and reports null in doctor when the extra is not installed. Package hashes in package-results.json identify the tested builds; later report-only additions can change the final sdist hash without changing runtime code.

## Remote verification

The upload preserves the legitimate initialization commit and creates one current baseline commit, without fabricated historical commits. The placeholder README is replaced by README.md. The final main tree must be compared by path, Git blob SHA and byte size against the authoritative source manifest; the final chat report records its SHA, tree comparison and actual Actions status. This document alone does not assert that a push or remote Actions run has completed.

## External gates

Not executed locally: Linux/macOS and other Python versions (GitHub matrix results reported separately), live Kafka/Event Hubs and production failover, Spark/Flink clusters, Fabric/ADF, production Hub HA/failover, cloud KMS/HSM, cross-domain classified deployment, 100M/1B generation endurance, multi-day streaming soak, signing identities and government accreditation/certification/FedRAMP or classified-system authorization. Local primitives and tests do not constitute any such authorization. No PyPI publication, repository visibility change or public release is part of this baseline.
