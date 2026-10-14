# W-pdb-2 review

Reviewer: independent review session (did not build this lane).
Head reviewed: `26e8624c79070646e520b26ed030ef465b6880c5` (expected prefix `26e8624`: matches).
Base: `origin/lane/W-pdb-1` (`bf87f56`). Only what is new on top of that lane was reviewed.
Plan: `origin/plan/promotion-093`, section 4.11 (row `profile-db`).

## Verdict: APPROVE

Tests only. The lane adds one file, `plugins/shape-sqlserver/tests/test_json_keys.py`, plus its
status file. It runs `ProfileDbCommand` against the in-memory `retail` scenario: catalog-only and
sampled, each with and without `-o`. It pins with set equality:

- the stdout receipt `docs/plugins/sqlserver.md:55` shows (`written, shape_content_id, tables,
  relationships`; only the two counts without `-o`);
- the file `--json SUMMARY.json` writes: top level `name, row_count, tables, relationships`;
  table `name, row_count, primary_key, columns`; column (13 keys); relationship (7 keys).

The builder rightly found that the plan's "`--json` keys" mixes the two documents:
`written`/`shape_content_id` are in the stdout receipt, not in the `--json` file. The test pins
both. There is no production change, CHANGELOG line, promotion, STABLE entry or snapshot write.
The plan assigns the CHANGELOG line to W-pdb-1 and asks none of W-pdb-2. No skip, xfail or
lowered gate.

## Commands (Python 3.11 venv, `make bootstrap` + docs/plugin installs: exit 0)

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | 698 source files, no issues |
| `pytest -p no:cov -q plugins/shape-sqlserver/tests/test_json_keys.py` | 0 | 4 passed |
| `pytest -p no:cov -q plugins/shape-sqlserver/tests` | 1 | 178 passed, 19 errors |

All 19 errors are in `test_emulator.py`: `ImportError: libodbc.so.2` (sandbox; known artefact).

## Mutation checks (local only, reverted)

- `command.py`: add a key to the stdout receipt: 4 failed.
- `command.py`: add a key to the `--json` summary: 4 failed.
- The builder's status records removing `mean` from every column and adding a root key: 4 failed each.

## Findings

Blocking: none.

Non-blocking:

1. `src/shape/profile/reference/profile.py:212-223,337-341`: `Profile.summary()` can add
   `findings`, `capture` (top level) and `findings`, `sampling` (table level). `profile-db` cannot
   produce them today. `findings` comes only from the workbook reader, `capture` is not passed, and
   `profile-db` records `sampling` on the dataset (`plugins/shape-sqlserver/src/shape_sqlserver/profiler.py:472`),
   which `summary()` does not copy. So the pinned sets are the real ones. If a later change makes
   any of them reachable, this test fails, which is the intended behaviour. The keys come from the
   core summary, so a core profile change also breaks this plugin test. Mention that in the claim
   table so nobody is surprised.
2. The docs page shows only the stdout receipt. When the `profile-db` page gets its claim table,
   list the `--json` file's key sets there too, because they are now pinned.
