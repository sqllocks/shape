# Public A state

Scratch branch: lead/public-a-2026-10-18
Base: d0e64d53eecfe03b29caa5dd111598f3b9a9ace2

| Issue | Branch | SHA / PR | Stage / next action |
|---|---|---|---|
| #4 W9-03 | lane/W9-03 | cc3ac1e; no PR | Prior implementation retained, Owner-amended DDL corpus updates and independent review underway. |
| #5 W9-04 | lane/W9-04 | not committed; no PR | Constraint derivation first; sink changes await W9-03. |
| #7 W9-06 | not started | none | Wait sink ownership from W9-03, then implement native nested maps. |
| #8 W9-07 | lane/W9-07 | prior 07498ab; no PR | Adopt prior implementation; verify Owner amendments; constraints depend W9-04. |
| #2 W9-01 | lane/W9-01 | prior 88a46ab; no PR | Prior source implementation retained; review/tests, DCO repair preserving remote backup. |
| #3 W9-02 | lane/W9-02 | uncommitted; no PR | Cloud source type tests underway; needs W9-01 model merged. |
| #16 W9-15 | prior lane/W9-15 | prior 4582655; no PR | Prior branch journal only; implementation starts after W9-01 merge. |

No implementation merged this run. W9-03 alleged signed int8 blocker was already resolved by Owner comments; no question is parked. W9-07 Owner comments remove upsert and rename requirements. W9-15 Owner authorizes bounded cursor fallback for unsupported Arrow types.

Existing main Nightly red is assigned to Lead B (#20); no changes made there.

Environment: task-local pinned uv 0.8.17 at /tmp/public-a-uv/bin/uv; root bootstrap running. Every test uses $HOME/bin/reap.py. Initial zombie count 2; observed later 9, below hard stop.

Next: complete baseline setup and verify existing source/type lanes, independent reviews, then PRs and exact-head CI.
