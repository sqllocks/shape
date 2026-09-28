# GA qualification — gates 1 and 3

## Gate 1: hardest generation combination

Implemented a vectorized relational generation path for deterministic composite primary keys, skew-capable foreign-key indices and composite FK materialization. Address generation now has a high-throughput dictionary-encoded mode that preserves coherent city/county/state/postal/country/lat/long/reference identity without materializing millions of duplicate Python strings.

The qualification workload simultaneously generates:
- two-part composite primary keys;
- two-part composite foreign keys with referential-integrity verification;
- weighted multi-ZIP LocationScope;
- coherent address/geospatial fields with latitude/longitude;
- correlated income/spend joint distributions;
- temporal event values;
- deterministic seeded replay.

Three-run 1M-row median: **1,062,602 rows/sec** — **1M/sec gate PASS**.

The single-allocation 10M workload is intentionally not the production execution model. Its last three-run median was **916,031 rows/sec**, below the 1M/sec target on this host, while earlier individual 10M runs exceeded 1M/sec. Production-scale 10M/100M/1B generation should use bounded chunks/streaming to cap memory. A long-running 100M/1B benchmark cannot be truthfully completed inside this execution window; the qualification runner remains in `rq/`.

Deterministic replay: **PASS**.

## Gate 3: security qualification

Added and tested:
- recursive structure/depth/container/string resource limits;
- credential/secret-pattern detection before artifact writing;
- classification downgrade checks and explicit sanitized-derivative provenance;
- artifact checksum/tamper rejection;
- path traversal rejection;
- duplicate-member rejection;
- unexpected-member rejection;
- decompression-ratio limits;
- existing AES-256-GCM authenticated encryption;
- existing Ed25519 signing/verification;
- query injection/adversarial input tests;
- 5,000 random query strings;
- 2,000 randomized nested structures;
- 500 malformed artifact fuzz cases.

Full local regression after security changes: **177 passed**.
Requirement registry: **73/73**.
Streaming fuzz: **100/100**.
Platform performance suite: **12/12 gates passed**.
Basic repository secret scan: **passed after test-fixture cleanup**.

## External security evidence still required

A true GA security sign-off additionally requires current external dependency/CVE databases, formal independent penetration testing/red-team review, real identity/RBAC infrastructure, HSM/KMS-backed production keys, signed release provenance, and deployment-specific controls/accreditation appropriate to any environment actually handling SECRET/TOP SECRET information. Local code hardening cannot substitute for those operational controls.
