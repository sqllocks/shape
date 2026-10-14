# W-mask-3 review

Reviewer: independent review session (did not build this lane).
Head reviewed: `679859be21211b6bad1eb58256f8735cffa4c3be` (expected prefix `679859b`: matches).
Base: `origin/lane/W-mask-1` (`0629a0a`). Only what is new on top of that lane was reviewed.
Plan: `origin/plan/promotion-093`, section 4.3.

## Verdict: APPROVE

Tests only: `tests/masking/test_mask_unkeyed_cli.py` and two new golden files under
`tests/masking/golden/unkeyed/`. No existing golden, regression store or corpus changed, and
production and docs are unchanged. The golden test runs `python -m shape mask` in a subprocess
with relative paths. It runs the default seed twice and `--seed 42` once, and compares stdout and
the masked CSV byte for byte. It also asserts an empty stderr, which keeps W-mask-1's pandas-hint
fix in place. The builder took the "free-text limit" to be the documented detection boundary
(`docs/MASK.md:79`: "It does not look inside free text ..."). The CLI's `_detect`
(`src/shape/builtins/transforms/mask.py:114-135`) classifies whole columns only, and neither the
code nor the page has a numeric free-text limit, so that reading is right. The test pins both
sides: the `notes` column (e-mail, phone, SSN, IPv4, name, address in prose) must come out
unchanged, and the page sentence must stay. No promotion, STABLE entry, snapshot write or
CHANGELOG change. No skip, xfail or lowered gate.

## Commands (Python 3.11 venv, `make bootstrap` + docs/plugin installs: exit 0)

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `mypy` | 0 | 698 source files, no issues |
| `pytest -p no:cov -q tests/masking tests/builtins/test_mask.py` | 0 | 96 passed |
| `SHAPE_KERNEL=python pytest -p no:cov -q tests/masking/test_mask_unkeyed_cli.py` | 0 | 4 passed (Python kernel gives the same bytes) |

## Mutation checks (local only, reverted)

| Mutation | New tests |
|---|---|
| CLI default seed 42 -> 43 (`src/shape/cli/mask.py:155`) | 2 failed (`default`, `repeat`; `--seed 42` still passes, as it should) |
| `_detect` masks the `notes` column | 4 failed |
| `docs/MASK.md:79` sentence changed | 1 failed (free-text test) |

## Findings

Blocking: none.

Non-blocking:

1. `tests/masking/golden/unkeyed/stdout.txt`: the golden pins the human summary text
   (`Masking result`, `Written 1 CSV files to out/`). `CLI_STABILITY.md` says output layout is not
   frozen, so a wording change will need this file re-recorded. That is acceptable for a CLI golden
   test, but there is no regeneration helper or note. Add a one-line comment in the test saying how
   to re-record it (the repo has no single golden-update convention; `scripts/golden_bytes.py
   --update` exists only for the generation corpus).
2. `tests/masking/test_mask_unkeyed_cli.py:33`: the parametrize ids `default`/`repeat` run the
   same arguments twice in separate processes. That is a deliberate repeat-run check; a comment
   would make the intent clearer.
3. The docs sentence is matched exactly after whitespace normalization, so any rewording of that
   line fails the test. That is what the task asked for. W-mask-2 (page edits) must keep the
   sentence, or update the test in the same commit.
