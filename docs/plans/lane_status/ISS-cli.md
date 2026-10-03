# ISS-cli — issues #6, #8, #27, #28, #29, #30 — status

Branch `lane/ISS-cli` (from `build/main-plan` at 4051235). The issues were filed against `main`,
which is older; every one was reproduced on this branch first (commands run from an empty folder,
Python 3.11.15, editable install, compiled kernel). No D-xx/T-xx decision, gate or tolerance was
touched; nothing is escalated under §0.4; §11 and §2.3 are unedited; `$SPINDLE_ROOT` untouched.
The issues were not commented on, labelled or closed.

Every regression test below was run against the unmodified `build/main-plan` source (a clean
worktree at 4051235 with only the new test files added): 52 failed and 35 errored there, and the
6 that passed are guards of behaviour that must not change (a non-empty CSV does not warn, a list
of non-dicts is still refused, other content is stored unchanged, the legacy `checkout NAME REF
OUT` order, and the example that already ran). All pass on this branch.

## #6 `examples/shape_as_code.py` crashes — reproduces; fixed

- Repro: `python examples/shape_as_code.py` → `SourceError: unsupported source type list; expected
  a path, glob, pyarrow.Table, pandas.DataFrame or a dict of those` (`profile/reference/sources.py`).
- Fix: `shape.profile` accepts a non-empty list or tuple of row dicts
  (`pyarrow.Table.from_pylist`, `sources.py`); a list of anything else is still refused. The
  engine's reader (`shape.io.open_source`) already took row iterables. Then the example failed on
  its next line (`shape.save(..., name=...)` has no `name`), then `shape.query` on a profile
  (`ModelError`) and `shape.generate(profile)` returning a result, not `(data, report)`; the
  example is rewritten to what runs: profile → save → load → summary → generate. README's "What's
  in early access" paragraph said `shape.generate()` raises `NotImplementedError` for a profile;
  that is no longer true and is corrected (query on a profile still exits 2).
- Tests: `tests/regressions/test_iss_examples_entrypoints.py` (runs every file in `examples/` as a
  subprocess; list-of-dicts profile; equality with the same columns as an Arrow table; non-dict
  lists still refused).

## #8 `python -m shape` — reproduces; fixed

- Repro: `python -m shape --version` → `No module named shape.__main__; 'shape' is a package ...`.
- Fix: `src/shape/__main__.py` calls `shape.cli.main.main`. Tests: same file (`--version`, and a
  `profile` run through `-m shape`). `shape --version` and `python -m shape --version` start in
  about 55 ms (T START ≤ 300 ms).

## #27 CLI rough edges

1. Duplicate commands — `show`/`inspect` reproduce (identical SHA-256 of output). §10/P1-11/P1-14
   of the plan require `inspect` with `show` as its alias, so neither is removed: they are now one
   parser (`inspect`, `aliases=["show"]`) and the help says "`show` is an alias". `capture`
   reproduces as a CSV-only command but it is **not** a duplicate of `profile`: it writes a Shape
   *model* (what `query`, `compatibility` and `plan` read), `profile` writes a *profile*. It is not
   in §10, but docs (QUICKSTART, SIGNING, TUTORIAL) and tests use it, so it stays; its help and
   `docs/CLI.md` now say how it differs. Deprecating it is a product decision left to the owner.
2. `doctor` — reproduces (`{"cryptography": "50.0.2", "pyarrow": "25.0.1", "python": ...}`). Now a
   readable report: Shape version, Python and platform, kernel (rust/python), required and
   optional packages with OK/missing and what each is needed for (including `tzdata`), `Result:
   OK`; exit 1 only when a required package is missing; `--json` keeps the old keys and adds
   `shape`, `kernel`, `required`, `optional`, `ok`. `tests/cli/test_cli.py::test_cli_doctor` parses
   JSON, so it now passes `--json` (the one existing test edited, for the changed default output).
3. 0-row CSV — reproduces (`printf 'a,b\n' > hdr.csv; shape profile hdr.csv -o o.shape` → exit 0,
   no warning). Now `shape: warning: hdr.csv has 0 rows: ...` on stderr (profile still written,
   exit 0); `--fail-on-empty` exits 2 and writes nothing. An empty file still fails as before.
