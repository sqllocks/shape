# API Stability — Shape by SQLLocks

Shape 1.0 freezes the versioned specification and the public interfaces identified in `docs/specs/SHAPE_2.md`.

Stable interfaces follow semantic-versioning compatibility through the 1.x line. Additive optional behavior is allowed; silent semantic changes are not. Experimental/internal modules are not covered unless explicitly promoted to Stable.

Artifact readers fail closed on unknown mandatory capabilities and tolerate unknown optional extensions only when they can be ignored safely.

Persisted files follow [the state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md): every file declares `format` and an integer `version`, every 1.x and later release reads every format version ever released, and a file from a newer release fails naming the minimum Shape release that reads it.

Plugin API v1 has its own promise, with the per-group rules and the deprecation process: `docs/plugins/stability.md`.
