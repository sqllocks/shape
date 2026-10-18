# Public B state

Main: d0e64d53eecfe03b29caa5dd111598f3b9a9ace2. Scratch: lead/public-b-2026-10-18.
Original own remote branches contain unsigned historical commits. Clean `-finish` continuations preserve those branches without force pushes, and apply only the assigned lane changes with new DCO-certified commits.

| Issue | Branch / head | Stage | Next action |
|---|---|---|---|
| #20 | no product branch | PARKED access + pass-rule conflict evidence | Actions artifacts still proxy403 after resume; #6 record contains exact local parity reproduction; no tolerance/recording edits |
| #6 | lane/W9-05 afbf4c5 | PARKED | Owner decision on unsupported nested/error-equivalence conflict; issue comment posted |
| #9 | lane/W9-08-finish 5033951 | common gates | resume_metadata; independent review by resume_eventhouse |
| #10 | lane/W9-09 | tests-first implementation | formats; Azure registry item approved by Owner comment |
| #11 | lane/W9-10-finish 5be9f04 | common gates + review fixes | resume_dbt; resume_metadata found ratio metric/exposure dependency omissions |
| #12 | lane/W9-11-finish | recovered implementation uncommitted | resume_eventhouse; tests46 pass, common gates queued |
| #13 | lane/W9-12-finish | recovered implementation uncommitted | root; focused171 pass, sempy0.14.2 signatures verified, conformance added |
| #14 | old lane/W9-13 1249af9 (journal only) | PARKED | missing merged W9-01; prior journal also documents invalid decimal specification boundaries |
| #17 | lane/W9-16-finish | recovered implementation uncommitted | resume_iceberg; interface reconciliation, tests105 pending |

No PRs/merges yet. Full suites serialize under /tmp/public-b-full-check.lock. All tests through $HOME/bin/reap.py. Last zombie observation19, below hard stop1000. Separate reviewer spawn blocked by platform thread cap; existing independent lane agents cross-review.

After context restart reread attachment and this state; inspect worktrees/ledgers before repeating work. Logs/status live per lane and /tmp. No passing CI or live result claimed.
