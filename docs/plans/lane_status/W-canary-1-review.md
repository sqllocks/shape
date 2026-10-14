# W-canary-1 review

Reviewer: independent review session (did not build this lane).
Head reviewed: `22c7e5df78ba99256add5c11e0fbfe3c0d14f69f` (expected prefix `22c7e5d`: matches).
Base: `origin/main`. Plan: `origin/plan/promotion-093`, section 4.1.

## Verdict: REJECT

The key-set pins are correct and do what was asked. Both `canary make --json` (with and without
`--dry-run`) and `canary check --json` (pass and blind spot) are compared to the exact sets in
section 4.1. The frozen sample is byte-for-byte what `canary make` writes today. The schema sits in
`src/shape/schemas/` with the repo's `<format>-v1.schema.json` naming. It is registered in
`FORMATS` (`src/shape/bridge/handlers/formats.py`), which the existing
`test_every_shipped_schema_file_is_in_the_table...` requires. There is no promotion: no STABLE
entry and no `cli_surface --write`.

One blocking problem: the published schema does not describe the document its registered reader accepts.

## Commands (Python 3.11 venv, `make bootstrap` + docs/plugin installs: exit 0)

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | 698 source files, no issues |
| `pytest -p no:cov -q tests/scenario/test_canary.py tests/bridge` | 0 | 1271 passed, 2 skipped (sklearn absent, pre-existing skips) |
| `shape canary make null-flood -o cn --json`; validate `payload` against the schema | 0 | validates |
| `shape canary check payload-canary.json --result cn/canary.json` (that payload saved as a file) | 2 | `shape: error: canary payload-canary.json has unknown keys: directory, dry_run, files, rows` |
| `canary.load_canary('payload-canary.json')` | raises | same message |
| dry-run payload against the schema | 0 | validates |

## Findings

### Blocking

1. `src/shape/schemas/shape-canary-v1.schema.json:31-40`: the schema accepts `directory`,
   `files`, `rows` and `dry_run` (optional, tied together by `dependentRequired`). The reader the
   lane registers for it, `shape.scenario.canary.load_canary`
   (`src/shape/bridge/handlers/formats.py:141-147`), refuses those keys
   (`canary.py:30` `_KEYS` plus `check_header`'s unknown-key check, `formats.py:65-67`). The Bridge
   publishes `format_schema` as "the JSON Schemas Shape's readers validate their input with"
   (`formats.py:1`). So a client that validates with this schema is told a document is a valid
   `shape-canary` v1 that `shape canary check` then refuses with exit 2 (reproduced above). The
   plan calls the `shape-canary` v1 document "frozen in practice". Once the schema is published,
   narrowing it later is a break, so fix it before the freeze.
   What to do: make the schema exactly the reader's document: the seven keys `format, version, id,
   scenario, seed, marker, expected`, with `additionalProperties: false`, and no plan fields or
   `dependentRequired`. To meet the "live `canary make --json` output validates" requirement,
   validate the written `canary.json` (the test already does) and the payload restricted to the
   document keys. The lane's own key-set test already pins the four plan fields
   (`directory, files, rows, dry_run`). If the lead instead wants the plan fields in the schema,
   that is a reader change (accept and ignore them) and needs the lead's answer first. Also drop the
   description sentence about the envelope, or reword it so it matches.

### Non-blocking

2. `src/shape/bridge/handlers/formats.py:141` registers `canary` with `since="1.2"`. Bridge 1.2
   shipped in 0.9.3, so `format_schema` served as 1.2 now lists one more name. That is additive and
   the existing convention test forces it. But it is a user-visible Bridge change with no
   CHANGELOG line under `## Unreleased`. Add one, or say why the lead does not want one.
3. `src/shape/schemas/shape-canary-v1.schema.json:27`: the `expected` item pattern hard-codes the
   kinds `drift|gate|rule`. That matches `detect.parse_check` today, but a new detection kind would
   need a schema version bump. That is fine if intended; consider saying so in the description.
4. `docs/CANARIES.md` does not mention the new schema file. Leave that to W-canary-2 (claim table/page).
5. The lane status records one `make check` run that exited 2 when the coverage stage was interrupted.
   That run showed two schema-registration failures, which the lane then fixed. The full suite has not
   completed since, so full-suite proof is left to CI.
