# W-mask-3: unkeyed CLI golden and free-text boundary

Base: `origin/lane/W-mask-1` at `0629a0a8aba270774c0bf354f0653700bd2443ff`.

## Changes and findings

Tests only: added `tests/masking/test_mask_unkeyed_cli.py` and two new golden files in
`tests/masking/golden/unkeyed/`. No existing golden or regression store was changed.
The CLI is executed in separate processes, with relative input/output paths so the
committed stdout has no temporary paths. Two default-seed runs and an explicit
`--seed 42` run must produce the exact golden stdout and CSV bytes. The fixture
covers duplicate values, null cells, two masked types and untouched prose.

The free-text limit is a detection boundary, not a character count. In
`src/shape/builtins/transforms/mask.py`, `_detect` classifies whole columns from
names or profiler patterns; it does not scan prose for embedded PII. The test
keeps email, phone, SSN, IPv4, name and address text in `notes` unchanged while
email and phone columns must change. It also pins the exact limitation sentence
in `docs/MASK.md` (normalized whitespace):

> It does not look inside free text: an e-mail address inside a `notes` column is not found.

The public keyed `Masker.mask("text", ...)` API has a different scope and is not
the CLI's free-text detection policy. Production code and docs were not changed.
No ambiguity or open question remains.

## Tests first / red evidence

Before adding the new expected fixtures, ran
`pytest -q tests/masking/test_mask_unkeyed_cli.py` (exit 1):

```text
FAILED tests/masking/test_mask_unkeyed_cli.py::test_unkeyed_default_seed_matches_golden[default]
FAILED tests/masking/test_mask_unkeyed_cli.py::test_unkeyed_default_seed_matches_golden[repeat]
FAILED tests/masking/test_mask_unkeyed_cli.py::test_unkeyed_default_seed_matches_golden[42]
3 failed, 1 passed in 9.64s
```

All three failures were `FileNotFoundError` for the newly specified golden
`stdout.txt`. The existing CLI free-text boundary already passed. Captured new
fixtures from the actual unkeyed CLI; no behavior implementation was needed.

## Setup and command record

Each command ran in `/workspace/prompt-runs/checkouts/W-mask-3` except the
initial origin check. The lane used a private clone, private venv, copied private
Rust target, and `/workspace/prompt-runs/tmp/W-mask-3` as `TMPDIR`.
Python 3.11 is absent; Python 3.12 satisfies CONTRIBUTING's Python 3.11-or-newer
requirement. Third-party distributions were linked read-only from the prepared
venv; Shape and all requested plugins were installed from this checkout.

| Command | Exit | Result |
|---|---:|---|
| `git -C /workspace/shape remote -v` | 0 | Confirmed origin is sqllocks/shape |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-mask-3` | 0 | Independent checkout |
| `git remote set-url origin https://github.com/sqllocks/shape.git`; copy Git identity | 0 | Correct upstream and identity |
| `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-mask-3 origin/lane/W-mask-1` | 0 each | Required base |
| `cat AGENTS.md CLAUDE.md CONTRIBUTING.md` | 1 | AGENTS.md absent; read other two files |
| `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` | 0 | Read Decisions and section 4.3 outside checkout |
| `rg` / `sed` / `ls` / `du` / process and log inspection | 0 or 1/2 for absent guessed paths | Located code, docs, golden conventions and setup paths; no repository changes |
| `python3.12 -m venv .venv`; third-party link setup | 0 | Private environment |
| `cp -a --reflink=auto /workspace/shape/rust/shape-kernel/target rust/shape-kernel/target` | 0 | Private target |
| `PIP_NO_DEPS=1 make bootstrap` | 0 | Native kernel and core built |
| `pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | All requested source packages |
| Initial import before bootstrap finished | 1 | Expected setup incomplete; retried after success |
| Import/source-path assertions for core and all five plugins | 0 | All resolve inside private checkout |
| New-file Ruff lint / format | 0 each | Passed |
| Golden fixture recording Python script | 0 | Captured default-seed CLI stdout and CSV |

## Verification

| Command | Exit | Result |
|---|---:|---|
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | All files formatted |
| `mypy` | 0 | No issues |
| `pytest -q tests/masking tests/builtins/test_mask.py` | 0 | 96 passed |
| `SHAPE_KERNEL=python pytest -q tests/masking/test_mask_unkeyed_cli.py tests/builtins/test_mask.py` | 0 | 32 passed |
| `timeout 180 make check` (one invocation) | 124 | Bounded run timed out in the broad core coverage stage |
| `git diff --check` | 0 | Clean |

The bounded `make check` passed Ruff, mypy, compileall, Vulture, import checks,
requirements, secrets, wording, exit/failure generation, shipped data, plugin
skeletons, versions, release workflows, conformance, CLI surface and v1 checks.
The core suite had begun and was progressing when the 180-second limit expired;
no failing test was reported before timeout. This is an incomplete full-suite
result, not a full-suite pass. Changed tests and their neighbors all passed.
No gate was lowered, no test was skipped or xfailed, and no unrelated corpus was
modified. The lead will verify the complete suite in CI.

Delivery: `git add` the four lane files; `git commit -s -m
"W-mask-3: pin unkeyed CLI output and free-text boundary"`; `git push -u origin
lane/W-mask-3` (exit 0), followed by remote SHA verification (exit 0). No other
branch, tag, pull request or merge was created.

