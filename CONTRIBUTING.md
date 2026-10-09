# Contributing

Status: available.

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

You contribute code and documentation under MIT. Sign off every commit with a
`Signed-off-by: Your Name <your email>` trailer. The sign-off certifies the
[Developer Certificate of Origin](https://developercertificate.org/). Use your own identity.
The DCO workflow checks every commit in a pull request.

## Prepare a change

Open an issue for a platform, domain or feature request. Describe the input, expected output
and a small reproducible case. Do not include credentials or production rows. Maintainers
review behavior against specifications and conformance tests. See [governance](GOVERNANCE.md).

Keep deterministic behavior, sensitivity and provenance propagation, and fail-closed input
handling. Core must not gain a mandatory network dependency. Add a regression test for a bug
and conformance tests for new normative behavior. Document the changed behavior.

## Local development

You need Python 3.11 or newer and Rust stable 1.85 or newer to build the native kernel.
The Makefile defines bootstrap and check targets. Review the targets before running them.
[Owner: contributor tooling — full development bootstrap and full repository checks have
not been executed as documentation examples in the docs environment.]

The docs workflow installs the core and local domains and databases plugins from this checkout,
runs the tutorial harness and API coverage checks, and builds MkDocs in strict mode. It also
checks wording and links. Use [the documentation style guide](docs/contributing/DOCS_STYLE.md)
for a new page. The harness runs only blocks explicitly marked runnable, with their shown output.

A new Fabric API, item type or runtime needs its entry in [the platform inventory](docs/FABRIC_PLATFORM.md)
with the shipped behavior and a source. Keep account-dependent execution claims out of docs.

## Public interfaces

The package's `shape.__all__` is the top-level public Python surface. Generated API documentation
and CI cover every name and its docstring. Modules with a separate `__all__` are also included.
The generation spec edit and schema modules have a compatibility baseline in
`tests/api/stable_api_baseline.json`. An additive change updates that baseline; a breaking
change needs a new major version. Do not erase a compatibility failure by editing the baseline.

See [scope and limits](docs/NOT_BUILDING.md) before proposing a new feature.

## Review

A pull request explains the concrete problem, changed behavior, tests and limitations. Keep
code, tests and affected docs in the same change. Maintainers decide whether the change is ready;
the repository owner merges. Security changes may tighten input handling. Report security
issues through [the security policy](SECURITY.md).

## Related

[Documentation style](docs/contributing/DOCS_STYLE.md) · [Governance](GOVERNANCE.md) ·
[Future CLI policy](docs/CLI_STABILITY.md) · [Future API policy](docs/API_STABILITY.md)
