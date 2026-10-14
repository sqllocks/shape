# W0-available review

Reviewer: independent (did not build this lane). Head reviewed:
`197693f049836ec2e78a395f8f1157472a17af2a` (matches the expected head).
Diff: `git diff origin/main...HEAD`, main = `c10923be9278bc63f2713ac0ed32a548b59a7afb`.
Judged against PROMOTION_PLAN_0.9.3.md (Decisions, section 1.3) on
`origin/plan/promotion-093` and the three lead answers in `W0-available.md`.

## Verdict: REJECT

Three blocking findings. B1 breaks the CI `test` job on its two Windows legs. B2
and B3 let a page escape the check. Each fix is small. The rest of the lane is in
good shape: the rules, ratchet, exit codes, message format, wiring and fixture
coverage match section 1.3 and the lead answers.

## Commands and exit codes

Environment: `python3.11 -m venv .venv`, `make bootstrap` and the five plugin
installs with `.[docs]`: exit 0 (rustc 1.97.0).

| Command | Exit | Result |
|---|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2141 files already formatted |
| `mypy` | 0 | no issues in 698 source files |
| `python scripts/check_available.py` | 0 | `AVAILABLE: 0 problem(s)` |
| `python scripts/check_available.py --require-base` | 0 | `AVAILABLE: 0 problem(s)` |
| `pytest -q tests/release/test_check_available.py -p no:cov` | 0 | 73 passed in 60.87s |
| `git diff --check origin/main...HEAD` | 0 | clean |
| Ratchet regeneration (every public `docs/**/*.md` outside plans/talks matching `^Status:\s*available`, sorted) compared with `tests/docs/available_legacy.txt` | n/a | identical: 71 = 71, same order |
| AST import scan of `scripts/check_available.py` | n/a | stdlib only, plus the in-repo `check_docs_owners` (stdlib only: argparse, re, pathlib), as section 1.3 asks |
| Red check, worktree: checker deleted, `pytest -x` on the test file | 1 | fails as expected |
| Mutant: growth loop disabled, `-k growth` | 1 | 1 failed (caught) |
| Mutant: demotion message disabled, `-k demotion` | 1 | 1 failed (caught) |
| Mutant: rule 4 disabled, `-k owner` | 1 | 2 failed (caught) |
| Simulated Windows default codec (`Path.read_text` defaulting to cp1252), `check_available.main([])` on the repo | 2 | `AVAILABLE: error: 'charmap' codec can't decode byte 0x81` (B1) |
| Adversarial fixture probes (`scratchpad/probe/probe.py`, `probe2.py`), results below | n/a | see B2, B3, N1 to N4 |

I did not run the full `make check`. The core-stage coverage figure, the docs
tests and the streaming timing test are known sandbox failures (see the brief).
The lead proves the full suite in GitHub CI.

Wiring: `Makefile:25` runs `$(PYTHON) scripts/check_available.py` directly after
`check_v1_done.py`. `.github/workflows/ci.yml:63-64` sits in the `test` job directly
after `check_v1_done.py`. It fetches `main` into `refs/remotes/origin/main` and
runs `--require-base`. `fetch-depth: 0` is set on the checkout (ci.yml:32). The
step runs on every matrix leg, including Windows (see B1).
CHANGELOG: changed only under `## Unreleased` (`### Added`). No skip, xfail,
coverage gate or tolerance changed. `src/` and `pyproject.toml` are unchanged.
The red evidence is genuine: removing the checker fails the suite, and the three
mutants are each caught.

## Findings

### Blocking

**B1. The CI `test` job will fail on Windows. `scripts/check_docs_owners.py:32`, reached from `scripts/check_available.py:323`.**
`inventory()` reads every docs page with `path.read_text()` and no encoding. Until
now it ran only in `docs.yml` on Linux. This lane runs it in the `test` matrix,
which includes `windows-latest` on 3.11 and 3.14 (ci.yml:24, :64). The default
codec there is cp1252, and two public pages contain bytes that cp1252 cannot
decode: `docs/HISTORY.md` (0x81) and `docs/plugins/behavior.md` (0x90). The
resulting `UnicodeDecodeError` is a `ValueError`, so the checker prints
`AVAILABLE: error: ...` and exits 2. I reproduced this by forcing the cp1252
default (table above). Fix: pass `encoding="utf-8"` in `inventory()`, which is a
one-word change. Setting `PYTHONUTF8=1` on that one CI step also works, but the
encoding fix is better. Also add a test that runs the checker with
`PYTHONUTF8=0` on a page holding non-cp1252 text.

