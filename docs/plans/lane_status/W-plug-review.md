# W-plug review

Reviewer: an independent session that did not build this lane.
Head reviewed: `54a0838afef98c19d62c61122de269199120319d` (`lane/W-plug`), against `origin/main`
`c10923be9278bc63f2713ac0ed32a548b59a7afb`, range `origin/main...HEAD` (3 commits, 17 files).

## Verdict: APPROVE

Every section-6 item (W-plug-1 to W-plug-6) and decisions 14 and 16 are met for an empty list.
Nothing is promoted. One gap in the alias rule (finding 1) must be closed before the first Fabric
command is promoted; it cannot affect this release because the list is empty.

## Commands run (this session, exit codes)

| Command | Exit | Result |
|---|---:|---|
| `git rev-parse HEAD` | 0 | `54a0838a…`, as expected |
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 | 2080 files formatted |
| `mypy` | 0 | no issues in 698 files |
| `python scripts/cli_surface.py --check` | 0 | 0 breaking, 0 notes |
| `python scripts/cli_surface.py --check --plugins` | 0 | 0 breaking, 0 notes |
| `python scripts/gen_exit_codes.py --check` | 0 | clean |
| `python scripts/check_versions.py` | 0 | 58 locations agree on 0.9.2 |
| `pytest -q --no-cov tests/cli tests/plugins/test_plugin_cli.py tests/plugins/test_stability_promise.py` | 0 | 889 passed, 1 skipped (`test_generate_to.py:162` `importorskip("shape_databases")`, already on `origin/main`) |
| `pytest -q --no-cov tests/plugins` (the loader in `host.py` changed) | 0 | 486 passed, 3 skipped (existing `importorskip` for plugins not installed here) |
| `git diff --quiet origin/main...HEAD -- tests/cli/cli_surface_v1.json` | 0 | core baseline unchanged |
| Red reproduction 1: `origin/main` source + the lane's tests from `2af003e` (`tests/cli/test_plugin_surface.py`, `tests/plugins/test_stability_promise.py`) | 1 | 33 failed, 29 passed, matching the status file |
| Red reproduction 2: `2af003e`'s `scripts/cli_surface.py` + `05dc23d`'s `tests/cli/test_cli_surface.py` | 1 | 2 failed (`renaming`, `reordering`), 26 passed, matching the status file |
| `gh run view 38534238535 --repo sqllocks/shape` | 0 | head `54a0838a…`, status `in_progress` when read; no conclusion yet |

The full core coverage stage and the 9 reference-example pages were not counted, as instructed;
the lead proves the full suite in run 38534238535.

## Checks

1. **Scope.** `STABLE_PLUGIN_COMMANDS: dict[str, str] = {}` (`src/shape/cli/stability.py:46`);
   `level()` reads both sets (`:90`). No version change (no diff in any `pyproject.toml` or
   `src/shape/__init__.py`). Core baseline unchanged. CHANGELOG changes are only under
   `## Unreleased`. Section 6 items: list and loader refusal (`host.py:335`), `snapshot_plugins()`
   and `--plugins` `--check`/`--write`, second baseline, `make check` (`Makefile:24`) and CI
   (`ci.yml:61`), stub and loaded-parser markers, exit-class harness and `gen_exit_codes.py`
   rows, lockstep test, missing-plugin one-line exit 2, both docs pages and the promise-page
   test. Decision 14: both spellings, including `setup-fabric` / `fabric setup`, are snapshotted
   and compared (probe below). Decision 16: same-release wording is in `docs/CLI_STABILITY.md`
   and `docs/plugins/stability.md`; the missing-plugin error names `DIST==<core version>`.
   Lead decision: `_compare_positionals` now fails a renamed, moved or reordered positional;
   added optional passes and added required fails (existing rule); core baseline still passes.
2. **Tests first.** Genuine. Both red runs in the status file reproduce exactly from the
   commits it cites (table above). Note that `2af003e` committed tests and implementation
   together, so the first red run is shown by reproduction, not by commit order.
