# API Stability — Shape by SQLLocks

Shape 1.0 freezes the versioned specification and the public interfaces identified in `docs/specs/SHAPE_2.md`.

Stable interfaces follow semantic-versioning compatibility through the 1.x line. Additive optional behavior is allowed; silent semantic changes are not. Experimental/internal modules are not covered unless explicitly promoted to Stable.

Artifact readers fail closed on unknown mandatory capabilities and tolerate unknown optional extensions only when they can be ignored safely.

The generation spec format (its JSON Schema, the stability promise within 1.x and the edit API) is described in `docs/GENERATION_SPEC.md`.
