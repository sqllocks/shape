# Public B state

Main: d0e64d53eecfe03b29caa5dd111598f3b9a9ace2. Scratch: lead/public-b-2026-10-18.
Original own remote branches contain unsigned historical commits. Clean `-finish` continuations preserve those branches without force pushes, and apply only the assigned lane changes with new DCO-certified commits.

| Issue | Branch / head | Stage | Next action |
|---|---|---|---|
| #20 | no product branch | PARKED Owner comparison-contract decision | Connector retrieved both artifacts/logs; exact6failures and all values in FRZ-01-evidence.md; nested/date context/intentional reference substitutions require Owner contract decision; no changes |
| #6 | lane/W9-05 afbf4c5 | PARKED | Owner decision on unsupported nested/error-equivalence conflict; issue comment posted |
| #9 | lane/W9-08-finish 5033951 | common gates | resume_metadata; review APPROVED, PR#32; makecheck at~23%, one likely inherited0077 permissions failure, narrow022 rerun passes |
| #10 | lane/W9-09 661cd61d | draft PR#34 | formats; six review findings fixed, independent APPROVED;194focused pass;common gates pending |
| #11 | lane/W9-10-finish 5be9f04 | common gates + review fixes | resume_dbt; ratio/exposure fixed+approved, PR#29; 142offline tests per mode,8dbt nightly pass |
| #12 | lane/W9-11-finish | recovered implementation uncommitted | resume_eventhouse;46permode+strictdocs pass, review APPROVED, PR#30;common gates queued |
| #13 | lane/W9-12-finish | recovered implementation uncommitted | root;171permode+docs/lint/mypy703/links/wording/DCO pass, review APPROVED, PR#33;prepared full collection19125 selected succeeds |
| #14 | old lane/W9-13 1249af9 (journal only) | PARKED | missing merged W9-01; prior journal also documents invalid decimal specification boundaries |
| #17 | lane/W9-16-finish | recovered implementation uncommitted | resume_iceberg;112permode+docs/static pass, review APPROVED, PR#31;emulator image pull denied;common queued |

Draft PR#34 formats (661cd61d) also open. Draft PRs now open: #29 dbt (63e4e55), #30 Eventhouse (5265080), #31 Iceberg (df426e9), #32 metadata (91ef620), #33 semantic models (3b3852a). All target main; no merges. All five have independent code approval; exact-head CI and common acceptance are pending. Full suites serialize under /tmp/public-b-full-check.lock. All tests through $HOME/bin/reap.py. Last zombie observation30, below hard stop1000. Separate reviewer spawn blocked by platform thread cap; existing independent lane agents cross-review.

After context restart reread attachment and this state; inspect worktrees/ledgers before repeating work. Logs/status live per lane and /tmp. No passing CI or live result claimed.
