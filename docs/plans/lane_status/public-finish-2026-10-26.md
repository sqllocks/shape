# Public finish — 2026-10-26

PR: [Public finish](https://github.com/sqllocks/shape/pull/53).

## Targeted test output

| Item | Red | Green |
|---|---|---|
| W8-05 vault bridge | `3 failed, 5 passed` (new commands absent) | `706 passed, 21 warnings in 11.21s` (affected bridge schemas, vectors, protocols and frozen 1.0–1.3 replay); final command tests `9 passed`; review regression `14 failed, 9 passed in 0.88s` then `35 passed, 145 deselected in 0.89s`; integrated vault tests `23 passed in 0.78s`; Ruff and format passed. |
| Chaos-kind catalogue | `3 failed, 4 passed in 1.59s` | `87 passed in 2.77s` (catalogue, bridge chaos, ground truth and audit); Ruff and format passed. |
| Timelapse contract | `1 failed in 2.12s` (`$.frames matches none of anyOf`) | `48 passed, 1 warning in 14.25s` (history, timelapse and frozen schema compatibility); stricter frame schema regression `1 passed in 0.80s`; Ruff and format passed. |
| CR-0117 report-card format | Python API: `7 failed` (unexpected `data_format`); bridge/CLI: `5 failed, 1 passed` | `99 passed in 17.31s` (report card, bridge format and earlier bridge behavior); Ruff and format passed. |
| W1-09 Windows Spark Delta | Not applicable: implementation already present on the starting main | Existing scheduled Windows Spark/Delta job passed; see CI link below. `git diff --check` passed for the status correction. |
| Combined integration | `1 failed, 845 passed, 22 warnings in 37.46s` (missing report-card format in command table) | Documentation correction: `60 passed in 0.37s`; two bridge executable examples passed using the default kernel; Ruff `All checks passed!`, format `29 files already formatted`; `check_user_facing: clean`. |
| CI snapshot/annotation correction | `4 failed, 85 passed in 3.05s` (CLI snapshot plus vault path/write annotations) | `201 passed in 3.35s` (CLI surface, bridge annotations and generated schemas); Ruff and format passed. |
| Tracker cleanup | `21 missing requirements` | `38 required statuses, exact W10-01 source row and 22 existing lane links passed`; `git diff --check` passed. |

## Green CI

- [Scheduled Nightly Windows Spark/Delta](https://github.com/sqllocks/shape/actions/runs/40296225466/job/121482842837).

- [Public finish PR checks](https://github.com/sqllocks/shape/pull/53/checks).
