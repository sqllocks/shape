# Location as Code

Status: experimental.

Status: Canonical implementation contract

`LocationScope` is reusable across capture, generation, validation, quality, scenarios, Policies, Packs, Reference Assets, ETL, streaming, diff and analysis.

Supported selectors: country, state/province, county, city, ZIP/postal code, arbitrary unions/sets, exclusions and weighted distributions. Human input is resolved to canonical geographic records before execution. Ambiguity fails closed.

Address generation preserves country ↔ state ↔ county ↔ city ↔ postal ↔ street ↔ latitude/longitude ↔ timezone coherence. Offline reference assets are versioned, licensed and provenance-bearing. Exact-reference mode is separate from synthetic modes and must be authorized by deployment policy.
