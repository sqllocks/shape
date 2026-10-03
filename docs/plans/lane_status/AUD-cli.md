# AUD-cli — audit of the command line — status

Branch `lane/AUD-cli`, started from `int/INT-15` (f99563e). Area: every `shape` command and flag
(help text, exit codes, error messages, `--json` output) and the docs pages that document commands.
Paths: `src/shape/cli/`, `src/shape/__main__.py`, their tests, docs pages about CLI commands.
No D-xx/T-xx decision, gate or tolerance touched; §11 and §2.3 unedited; `$SPINDLE_ROOT` untouched.

## Phase 1 — findings

Reproduced from an empty folder, Python 3.11.15, editable install with the compiled kernel,
`.[dev,streaming,advanced]` plus `plugins/shape-domains` (the CI install).

1. **high** — `shape diff` on capture/evidence JSON ignores `--fail-on-drift`, `--json` and every
   threshold flag (`main.py`, the legacy `diff` branch of `_dispatch`). Repro: `shape capture
   data.csv -o base.json; shape capture later.csv -o later.json` (later has new extremes);
   `shape diff base.json later.json --fail-on-drift --json r.json --ignore amount` prints the
   changes, exits 0 and writes no `r.json`. Expected: exit 1 with drift under `--fail-on-drift`,
   `--json` written, flags that only apply to profiles refused. A CI gate built on it never fails.
2. **medium** — `shape check EVIDENCE.json CONTRACT.json --json OUT` does not write `OUT`
   (`_cmd_evidence`). Expected: the result written, as for a profile.
3. **medium** — "artifact not verified" notices print as raw Python warnings
   (`/…/src/shape/cli/profiles.py:227: ArtifactNotVerifiedWarning: … \n  return shape.load(…)`)
   instead of the one `shape: note: …` line `docs/SIGNING.md` promises, for `shape profile
   export|import|list|validate|safe|registry …`, `compatibility`, `certify-shapes`,
   `registry diff` (which also names its temporary files) and `conformance` (its self-test's
   temporary files). Only the commands dispatched through `main._run` route them.
4. **medium** — `shape fidelity A B --format text` (or any unknown format) fails after the whole
   comparison with `shape: error: missing key "no plugin 'text' in group shape.reports" in the
   input`: the format is not checked up front, and `errors.describe` turns every `KeyError` into
   "missing key … in the input".
5. **medium** — `shape fidelity A B --tier N -o R.md -o R.html` writes JSON into `R.md` and ignores
   `R.html` (`tiers.run_fidelity`); the help says `-o` picks the format by extension and repeats.
6. **medium** — `shape generate|describe|presets|emit|stream|continue|time-travel|chaos|
   generate-drift SCHEMA.yaml` fail with `not valid JSON: Expecting value…`, while `shape validate
   SCHEMA.yaml` reports the same file valid and `shape doctor` lists PyYAML as needed for "YAML
   generation schemas" (`generation.load_target` reads JSON only).
7. **medium** — `shape learn` writes non-standard JSON (`"std_dev": Infinity`) when a column's
   spread overflows (`x` = 1e308, -1e308); `shape generate` then refuses the schema. Expected: the
   CLI never writes a `.json` file that is not JSON. (Root cause in `shape.generation.learn`,
   outside this area: filed.)
8. **medium** — `shape quality` exits **2** when the data violates the rules; `docs/CLI.md` says 2 is
   bad input and 1 a failed check.
9. **low** — `docs/CLI.md` says each command's `--help` names its verdict exit codes; `check`
   (4 for an evidence contract) and `compatibility` (5) do not, and `conformance`, `version`,
   `quality`, `key`, `fd`, `privacy-k`, `query`, `certify-shapes` have no help text at all (absent
   from the command list of `shape -h`, positional arguments unexplained).
10. **low** — errors on a file that is not JSON or not text do not name the file:
    `generate|describe|presets|emit|validate X.json` → `not valid JSON: Expecting …`;
    `validate|from-ddl|describe BIN` → `'utf-8' codec can't decode byte …`;
    `continue --transitions BAD.json` the same.
11. **low** — `shape generate DOMAIN --scale-mode local_single` with no `-o`/`--sink` prints
    `generating into memory (nothing is kept)` twice (`scale.build_request` and `run_scale`).
12. **low** — `shape emit|stream --poison-fraction -1` is accepted silently (the other fractions are
    range-checked); `--retries -1` and `--checkpoint-every 0` are accepted too.
13. **low** — `shape emit … --live-target T --live-report R.txt` refuses the report format only
    after the whole stream has been delivered (exit 2 after the work is done).
14. **low** — output closed early (`shape plugins list | head`) ends with `shape: error: Broken
    pipe` and exit 2.
15. **low** — `docs/DEMO.md` shows `shape demo notebook retail --mode seeding -o retail.ipynb`;
    `-o` is not accepted (`--output` only); `demo report` the same.
16. **low** — `shape profile registry delete MISSING` exits 1 with `shape: profile '…' not found.`
    while `tag`/`diff` of a missing profile exit 2 with `shape: error: profile not found: …`.
17. **low** — `--log-level bogus` is accepted silently.
18. **low** — `--metrics DIR/MISSING/m.json`: the command runs to the end, then exits 2 with
    `file not found: …`, hiding the command's own result.
19. **low** — `shape generate --chunk-rows 0` is silently ignored (the default is used); a negative
    value is refused.
20. **low** — missing-path wording still varies: `learn`/`mask` `path not found: X`, `profile
    registry save` `not found: X` (the documented form is `file not found: X`; `verify`/`drift`/
    `fidelity` `Path not found:` is pinned by `tests/quality/test_verify.py`, see ISS-cli).
21. **low** — `shape cat` as a git textconv prints a `shape: note: /tmp/git-blob-…/x.shape is not
    signed … (check it with --verify PUBKEY)` line on every `git diff`; `cat` has no `--verify`.
22. **low (outside the area)** — `shape demo run S --input-file MISSING` exits 1 (run failed), not
    2 (bad input) as `docs/DEMO.md`'s exit codes put it; the demo API records it as a failed run.

Coverage of `shape.cli` before any change: see "Commands and results".

## Phase 2 — issues

No duplicates found (searched open issues; #5, #27 and #4 are related but different).

| Finding | Issue |
|---|---|
| 1 | #107 |
| 2 | #108 |
| 3 | #109 |
| 4 | #110 |
| 5 | #111 |
| 6 | #112 |
| 7 | #113 |
| 8 | #114 |
| 9 | #115 |
| 10 | #116 |
| 11 | #117 |
| 12 | #118 |
| 13 | #119 |
| 14 | #120 |
| 15 | #121 |
| 16 | #122 |
| 17 | #123 |
| 18 | #123 |
| 19 | #124 |
| 20 | #125 |
| 21 | #126 |
| 22 | #127 |


## Phase 3 — fixes

(filled in below)
