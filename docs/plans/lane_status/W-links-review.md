# W-links review

Reviewer: independent (did not build this lane). Head reviewed: `08f5dce2423bf185f761cdbc3cc0466d34704d38`
(matches the expected prefix), diff `origin/release/0.9.3...HEAD` (base `a8e476ca`).

## Verdict: APPROVE

The lane does what was asked: own-repository `blob/main` and `tree/main` links are checked
against the checkout by a stdlib script that runs in the same job before lychee, those URLs
alone are excluded from the network check, lychee retries with back-off, no status code is
accepted, `fail: true` is kept, and the earlier `--root-dir` and `site_url` exclusion are intact.

## Commands

| Command | Exit | Result |
| --- | --- | --- |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2152 files formatted |
| `mypy` | 0 | no issues in 701 files |
| `python scripts/check_release_workflows.py` | 0 | OK |
| `python -m pytest -p no:cov -q tests/docs/test_repo_links.py` | 0 | 10 passed |
| `python -m pytest -p no:cov -q tests/docs/test_launch_site.py tests/docs/test_repo_links.py` | 0 | 16 passed |
| `python -m pytest -p no:cov -q tests/docs -x` | 1 | stops at `test_brand.py` (playwright not installed; sandbox, not this lane) |
| `python -c yaml.safe_load(...)` on `.github/workflows/docs-links.yml` | 0 | parses |
| `actionlint 1.7.7 -shellcheck= .github/workflows/docs-links.yml` | 0 | no findings (base also 0) |
| `mkdocs build --strict -d <scratch>/site` | 0 | real site built |
| `python scripts/check_repo_links.py --site <scratch>/site` | 0 | 0 broken; 383 own-repo hrefs, 30 unique URLs |
| same, after temporarily renaming `CHANGELOG.md` | 1 | "2 broken repository link(s)" (restored afterwards) |

URL-variant probes (one link per page, script against this checkout):

| Link | Exit |
| --- | --- |
| `.../blob/main/NOPE.md?plain=1` | 1 |
| `.../blob/main/NOPE.md#L1` | 1 |
| `.../blob/main/nope/` and `.../tree/main/nope/` (trailing slash) | 1 |
| `.../blob/main/./NOPE` | 1 |
| `.../blob/main/%4EOPE.md` (percent-encoded) | 1 |
| `../outside`, `%2Foutside` (escape, in the lane's tests) | 1 |
| `/raw/main/...`, other branches, other repositories, upper-case host | not matched by the script **and** not excluded from lychee, so still network-checked |

Two own-repo occurrences outside `href` (search index JSON and a highlighted code span on
`CI/index.html`) are not checked by lychee either (verbatim/code text is skipped by default),
so no URL is excluded without being checked locally.

Accepted-status check: no `--accept` anywhere in the workflow (and the lane test asserts this).
lychee retries 429 and 5xx responses by default; `--max-retries 3 --retry-wait-time 5` gives the
back-off. No workflow was dispatched.

## Findings

None blocking.

1. Non-blocking, `scripts/check_repo_links.py:43`: existence is checked on the filesystem,
   so a link to an untracked or ignored path that exists in the CI checkout (for example
   `blob/main/site/...` after `mkdocs build`, or `.venv`) passes locally but would 404 on
   GitHub. Probe: `blob/main/site/x.html` and `blob/main/.venv` exit 0. Consider checking against
   `git ls-files` / `git ls-tree HEAD` instead of `Path.exists()`.
2. Non-blocking, `scripts/check_repo_links.py:39`: the prefix match is on the raw attribute
   value, while lychee trims it before applying `--exclude`; an `href=" https://github.com/sqllocks/shape/blob/main/x"`
   (leading whitespace) is neither checked locally nor checked by lychee. MkDocs does not emit
   this; a `.strip()` before the prefix test would close it.
3. Non-blocking, `scripts/check_repo_links.py:25`: only `href`/`src` are parsed; lychee also
   reads `srcset` and similar attributes. None in the built site today.
4. Non-blocking, `.github/workflows/docs-links.yml:21`: the new comment sits after the step it
   describes and directly above the unrelated `--root-dir` comment; moving it above line 20 would
   read better.