**B2. A whitespace variant of the fence hides a command from both the check and the reader. `scripts/check_available.py:29` against `scripts/mkdocs_hooks.py:234`.**
The checker's `FENCE` pattern, `<!--\s*experimental:(start|end)\s*-->`, accepts
`<!--experimental:start-->` and `<!--  experimental:start  -->` on their own lines
and drops the text between them. The hook renders a warning box only when a
line equals `<!-- experimental:start -->` exactly. Probe: an available page with
`<!--experimental:start-->`, then ``Use `shape mask`.``, then
`<!--experimental:end-->` exits 0. The check skips the command, and the site shows
it with no warning, because the delimiters are invisible HTML comments. This is the
same class of escape the builder already fixed for inline delimiters. Fix: make
both sides use one definition. Either the checker accepts only the exact
standalone strings, or both share one regex. Then reject (P-BAR 1, malformed
experimental fences) any `experimental:` comment that is not the exact form.
Add a fixture for the no-space and extra-space forms.

**B3. A page labelled available in any non-canonical way is not checked at all. `scripts/check_available.py:25` and `:68`.**
`STATUS` is case-sensitive and anchored at column 0. Only the first `Status:`
line counts, and YAML front matter is ignored. With no section in AVAILABLE.md
and the page not on the legacy list, each of these exits 0, with
`shape mask` on the page:
`status: available.`, `Status: Available.`, `**Status:** available.`,
` Status: available.` (leading space), a front-matter-only `status: available`,
and a page whose first `Status:` line says experimental and a later one says
available. A reader sees "available" on each of these pages, yet they are
neither exempt by the list nor held to rules 1 to 5. No other repository check
enforces the canonical line (searched `scripts/` and `tests/docs/`). Fix: fail
closed. Report any line matching `(?im)^\W*status\W*:\W*available` that is not
the canonical `^Status: available` form, and any front-matter `status: available`
that the body does not repeat canonically (the six tutorials repeat it, so they
pass). Choose a rule for pages with more than one `Status:` line: treat the page
as available if any page-level line says so, or require exactly one. Add fixtures
for each case above.

### Non-blocking (recommended; for the lead to schedule)

**N1. Demotion escapes if the AVAILABLE.md section is deleted in the same change. `scripts/check_available.py:314-316`.**
The demotion rule works both ways that it was written for. A page in
AVAILABLE.md that turns experimental or is deleted fails P-BAR 1, and a legacy
page that turns experimental fails the ratchet until it is removed from the list.
However, it reads only the current AVAILABLE.md. Deleting the page's section and
relabelling the page together exits 0 (probe "demote by deleting section"). The
plan calls this rule the twin of the CLI rule, which compares against a baseline.
Recommend reusing `baseline()` to read the AVAILABLE.md headings at the merge base
and treating a removed heading as a demotion. This has no exposure until the first
promotion section lands, so fix it before then.

**N2. A `since:` value other than the current version exempts a page from rule 5 forever. `scripts/check_available.py:404`.**
This follows lead answer 1 as written. Still, a section with `since: 0.0.1` and
no `### Promoted` line anywhere exits 0. Also note that `pyproject.toml` still
says `0.9.2`, so a page promoted now with `since: 0.9.4` is not checked until the
version bump. Suggest also requiring that, when `since` is not the current
version, that version's CHANGELOG section exists and names the page. A CHANGELOG
heading carrying a date (`## 2.4.6 (2026-10-20)`) is not matched and falls back to
Unreleased. The repo uses bare headings today, so this is latent.