3. **No lowered gates.** `git diff origin/main...HEAD --stat` touches no regression store,
   golden corpus or `benchmarks/` path. Grepping added lines for
   `skip|xfail|importorskip|cov-fail-under|tolerance` matches only prose in docs and the status
   file and one comment saying a listed plugin is never skipped. No marker, gate or tolerance
   was added or changed.
4. **Tool runs.** All clean (table above).
5. **Adversarial read.**
   - CI wiring: `ci.yml:61` runs `cli_surface.py --check --plugins` in the `test` matrix after
     `pip install -e` of all five first-party plugins (`ci.yml:39`); `Makefile:24` runs it in
     `make check`. `test_plugin_surface_is_checked_in_make_and_ci` pins both.
   - Markers, real plugins installed: all 12 plugin stubs in `shape --help` end
     `, experimental)`; all 12 loaded `--help` outputs contain `(experimental)`, and so does
     `shape fabric publish --help`; no core `STABLE` command shows the marker.
   - Both spellings, real Fabric plugin, with `{"publish": DIST}` listed: the snapshot holds
     `publish` and `fabric publish`; renaming `--auth` on the `fabric publish` spelling only is
     reported (`sqllocks-shape-fabric: fabric publish --auth: flag was removed or renamed`).
     With `{"fabric": DIST}` listed, all 8 pairs plus `fabric` are snapshotted and
     `profile-model` (no Fabric spelling) stays experimental.
   - Renamed plugin flag: caught by `compare()` per distribution, on both spellings
     (`test_each_spelling_rejects_breaking_changes`). A listed command whose baseline was not
     rewritten is caught by `test_plugin_registry_and_live_baseline` (snapshot must equal the
     baseline), the same way the core baseline is held. Moving a command to another
     distribution key fails (`test_distribution_removal_or_transfer_breaks`).
   - Hole found: listing only the `fabric …` spelling (finding 1).

## Findings

1. **Non-blocking now; must be fixed before any Fabric command is promoted.**
   `src/shape/cli/stability.py:62-71` (`plugin_commands()`) maps only short name to Fabric
   spelling. With `STABLE_PLUGIN_COMMANDS = {"fabric publish": DIST}`, `level("publish")` is
   `experimental` and `plugin_commands()` returns only `fabric publish` (probe, this session);
   likewise `{"fabric setup": DIST}` leaves `setup-fabric` experimental. The snapshot then
   freezes one spelling, against decision 14, and `scripts/gen_exit_codes.py:55` skips every
   `fabric …` path, so that command gets no exit-code rows. No test catches it:
   `tests/cli/test_plugin_surface.py:105` only lists the short name. What to do: either add
   the reverse mapping (a listed `fabric X` also lists its short spelling) or refuse such an
   entry with a test that every key of `STABLE_PLUGIN_COMMANDS` is a top-level entry-point name
   (or `fabric`), never a `fabric X` path; add a test for the chosen rule.
2. **Non-blocking.** `scripts/gen_exit_codes.py:66` gives a listed plugin command with no entry
   in `exitcodes.CODES` a default row `1: a check failed`. Section 6.2 item 4 asks for 0, 1, 2
   or a documented verdict class; a default meaning for 1 is invented text, not documented.
   What to do: when a command is promoted, require a `CODES` entry for it (fail generation if
   absent) instead of a default.
3. **Non-blocking (by design, noted).** `tests/cli/test_plugin_surface.py:261`
   (`test_every_listed_command_and_alias_has_a_bad_input_exit_class`) and
   `:273` (`test_listed_distributions_are_in_the_existing_lockstep_check`) loop over the empty
   list and pass without asserting anything today. They are not skips and they become real on
   the first promotion; the mechanism itself is covered by the fixture-based tests. No action
   now.
4. **Non-blocking.** Section 6.2 item 5 (`--json` key test for listed commands with `--json`)
   is deferred: no command is listed. `docs/CLI_STABILITY.md` says existing `--json` keys are
   "covered by the command's own tests". What to do: the `profile-db` promotion must add the
   key-set test in the same change.
