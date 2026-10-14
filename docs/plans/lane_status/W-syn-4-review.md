# W-syn-4 review

Reviewer: independent review session (did not build this lane).
Head reviewed: `f8126389726092df16be172ab6988e1c7419482b` (expected prefix `f812638`: matches).
Base: `origin/main`. Plan: `origin/plan/promotion-093`, section 4.7.

## Verdict: APPROVE

The lane adds one contract test (`plugins/shape-fabric/tests/test_synapse_config_docs.py`)
that compares the keys `SynapseSink` and `_prefixed_table` read with the first column of the
`synapse://` option table in `docs/plugins/fabric-writers.md`. It documents the two
user-settable keys that were missing (`commit_rows`, `table_prefix`), and keeps `filesystem` in a
commented exclusion list with file:line evidence. That is what the lead's answer asked for. It
adds one CHANGELOG line under `## Unreleased`. It changes no sink behaviour and does not promote
anything: no STABLE entry and no surface snapshot write. No skip, xfail, gate, regression store or
golden corpus changed. W-syn-1/2/3 were not done.

## Commands (Python 3.11 venv, `make bootstrap` + docs/plugin installs: exit 0)

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | 701 source files, no issues |
| `pytest -p no:cov -q plugins/shape-fabric/tests/test_synapse_config_docs.py plugins/shape-fabric/tests/test_synapse.py` | 0 | 92 passed |
| `pytest -p no:cov -q plugins/shape-fabric/tests --ignore-glob='*emulator*'` (lane) | 1 | 34 failed, 1258 passed, 1 error |
| same on `origin/main` | 1 | 34 failed, 1246 passed, 1 error |

The 34 failures and the error are identical on `origin/main`: `test_live_*` and one CLI test that need
a live service or ODBC driver. They are sandbox artefacts, not this lane's. The lane adds 12 passing tests.
The emulator files fail here because libodbc is missing (sandbox).

## Mutation checks (local only, reverted, not committed)

Each mutation made `test_synapse_documented_sink_config_keys_match_code` fail (1 failed):

1. add `opts.pop("extra_key", None)` to `SynapseSink._write`;
2. add `"fresh"` to the `_take(...)` tuple;
3. rename the key `_prefixed_table` reads (`table_prefix` -> `tbl_prefix`);
4. delete the `index` row from the docs table;
5. add an undocumented `zorder` key to a docs row.

The lane's own guard tests cover the same cases in-process.
The `filesystem` evidence was checked: `sinks.py:213` and `_storage.py:59-60,71,75` are as cited.

## Findings

Blocking: none.

Non-blocking:

1. `docs/plugins/fabric-writers.md:170`: `connection` (an open DB-API connection) and `schema` (a
   pyarrow schema) stay documented as options, so the test treats them as public. Like `filesystem`,
   they cannot be supplied as `--sink-config` strings. The test is right to pin the page as it is: both rows
   predate the lane, and the lead's answer covered only `filesystem`, `commit_rows` and
   `table_prefix`. The docs owner (W-syn-3) should say on the page that those two are Python-only,
   or the lead should decide whether they belong in the exclusion list.
2. `docs/plugins/fabric-writers.md:131-135,211-215`: prose edits ("by default", the
   `commit_rows` failure sentence) go a little beyond the option table. They are accurate
   against `sinks.py:243-281` and are covered by the lead's answer that allowed documenting the keys.
3. `plugins/shape-fabric/tests/test_synapse_config_docs.py:73-77,82`: renaming `SynapseSink`,
   `_prefixed_table` or the `## \`synapse://\`:` heading fails with `StopIteration`/`IndexError`
   instead of a message. It still fails loudly; a clearer message is optional.
4. The lane status records `make check` as timing out (exit 124). Full-suite proof is left to
   GitHub CI, as planned.