**N3. Rule 3 rejects every CI transcript the repo produces today. `scripts/check_available.py:246-249`.**
The checker requires `shape` (or `shape_version`) and an ISO `date` in JSON
evidence. None of the 39 transcripts under `docs/evidence/transcripts/` have
either field. The Fabric transcript the plan cites
(`...38400931371-c37ff1b1dadb-...-publish-targets.json` on `10c4cd63`) has neither
field either. Its version appears only in the stdout banner (`Shape v0.9.1`). It
also has no `paths`, so the freshness path cannot apply. This fails closed and
matches one reading of 1.2. Section 1.2, however, lists only `run_id`, `commit`,
`exit_code` and `status` for a CI transcript. The lead should decide whether the
transcript generator gains these fields or the check derives them (banner
version, commit date, `paths=` from the workflow). Otherwise no Fabric or service
page can be promoted with existing evidence.

**N4. Command detection misses some mentions and flags plain prose. `scripts/check_available.py:27`.**
Missed, each probe exiting 0 with an unstable command: `Shape mask`, `shape`
and `mask` split across a line break, and a global option before the command
(`shape --quiet mask`). Flagged wrongly: lowercase prose. Across the 71 legacy
pages the pattern finds `shape is`, `shape and`, `shape rather`, `shape above`,
`shape identity`, `shape files` and others. Each would fail rule 1 on promotion,
and "add it to Covers" cannot fix them. This fails closed, but it will cost
promotion lanes time. Consider matching only command names from the parser
surface (`scripts/cli_surface.py`'s snapshot) and allowing options before the
command.

**N5. Small items.**
- The growth check compares with `merge-base HEAD origin/main`. On a push to
  `main` that is HEAD itself, so direct pushes to main are never checked for
  growth. PRs are checked. This is acceptable under lead answer 2; record it on
  the page.
- An empty line in `available_legacy.txt` produces
  `AVAILABLE: : ratchet: no longer available`, which names an empty page. Prefer
  a clearer "blank line" message.
- `STABLE_PLUGIN_COMMANDS` is read with `set(...)`. If W-plug keys it by
  two-word names (`fabric publish`), those keys will never equal the one-word
  token that `COMMAND` captures. Agree the key shape with W-plug.
- An exception raised while executing `stability.py` (for example a
  `SyntaxError`) is not caught, so the checker exits 1 with a traceback instead
  of 2.
- The hook rewrites fences inside fenced code blocks too. The checker does the
  same, so the two stay consistent, but a page cannot show the delimiter syntax in
  a code block. Only inline backticks work, as AVAILABLE.md already says.

## What is right

- Exit codes 0/1/2 and the `AVAILABLE: <page>: P-BAR <n>: <message>` format hold
  throughout. Ratchet messages use the exact wording from lead answer 2.
- Rules 1 to 5 are implemented as 1.3 describes. Rule 4 calls `inventory()`
  directly and still counts notes inside fences. Rule 5 follows lead answer 1.
  AVAILABLE.md sections follow lead answer 3.
- Unclosed, nested, same-line, unmatched and uppercase fences all fail or are
  ignored safely (probes). A `Status: available` inside a code block on a page
  with no status line fails closed.
- Ratchet: the list is sorted and generated, and equals a fresh regeneration.
  Growth fails, shrinking passes, a stale entry fails, introducing the file needs
  no comparison, and `--require-base` exits 2 without a baseline.
- `STABLE_PLUGIN_COMMANDS` is read only if present
  (`getattr(module, "STABLE_PLUGIN_COMMANDS", ())`).
- AVAILABLE.md follows V1_DONE.md. It has the intro, states both limits from
  1.3 and has no page sections.
- The fixture tree covers every rule the brief lists, plus fence, ratchet and
  evidence edge cases. The tests build real temporary Git histories.

## To do before approval

1. B1: pass `encoding="utf-8"` in `inventory()` and add a non-cp1252 test.
2. B2: give the checker and the hook one fence definition and reject
   non-canonical fence comments, with fixtures.
3. B3: fail closed on non-canonical or front-matter "available" labels, with
   fixtures.

N1 to N5 can follow in this lane or a later one, at the lead's choice. Fix N1
and N3 before the first promotion section lands.
