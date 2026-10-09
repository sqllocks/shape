# Governance

Status: available.

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

SQLLocks maintains Shape. The repository owner reviews and merges contributions.
[Owner: repository owner — confirm the named maintainer roster and review delegation.]

## Decisions

You propose a change in an issue or pull request with the problem, expected behavior and
supporting code or tests. Maintainers review public behavior against versioned specifications
and conformance tests. Accepted decisions are recorded in the issue or pull request. A breaking
specification change needs a version and migration path. Security fixes may tighten behavior.

## Platform requests

Use the platform request template. Describe whether you need reads, writes or exports, the
URI and authentication model, and a local fixture or account-free test strategy. Maintainers
check dependencies, credential handling, licensing and tests before accepting a new adapter.
A request is not a delivery commitment. Shipped platform behavior must have documentation
that matches the code; cloud targets need owner validation before commands are published.

## Domain requests

Use the domain request template. Explain tables, keys, relationships, scales and reference-data
sources. Reference data must permit redistribution and include licence and attribution evidence.
Maintainers review schema tests, deterministic generation and the documented sources.
An unproved calibration claim stays an owner placeholder.

## Participation

Follow the [code of conduct](CODE_OF_CONDUCT.md). Sign off contributions under the
[contributing policy](CONTRIBUTING.md). Public support uses support@shapedata.ai;
security reports follow [SECURITY.md](SECURITY.md).
