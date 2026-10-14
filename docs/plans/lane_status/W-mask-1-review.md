# W-mask-1 review

Reviewer: independent (did not build the lane). Reviewed head
`07c936b568f439bb49a9c3a5b2c0f10cddc41d21` against `origin/main`
`c10923be9278bc63f2713ac0ed32a548b59a7afb` (`git diff origin/main...HEAD`). Scope checked against
promotion plan section 4.3 "Changes before freezing" items 1 to 3 (item 4 not done, `--dry-run`
left for W-mask-2, as ruled).

## Verdict: APPROVE

All three items are done, tested, and the red evidence reproduces. No blocking findings.

## Commands and exit codes

Python 3.11 venv, `make bootstrap`, then `pip install -e` of shape-domains, shape-sqlserver,
shape-eventhubs, shape-fabric, shape-kafka (exit 0).

| Command | Exit | Notes |
|---|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 | |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 | |
| `mypy` | 0 | 698 files |
| `python scripts/cli_surface.py --check` | 0 | 0 breaking changes, 0 notes; the mask flag set is unchanged (only `--seed`'s default and help text changed, which the surface does not record) |
| `pytest -q tests/builtins tests/masking tests/cli -p no:cov` | 1 | 1128 passed; 1 failed + 2 errors, all in `tests/builtins/test_abfss_sink_live.py` (module is `pytestmark = pytest.mark.live`, needs a live account) |
| same, with `-m "not emulator and not live and not heavy"` | 0 | 1126 passed, 1 skipped, 15 deselected |
| `pytest -q tests/docs/test_reference_examples.py -k MASK -p no:cov` | 0 | 1 passed: the MASK.md examples run as written |
| `pytest -q tests/security/test_p7_04_review.py tests/regressions/test_hunt2_cli.py tests/regressions/test_aud_cli.py -k mask -p no:cov` | 0 | 4 passed (existing mask guards) |
| red: new tests against `origin/main`'s `src/shape/cli/mask.py` | 1 | 4 failed, 2 passed: exactly the four the lane reports; file restored afterwards |
| `pytest -q tests/masking/test_mask_cli_guards.py -p no:cov` on head | 0 | 6 passed |

Diff scan: no skip, xfail, deselect, gate or tolerance change. CHANGELOG edit is only under
`## Unreleased` (one hunk, replacing "No unreleased changes.").

## Adversarial probes (on head, CLI)

| Probe | Result |
|---|---|
| `--key-env K --seed 42` (explicit seed equal to the default) | exit 2, one `shape: error:` line, no output folder created |
| `--key-file k.key --seed=42` | exit 2 |
| no key, no `--seed` | exit 0; output identical to `--seed 42` and to `origin/main`'s default output |
| keyed run, no `--seed` | exit 0; output identical to `origin/main`'s keyed output |
| re-run into a folder that now has files | exit 2, one error line, files untouched |
| `-o` naming an existing file | exit 2 ("exists and is not an empty folder") |
| `-o` folder holding only a dot-file | exit 2 (refused; correct, `iterdir` sees hidden files) |
| `-o` equal to the input folder | exit 2, the older "would overwrite the input files" message wins |
| `--json` run on the identifier-like CSV | stderr empty |

`--seed` detection: the default is now `None` and 42 is applied only after the check
(`src/shape/cli/mask.py:150-155`), so an explicit `--seed 42` with a key is refused and a plain
unkeyed run still gets 42. The non-empty check (`mask.py:148-149`) runs before the key is read,
before any table is read and before `out_dir.mkdir`, so nothing is written on refusal.

Warning suppression: the filter (`mask.py:170`) is `ignore` for `UserWarning` whose message
contains `--string-columns`, scoped by `catch_warnings` to the `profile(...)` call only. Checked
that the compiled pattern matches the hint and not other text; the only emitter of that text is
`src/shape/profile/reference/readers.py:534`. Other profiler warnings still reach the user. The
hint's content (identifier-like integers lose leading zeros) does not apply to `mask`, which
reads every CSV value as text (`_read`).

## Findings

None blocking.

1. **Non-blocking — the mask regression harness can no longer re-run into the same output
   folder.** `benchmarks/vs_refengine/mask_1to1/verify.py:370` creates
   `$BENCH_OUT_DIR/mask_1to1/<case>` with `exist_ok=True` (default `BENCH_OUT_DIR` is
   `~/bench-out`, persistent), and `run_shape` (`verify.py:229-233`) runs `shape mask ... -o
   <case>/shape` with `check=True`. A second run with the same `BENCH_OUT_DIR` now gets exit 2
   and a `CalledProcessError`. To do: remove `<case>/shape` (and the baseline's folder) before
   each run in `run_case`, or note in the harness README that `BENCH_OUT_DIR` must be fresh.
   Can be a follow-up; not on the CI path.
2. **Non-blocking — the two new refusals are not on the pages that list mask's behaviour.**
   `docs/EXIT_CODES.md` (section `shape mask`) has the generic exit-2 line only, which covers
   them; but `docs/MASK.md:172` "Changes to existing behaviour" does not mention that a non-empty
   `-o` and `--seed` with a key are now refused (CHANGELOG does). Plan 4.3's frozen exit-2 list
   should also gain both cases. To do: one bullet each in MASK.md's changes section, now or in
   W-mask-2/W-mask-4.
3. **Non-blocking, pre-existing — an empty `--key-env ""` counts as "no key".** `_key`
   (`mask.py:106,110`) and the new guard (`mask.py:150`) test truthiness, so
   `--key-env "" --seed 7` exits 0 and runs unkeyed with seed 7, while `--key-env ""` alone also
   silently runs unkeyed. Not introduced by this lane; worth `is not None` plus a refusal of an
   empty variable name when the key handling is next touched.

## Not counted

The full core-stage coverage figure and the docs/docs_site failures in this sandbox were not
re-run here; they are known sandbox artefacts that also fail on clean `origin/main`. The lead
proves the full suite in GitHub CI.