4. Missing-file wording — reproduces (`source not found: nope.csv` vs `[Errno 2] No such file or
   directory: 'nope.shape'`). One formatter (`shape/cli/errors.py`): `shape: error: file not found:
   PATH` for `profile`, `check`, `show`, `compatibility`, `registry commit`, `profile registry`
   and the privacy commands. Not unified: `shape verify`/`drift` on a missing path say `Path not
   found: ...` (pinned by `tests/quality/test_verify.py`); the wording there is left as it is.
5. Same data, different format, different id — reproduces, with a precise cause. On a CSV and a
   Parquet file of the same 500 rows with a date column, `shape diff` says `drifted: false` and the
   ids differ because the stored minimum/maximum of the date column are `['str','2025-01-01']`
   (from CSV) vs `['date','2025-01-01']` (from Parquet). With only int/string columns the ids are
   equal. Not changed (it would change profile content and every id, and is outside this lane's
   list); documented in `docs/CLI.md` ("Content ids across source formats"). Offer for the lead:
   normalising the min/max tag for date columns in the CSV path.
- Tests: `tests/regressions/test_iss_cli_rough_edges.py`.

## #28 registry commits raw profiles — reproduces in both stores; fixed

- `shape registry` (content-addressed `.shape` registry): `shape registry reg commit customers
  p.shape` stored the raw profile; 500 rows with an email column gave 1002 matches of the e-mail
  pattern in `reg/objects/<id>/profile.json` (the issue saw 490; this data has two per value).
- `shape profile registry` (P6-10, named profiles): `profile registry save p.shape --system crm
  --name q1` stored the raw profile as well (1002 matches). It is a different store, with a
  home-folder default root, but `--root` can point anywhere.
- Fix, `shape registry`: `LocalRegistry.commit(..., allow_raw=False)` raises `RawProfileError` for
  a raw profile (a `.shape` of kind `profile`, or `shape profile export` JSON); the CLI refuses it
  (exit 2) and names the options: `--safe` converts with `to_safe_profile` (`--k`, `--sensitive`)
  and commits the safe JSON; the output of `shape profile safe` commits as is (it is leak-scanned
  with the `validate --safe` scanner first; one that fails, e.g. `--unsafe-full-fidelity`, is
  refused with exit 1); `--allow-raw` keeps the old behaviour with a stderr warning. A profile
  commit records `profile_form` = `safe`|`raw` in the log. Content ids are unchanged in meaning:
  the sha256 of the stored bytes (the safe JSON is deterministic, so re-committing gives the same
  id). Everything else is committed unchanged. No spec rule changed:
  `docs/specs/SHAPE_ARTIFACT_SPEC.md` is not touched and the registry stores what it was given.
- Fix, `shape profile registry`: `save --safe [--k N] [--sensitive]` stores the safe form as
  `<system>/<table>/<name>.safe.json` (one table per file, as `profile safe` writes it plus a
  `registry` block for description and tags; the tags are one comma-separated string, because the
  leak scanner reads a list of more than two strings as a value dump). `list` (a Form column and
  `form` in JSON), `tag`, `diff`, `validate` (also leak-scans safe entries), `validate --data`,
  `reindex` (tags survive it) and `delete` work on both forms; an identity has one form, and the
  other needs `--overwrite`. A full save prints on stderr that it holds real values and that
  `--safe` exists. Safe entries drop relationships between tables.
- Docs: new `docs/REGISTRY.md` (incl. a table of which store holds real values), updated
  `docs/PROFILE_REGISTRY.md`, a README sentence next to "What to commit", CHANGELOG (with the
  behaviour change: committing a raw profile to `shape registry` now exits 2).
- Tests: `tests/regressions/test_iss_registry.py` (the issue's leak check: no e-mail in any stored
  byte after `--safe`), `tests/regressions/test_iss_profile_registry_safe.py`.

## #29 registry CLI — reproduces; fixed

All six findings reproduced (`log` showed `"metadata": {}`; the third positional JSON was
ignored; usage `root {commit,...} name [arg1] [arg2]`; `checkout NAME ref` wrote zip bytes to the
terminal; `created_at` epoch only; unknown ref traceback; `log` of an unknown name `[]`, exit 0).
`src/shape/cli/registry.py` (the parser and run code leave `main.py` as two small hooks):
named sub-commands with metavars (`ROOT ACTION`, `NAME`, `ARTIFACT`, `REF`, `TAG`, `SOURCE`,
`TARGET`, `REF1 REF2`), `commit --meta KEY=VALUE` (repeatable) and `--business-date YYYY-MM-DD`
(validated), `created` ISO-8601 UTC next to `created_at`, `checkout ... -o OUT` (refuses to write to
a terminal; stdout still works when redirected or piped; the old `checkout NAME REF OUT` order
still works), `list`, `show NAME [REF]`, `diff NAME REF1 REF2` (JSON: changed paths; raw profiles:
drift; otherwise whether the ids differ), unknown ref = one line, exit 2, and `log` of an unknown
name is an error naming the known names. The old order `shape registry ROOT ACTION NAME ...` is
unchanged for commit/tag/promote/log. Not done: an author or message on a commit (`--meta` carries
them: the registry entry has no separate field). Tests: `test_iss_registry.py`.

