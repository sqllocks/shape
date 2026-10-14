# W0-available status

Blocked by the instruction to stop on ambiguity. The two earlier lead answers
are resolved and recorded below. No implementation or setup was started.

## Exact question

What exact Markdown syntax must `docs/AVAILABLE.md` use to bind each claim
row set to a docs page and declare its `since:` version? Section 1.3 says
“one section per available page” and specifies five row cells, but provides
no page-key or `since:` syntax. May I implement the canonical form below,
with a repository-relative page path in the level-two heading and a plain
`since:` line immediately beneath it?

```markdown
## docs/CANARIES.md

since: <version>

| Claim | Statement | Proof | Evidence | Status |
|---|---|---|---|---|
| AV-CANARIES-01 | claim (short) | `ci:ci.yml/test` | none | done |
```

Please confirm that form or provide the required alternative. This is
needed to associate rows with page anchors, apply the demotion rule, and
limit P-BAR 5 to the intended page/version. `docs/V1_DONE.md` has thematic
section headings and no `since:` field, so it does not establish this syntax.

## Lead answer 1: P-BAR 5

- This release is exactly the version in `pyproject.toml`, read at check time.
  Do not hard-code the target release.
- Use the `## <version>` CHANGELOG section; if absent, use `## Unreleased`.
- Apply rule 5 only to page row sets with `since:` equal to that version.
- Cover a Promoted line in the numbered section, fallback to Unreleased,
  a missing line failing P-BAR 5, and another version being exempt.

## Lead answer 2: shrink-only ratchet

- Generate `tests/docs/available_legacy.txt` once from today's available docs
  pages, sorted, one path per line. This is the exemption list.
- Always check exactness without git: each entry exists and still says
  available. Otherwise fail with `AVAILABLE: <page>: ratchet: no longer
  available; remove it from tests/docs/available_legacy.txt`. Unlisted
  available pages must pass rules 1 to 5.
- Read the same file at `git merge-base HEAD origin/main` using `git show`.
  A current entry absent from that baseline fails with `AVAILABLE: <page>:
  ratchet: the legacy list may only shrink`. If the file does not exist at
  the base, there is no growth check.
- If git, origin/main, or the merge base is unavailable, print to stderr
  `AVAILABLE: ratchet growth not checked: no baseline (<reason>)` and pass
  for that reason by default. `--require-base` makes this an error, exit 2.
- `make check` uses no flag. Only the CI test job uses `--require-base`
  and fetches main sufficiently to make the baseline available.
- Test stale entries, unlisted available pages, growth and removal in a
  temporary git fixture, no file at base, and unavailable-base behavior
  both with and without the flag.

## Commands and observed results in this session

All repository shell commands ran from `/workspace/shape` after confirming
origin as `https://github.com/sqllocks/shape.git`. No plan branch was merged
or committed. No network operation failed; no retries were needed.

- Before repository work: the first attachment download failed because the
  workspace was starting. Read the cloud runtime skill, waited for the
  environment (ready), then downloaded the attachment successfully.
- Workspace discovery (`pwd; rg --files ... /workspace`): exit 0.
- Read the uploaded request and attempted `cat /workspace/shape/AGENTS.md`:
  exit 1 because AGENTS.md is absent; the request was read successfully.
- Read cloud environment status and networking reference: successful tool
  results. Read `/etc/codex/network-policy.json`, `git status --short`, and
  `git branch --show-current`: exit 0, clean tree, initial branch `work`.
- `cd /workspace/shape; git remote get-url origin; git fetch origin`:
  exit 0, verified the required repository.
- Initial branch setup invocation: `git checkout -B lane/W0-available
  origin/lane/W0-available` failed (exit 128) because the default fetch
  did not include the lane ref. The following `git merge origin/main`
  reported already up to date. Fetch of the plan ref and extraction with
  `git show ... > /tmp/PROMOTION_PLAN.md` succeeded. Invocation exit 0.
- `git fetch origin lane/W0-available:refs/remotes/origin/lane/W0-available`:
  exit 0.
- Repeated checkout of the lane succeeded, merge of origin/main reported
  already up to date. Read CLAUDE.md, CONTRIBUTING.md, cli_surface.py,
  stable_api_compat.py and the plan: invocation exit 0 (output truncated).
- Focused read of the plan opening, Decisions and complete section 1.3,
  check_v1_done.py, check_docs_owners.py, V1_DONE.md, earlier lane status,
  and cli/stability.py: exit 0. Section 1.3 was visible in full.
- `git status --short`, `git log -3 --format=...`, identity reads and
  focused plan search succeeded. Repository `rg --files -g AGENTS.md ...`
  found no AGENTS.md (exit 1); invocation exit 1. Tree was clean, lane
  head was `513ff2e`, identity was configured.

## Checks and changes

- Baseline `make check`: not run; ambiguity requires stopping before setup.
- Red tests: not written or run.
- Final `make check`: not run.
- Changed only this lane status file. No version, dependency file,
  regression store or golden corpus was changed.
- Delivery commands and their observed results are reported in the final
  chat response; this avoids claiming results before running them.
