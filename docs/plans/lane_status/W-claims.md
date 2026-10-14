# W-claims lane status

Status: blocked; stopped under the prompt's ambiguity/contradiction rule.

## Exact question

How should W-claims record claim tables for the five currently experimental pages in docs/AVAILABLE.md while keeping scripts/check_available.py passing and leaving every page status unchanged? Should the ledger/checker gain an explicit draft-table state in a separately authorized change, or should these tables wait outside the ledger until a later promotion lane?

## Evidence and scope

The prompt requires claim tables for CANARIES, REPRODUCIBILITY, DETERMINISM, QUICKSTART, SIGNING and VERIFY, says “Do not mark any page `Status: available`” and “tables only”, and requires scripts/check_available.py to pass. On origin/lane/W0-available, five of these pages say `Status: experimental`; only QUICKSTART says available.

`scripts/check_available.py:315` iterates every parsed ledger section and rejects a page whose status is not available, emitting “a page once available is not demoted”. This rejection applies even to an `open W-canary-2` row. The promotion plan's section 1.3 likewise specifies one section per available page and rejects ledger pages saying experimental. Its section 4.1 splits canary claim tables (W-canary-2) from promotion (W-canary-3); sections 4.4–4.6 split claim tables from their other promotion prerequisites. The requirements therefore cannot all be met by a tables-only change on the specified base.

No feature, test, ledger, page status, checker, release note, or plan file was changed. Only this blocker report is committed. The lead confirmed there is no existing authorization resolving the contradiction and instructed this lane to stop and push the exact question.

## Commands and results

- `cd /workspace/shape && git remote get-url origin && git config user.name && git config user.email` — exit 0; verified origin https://github.com/sqllocks/shape.git and copied repository author identity.
- Read the cloud environment runtime skill and networking reference; environment status reports connected/running. `cat /etc/codex/network-policy.json` — exit 0.
- `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-claims` — exit 0.
- Set isolated clone origin and repository author identity with `git remote set-url` and `git config` — exit 0.
- `git fetch origin` — exit 0.
- `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093` — exit 0.
- `git checkout -B lane/W-claims origin/lane/W0-available` — exit 0.
- Read CLAUDE.md, CONTRIBUTING.md, docs/AVAILABLE.md, scripts/check_available.py, Makefile, and the fetched plan's Decisions, 1.3, 4.1, 4.4, 4.5 and 4.6 sections with `cat`, `rg`, `git show` and `sed` — exit 0. No AGENTS.md was found by the tracked-file search.
- `rg -n '^Status:|claim:' docs/CANARIES.md docs/REPRODUCIBILITY.md docs/DETERMINISM.md docs/QUICKSTART.md docs/SIGNING.md docs/VERIFY.md` — exit 0; five experimental target pages confirmed.
- `python3 scripts/check_available.py` on the unchanged ledger — exit 0, `AVAILABLE: 0 problem(s)`.
- The same check after temporarily appending one syntactically valid CANARIES section with an `open W-canary-2` row — exit 1, `AVAILABLE: docs/CANARIES.md: P-BAR 1: a page once available is not demoted`; one problem. A Python `finally` restored the original ledger. The reproduction wrapper exited 0.

Red evidence above demonstrates the requirement contradiction, not a new behavior test. Setup, lint, mypy, focused tests and `make check` were not run because the prompt explicitly requires stopping rather than guessing when the plan contradicts code. No implementation was authorized after discovering this blocker.