## #30 tracebacks for ordinary errors — reproduces (3 of 3 shapes); fixed

- Repros on this branch: `shape compatibility p.shape p.shape` → `ArtifactError: shape.json
  missing: not a Shape model artifact`; `shape registry reg checkout orders nope` →
  `RegistryError` traceback. `shape fidelity people.csv people.csv` no longer fails: CSV vs CSV
  works (P4-09); the `JSONDecodeError` remains for `shape fidelity bad.json people.csv`. A sweep of
  34 bad-input invocations found 23 that ended in an uncaught exception with exit 1, among them
  `query`, `check`, `diff`, `quality`, `key`, `fd`, `privacy-k`, `capture`, `plan`,
  `certify-shapes` and four `registry` actions.
- Fix: `src/shape/cli/errors.py`, one handler around the whole run (`main`): `ShapeError`,
  `OSError` (so `FileNotFoundError`), `ValueError` (so `JSONDecodeError`), `KeyError`,
  `ImportError`, `NotImplementedError`, `zipfile.BadZipFile` → `shape: error: MESSAGE`, exit 2.
  Anything else (a bug) still raises. `--debug` (before the command) or `SHAPE_DEBUG=1` re-raises
  everything, including in `_run` and the `profile ...` / `profile safe|validate` routers. JSON
  files are read by one helper that names the file. `compatibility`/`certify-shapes` say they need
  model artifacts when given a profile; `fidelity` says a profile is not a data reference. Exit
  codes are documented in `docs/CLI.md`.
- Per command (one test each in `test_iss_cli_errors.py`): fidelity (bad JSON, profile as
  reference, missing), compatibility (profiles, missing, bad JSON), registry (checkout / tag /
  promote unknown ref, bad name, missing file; missing ARTIFACT is the usage error, exit 2),
  query, check, diff, inspect, show, quality, key, fd, privacy-k, certify-shapes, plan, capture,
  profile; plus `--debug`, `SHAPE_DEBUG=1`, and a bug is not hidden.
- Open decision: the issue also asks `fidelity` to accept a `.shape` reference. CSV, Parquet and
  JSONL references work; a profile is refused with a message that says to profile the synthetic data
  and use `shape diff`. Scoring data against a profile has no defined semantics in the plan
  (it would need its own acceptance criteria), so it is not invented here.

## Results (final tree, commit before this status edit; run in this session)

- `ruff check` and `ruff format --check` (src tests plugins benchmarks/vs_spindle): clean.
- `mypy` (strict): no issues in 311 source files. `vulture src/shape scripts/vulture_whitelist.py
  --min-confidence 80`: nothing. `lint-imports`: 1 kept, 0 broken (`shape.__main__` added to the
  contract's module list, which `tests/plugins/test_builtins.py` requires). `check_user_facing`:
  clean. `bandit -q -r src -ll`: exit 0. `check_requirements`, `check_secrets`,
  `check_plugin_skeletons`, `check_conformance_coverage`: OK.
- START: `shape --version` 41-76 ms over 5 runs (≤ 300 ms); `python -m shape --version` 41-71 ms.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`:
  4598 passed, 46 deselected. Same with `SHAPE_KERNEL=python`: 4598 passed, 46 deselected.
  `pytest tests/demo --ignore=tests/demo/fabric`: 133 passed. (The first full run found one
  failure, the import-contract module list; fixed and re-run in full.)
- Not run: the Fabric demo tests, emulator and live tests, the `heavy` marker, `make check`'s
  coverage threshold run and the vs-baseline benchmark harness (none of the changes touch them).

## Left for the lead / owner

- `fidelity` with a `.shape` profile as the reference (issue #30): refused with a clear message;
  it needs a defined meaning first.
- `shape capture`: kept (writes models, documented); whether to deprecate it is the owner's call.
- #27.5 (content id differs by source format for a date column's min/max tag): documented, not
  changed.
- Registry commits carry no separate author/message field (`--meta` is the way).
