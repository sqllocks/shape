# W-mask-2 review

Verdict: **REJECT** (1 blocking finding; small doc fix).

Head reviewed: `3cd69012cf1311595114c421cdaf3615e4f8cf7b` (expected prefix `3cd6901`: matches).
Base: `origin/lane/W-mask-1` (`0629a0a`). Diff reviewed: `git diff origin/lane/W-mask-1...HEAD`
(docs/MASK.md, docs/THREAT_MODEL.md, tests/masking/test_mask_cli_guards.py, lane status).
Plan section read: 4.3 (`shape mask`) of `origin/plan/promotion-093`; CLAUDE.md, CONTRIBUTING.md,
docs/plans/lane_status/W-mask-2.md.

## Scope judgement

- `--dry-run` already exists on this base through the generic machine layer
  (`src/shape/cli/main.py:2227` `machine.install(p)`; `mask` in `SPECS`). The builder showed it with
  `shape mask --help` and live invocations, and I reproduced it. No wiring was missing, so the
  lane has no code change. That matches the lead's answer (generic layer, no mask-specific dry
  run).
- No promotion, no STABLE entry, no surface snapshot write, no CHANGELOG change, no skip/xfail,
  no gate/tolerance/regression-store/golden change.
- Red evidence: none needed (no behaviour change). The new test pins existing behaviour
  (exit 0, nothing written, standard `shape-dry-run` document with one `-o` action) as the lead
  asked.
- `--exclude`/`--pii` scope text checked against `src/shape/cli/mask.py:159-166` (unknown name in
  every table is exit 2), `src/shape/builtins/transforms/mask.py:121-133` (exclude checked before
  pii; forced type subject to `_compatible`) and `:269-279` (an excluded FK is left alone). It is
  supported by the code.
- THREAT_MODEL rows: each cited test exists and asserts its row (`test_mask_keyed_cli.py:87,105,116`;
  `MIN_KEY_BYTES = 16` and the group/other mode check in `src/shape/masking/_core.py:23,66`).

## Commands (this sandbox, Python 3.11 venv)

| Command | Exit |
|---|---|
| `make bootstrap` then `pip install -e '.[docs]' -e plugins/shape-{domains,sqlserver,eventhubs,fabric,kafka}`, `pip install -e .` on this lane | 0 |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` (2140 files) | 0 |
| `mypy` (698 files) | 0 |
| `pytest -q -p no:cov tests/masking tests/builtins/test_mask.py tests/cli/test_ci_flags_coverage.py tests/cli/test_json_envelope.py 'tests/docs/test_reference_examples.py::test_reference_page[MASK]' tests/docs/test_reference_examples.py::test_restored_example_inventory` (118 passed) | 0 |
| `python scripts/check_doc_links.py` | 0 |
| `python scripts/check_user_facing.py` | 0 |
| probe: `shape mask nope.csv -o out --dry-run` | 2 |
| probe: `shape mask in.csv -o full --dry-run` (`full/` holds one file) prints `would write  full` | **0** |
| probe: `shape mask in.csv -o full` (same folder) | 2 (`full exists and is not an empty folder`) |
| probe: `shape mask in.csv -o o2 --dry-run --exclude nosuchcol` | 0 (real run: 2) |

Not run here: mkdocs strict build and `make check` (the full suite is proven in GitHub CI).

## Findings

1. **Blocking. `docs/MASK.md:39` and `:43-48`: the page says the dry run checks output paths and
   that an existing output folder is reported as `write`, but it never says a non-empty
   folder passes.** As the probes above show, a dry run against a non-empty output folder
   exits 0 (`would write`), while the real run refuses the same folder with exit 2. A reader
   who uses the dry run to check paths gets a false OK. Fix: in the paragraph after the exit
   codes, say the dry run does not check that an existing output folder is empty, and that the
   real run refuses a non-empty one. Optionally reword the table cell from "check input and
   output paths" to "check that the input exists". Add the non-empty case to
   `test_dry_run_plans_output_without_writing` (dry run exit 0 and nothing written) so the
   sentence has a proof. Do not change the generic planner in this lane. If the lead wants the
   dry run to refuse a non-empty folder, that is a separate behaviour change.
2. Non-blocking. `docs/MASK.md:93`: "`--pii` forces the whole column ... subject to the
   column's type being compatible". `transforms/mask.py:126-127` lets a float column take any
   forced type without the compatibility check. The sentence is still true in practice, so
   no action is required. If W-mask-3 pins this paragraph, it may say "a column whose type cannot
   hold the type is refused".
3. Non-blocking. The precedence sentence ("`--exclude` ... takes precedence over `--pii`") is
   correct in code (`transforms/mask.py:121`) but no test asserts it. If the page is
   promoted, the claim table needs a test for it.
4. Non-blocking. The lane status says `make check` was interrupted by the 600-second limit and
   names one umask-sensitive failure
   (`tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left`)
   that this lane does not touch. That is reported honestly. CI is the proof.
