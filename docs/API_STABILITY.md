# API Stability — Shape by SQLLocks 1.0 GA

Shape 1.0 freezes the versioned specification and the public interfaces identified in `docs/specs/SHAPE_1_0_GA.md`.

Stable interfaces follow semantic-versioning compatibility through the 1.x line. Additive optional behavior is allowed; silent semantic changes are not. Experimental/internal modules are not covered unless explicitly promoted to Stable.

Artifact readers fail closed on unknown mandatory capabilities and tolerate unknown optional extensions only when they can be ignored safely.
