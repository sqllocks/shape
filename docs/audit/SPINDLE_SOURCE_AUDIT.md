# Spindle Source-Level Audit

Upstream: `sqllocks/spindle` main. Repository-native inspection completed 2026-09-28.

## License
Spindle source is MIT licensed; copied/substantially derived portions require retention of its MIT copyright/permission notice. Spindle's bundled geographic reference data has separate third-party attribution.

## Disposition
| Upstream surface | Shape disposition |
|---|---|
| domains + shared reference data | REDESIGN as versioned Domain Packs/Reference Assets |
| retail coherent address `record_sample` + `record_field` for city/state/zip/lat/lng | REIMPLEMENT + EXPAND through AddressPack/LocationScope |
| engine strategies: composite FK, computed, conditional, correlated, derived, distribution, empirical, enum, faker, first-per-parent, FK, formula, lifecycle and related strategies | REIMPLEMENT behind GenerationPlan |
| chunk worker/generator, scale/spark routers | REDESIGN around Arrow batches and benchmark-driven execution |
| correlation | REIMPLEMENT as dependency evidence |
| inference profiler/advanced/tiered profiler/comparator/masker/schema builder/profile store/safe profile/validator | SUPERSEDE with Capture/Shape/Diff/Policy/Privacy; preserve edge cases |
| incremental continue/time-travel | SUPERSEDE with ShapeSeries/History/Scenario |
| streaming envelope/rate limiter/anomaly/multi-writer/streamer/sinks | REIMPLEMENT as true streaming/windows/watermarks/backpressure/checkpoints |
| chaos | REIMPLEMENT as Scenario/failure injection |
| Fabric/output sinks | REIMPLEMENT as connectors/plugins outside core |
| packs/presets/manifests | REDESIGN as signed/versioned Packs |
| MCP bridge | PLANNED adapter, not core |

## Address parity
Spindle's retail address samples a US ZIP record and reads correlated state, ZIP, latitude and longitude from that same record. Shape preserves that invariant and adds county, country, timezone, canonical IDs, weighted scopes, exclusions and provenance.

## Compatibility target
Preserve useful semantics for sequence, enum/weighted enum, distributions, empirical, faker/domain providers, pattern/formula/computed, correlated, derived, conditional, FK/composite/self references, first-per-parent, lifecycle, temporal, reference data, record sample/field and deterministic seeds. API/class-name compatibility is not required.

## Conclusion
No inspected Spindle capability requires changing locked Shape architecture. Its useful behavior maps to Packs, GenerationPlan, History, Streaming, Scenario, Connector and Policy primitives.
