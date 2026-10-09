# Python API guide

Use the Python interface for local profiling, contracts, drift and generation.

Status: available (profiles, contracts and drift); experimental (model and other APIs).

The [generated reference](reference/api.md) documents every top-level name in `shape.__all__`
and every explicitly exported module surface in `src/shape`. It is built with mkdocstrings
from the code. CI fails when an exported callable lacks a docstring or is absent from the
reference inventory. Constants are listed with their exporting module.

Start with the [profile tutorial](tutorials/01-first-profile.md), then
[contracts](tutorials/04-contract.md) and [generation](tutorials/05-generate-profile.md).
The top-level names load lazily. A profile differs from a model; see [Models](MODELS.md).

The [API stability page](API_STABILITY.md) describes the future 1.x policy.

## Related

[Concepts](CONCEPTS.md) · [Known limitations](KNOWN_LIMITATIONS.md)
