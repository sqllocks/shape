# HC-behavior: Shape's own behavior framework (issue #45, lane 1 of 4)

Lane branch `lane/HC-behavior` (from the lead's integration tree). Builder notes; the lead merges.
No issue was commented on, labelled or closed. Never edited §11, §2.3 or `$SPINDLE_ROOT`.

## Where HC-domain starts

**The API sketch and docs are first in the history: `docs/plugins/behavior.md`** (module
document format, state types, Python API, event schema, extension point, GMF import, CLI). It is
the contract; the implementation follows in later commits on this branch. Plugin:
`plugins/shape-behavior` (distribution `sqllocks-shape-behavior`, package `shape_behavior`).

(Sections below are filled in as the work lands.)
