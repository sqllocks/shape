# W-links lane status

Built on `origin/release/0.9.3` at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`, in an independent clone. Only `lane/W-links` is authorized for push. The promotion plan was read through `git show`, not copied or merged.

## Changes

- Added a stdlib HTML checker for this repository's `/blob/main/` and `/tree/main/` links. It checks every generated HTML page (including 404), decodes URL paths, ignores query strings/fragments, and rejects missing paths and paths escaping the checkout.
- The external-link workflow runs that check after the strict build and excludes only those locally verified GitHub URLs from lychee. Other repositories, branches, and GitHub endpoints stay subject to network checks.
- Lychee retains `--max-retries 3`, now with `--retry-wait-time 5` back-off. `fail: true` and accepted status behavior remain unchanged; neither 429 nor 503 is accepted as success.
- Added CLI/workflow regression tests covering present/missing paths, nested/404 pages, encoded filenames, traversal, scope, and usage errors.

## Verification

The real strict site build passed. The new checker passed over 175 generated HTML pages containing 383 own-repository URLs, with zero missing paths. The touched test suite and adjacent existing site/link-check suite passed: 30 tests.

Setup uses Python 3.12.14 because Python 3.11 is not installed. Third-party packages were reused through symlinks in a local venv, excluding all Shape packages and original editable paths; own core/plugins are installed from this clone. Rust was found under `/workspace/.onboarding`.

## Red evidence

Command: `.venv/bin/python -m pytest -q tests/docs/test_repo_links.py` (exit 1).
Tests written before implementation: 9 failed, 1 passed.

```text
FFFFFFFF.F                                                               [100%]
=================================== FAILURES ===================================
_______________ test_existing_repository_paths[blob-a file.txt] ________________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_existing_repository_paths0')
kind = 'blob', path = 'a file.txt'

    @pytest.mark.parametrize("kind,path", [("blob", "a file.txt"), ("tree", "folder")])
    def test_existing_repository_paths(tmp_path: Path, kind: str, path: str) -> None:
        site = tmp_path / "site"
        site.mkdir()
        target = tmp_path / path
        if kind == "tree":
            target.mkdir()
        else:
            target.touch()
        encoded = path.replace(" ", "%20")
        (site / "index.html").write_text(
            f'<a href="{PREFIX}{kind}/main/{encoded}?plain=1&amp;x=2#anchor">source</a>'
        )
        result = run_check(tmp_path, site)
>       assert result.returncode == 0, result.stdout + result.stderr
E       AssertionError: /workspace/prompt-runs/checkouts/W-links/.venv/bin/python: can't open file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory
E         
E       assert 2 == 0
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:39: AssertionError
_________________ test_existing_repository_paths[tree-folder] __________________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_existing_repository_paths1')
kind = 'tree', path = 'folder'

    @pytest.mark.parametrize("kind,path", [("blob", "a file.txt"), ("tree", "folder")])
    def test_existing_repository_paths(tmp_path: Path, kind: str, path: str) -> None:
        site = tmp_path / "site"
        site.mkdir()
        target = tmp_path / path
        if kind == "tree":
            target.mkdir()
        else:
            target.touch()
        encoded = path.replace(" ", "%20")
        (site / "index.html").write_text(
            f'<a href="{PREFIX}{kind}/main/{encoded}?plain=1&amp;x=2#anchor">source</a>'
        )
        result = run_check(tmp_path, site)
>       assert result.returncode == 0, result.stdout + result.stderr
E       AssertionError: /workspace/prompt-runs/checkouts/W-links/.venv/bin/python: can't open file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory
E         
E       assert 2 == 0
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:39: AssertionError
____________ test_missing_path_fails_in_every_html_page[index.html] ____________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_missing_path_fails_in_eve0')
page = 'index.html'

    @pytest.mark.parametrize("page", ["index.html", "404.html", "nested/page.html"])
    def test_missing_path_fails_in_every_html_page(tmp_path: Path, page: str) -> None:
        site = tmp_path / "site"
        html = site / page
        html.parent.mkdir(parents=True)
        html.write_text(f'<img src="{PREFIX}blob/main/missing.png">')
        result = run_check(tmp_path, site)
>       assert result.returncode == 1
E       assert 2 == 1
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:49: AssertionError
_____________ test_missing_path_fails_in_every_html_page[404.html] _____________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_missing_path_fails_in_eve1')
page = '404.html'

    @pytest.mark.parametrize("page", ["index.html", "404.html", "nested/page.html"])
    def test_missing_path_fails_in_every_html_page(tmp_path: Path, page: str) -> None:
        site = tmp_path / "site"
        html = site / page
        html.parent.mkdir(parents=True)
        html.write_text(f'<img src="{PREFIX}blob/main/missing.png">')
        result = run_check(tmp_path, site)
>       assert result.returncode == 1
E       assert 2 == 1
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:49: AssertionError
_________ test_missing_path_fails_in_every_html_page[nested/page.html] _________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_missing_path_fails_in_eve2')
page = 'nested/page.html'

    @pytest.mark.parametrize("page", ["index.html", "404.html", "nested/page.html"])
    def test_missing_path_fails_in_every_html_page(tmp_path: Path, page: str) -> None:
        site = tmp_path / "site"
        html = site / page
        html.parent.mkdir(parents=True)
        html.write_text(f'<img src="{PREFIX}blob/main/missing.png">')
        result = run_check(tmp_path, site)
>       assert result.returncode == 1
E       assert 2 == 1
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:49: AssertionError
_______________ test_path_cannot_escape_checkout[../outside.txt] _______________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_path_cannot_escape_checko0')
target = '../outside.txt'

    @pytest.mark.parametrize("target", ["../outside.txt", "%2Foutside.txt"])
    def test_path_cannot_escape_checkout(tmp_path: Path, target: str) -> None:
        root = tmp_path / "repo"
        site = root / "site"
        site.mkdir(parents=True)
        (tmp_path / "outside.txt").touch()
        (site / "index.html").write_text(f'<a href="{PREFIX}blob/main/{target}">bad</a>')
        result = run_check(root, site)
>       assert result.returncode == 1
E       assert 2 == 1
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:62: AssertionError
_______________ test_path_cannot_escape_checkout[%2Foutside.txt] _______________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_path_cannot_escape_checko1')
target = '%2Foutside.txt'

    @pytest.mark.parametrize("target", ["../outside.txt", "%2Foutside.txt"])
    def test_path_cannot_escape_checkout(tmp_path: Path, target: str) -> None:
        root = tmp_path / "repo"
        site = root / "site"
        site.mkdir(parents=True)
        (tmp_path / "outside.txt").touch()
        (site / "index.html").write_text(f'<a href="{PREFIX}blob/main/{target}">bad</a>')
        result = run_check(root, site)
>       assert result.returncode == 1
E       assert 2 == 1
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode

tests/docs/test_repo_links.py:62: AssertionError
______________ test_other_repositories_and_branches_are_not_local ______________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-0/test_other_repositories_and_br0')

    def test_other_repositories_and_branches_are_not_local(tmp_path: Path) -> None:
        site = tmp_path / "site"
        site.mkdir()
        (site / "index.html").write_text(
            '<a href="https://github.com/other/repo/blob/main/missing">external</a>'
            f'<a href="{PREFIX}blob/release/0.9.3/missing">branch</a>'
            f'<a href="{PREFIX}issues/999">issue</a>'
        )
>       assert run_check(tmp_path, site).returncode == 0
E       assert 2 == 0
E        +  where 2 = CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n").returncode
E        +    where CompletedProcess(args=['/workspace/prompt-runs/checkouts/W-links/.venv/bin/python', '/workspace/prompt-runs/checkouts/...en file '/workspace/prompt-runs/checkouts/W-links/scripts/check_repo_links.py': [Errno 2] No such file or directory\n") = run_check(PosixPath('/tmp/pytest-of-agent/pytest-0/test_other_repositories_and_br0'), PosixPath('/tmp/pytest-of-agent/pytest-0/test_other_repositories_and_br0/site'))

tests/docs/test_repo_links.py:73: AssertionError
__________ test_workflow_checks_repo_links_before_lychee_with_retries __________

    def test_workflow_checks_repo_links_before_lychee_with_retries() -> None:
        workflow = (ROOT / ".github/workflows/docs-links.yml").read_text()
>       assert "python scripts/check_repo_links.py --site site" in workflow
E       assert 'python scripts/check_repo_links.py --site site' in "name: External documentation links\non:\n  schedule:\n    - cron: '21 3 * * *'\n  workflow_dispatch:\npermissions:\n ...           --exclude '^https://docs\\.shapedata\\.ai/'\n            site/**/*.html site/*.html\n          fail: true\n"

tests/docs/test_repo_links.py:82: AssertionError
=========================== short test summary info ============================
FAILED tests/docs/test_repo_links.py::test_existing_repository_paths[blob-a file.txt]
FAILED tests/docs/test_repo_links.py::test_existing_repository_paths[tree-folder]
FAILED tests/docs/test_repo_links.py::test_missing_path_fails_in_every_html_page[index.html]
FAILED tests/docs/test_repo_links.py::test_missing_path_fails_in_every_html_page[404.html]
FAILED tests/docs/test_repo_links.py::test_missing_path_fails_in_every_html_page[nested/page.html]
FAILED tests/docs/test_repo_links.py::test_path_cannot_escape_checkout[../outside.txt]
FAILED tests/docs/test_repo_links.py::test_path_cannot_escape_checkout[%2Foutside.txt]
FAILED tests/docs/test_repo_links.py::test_other_repositories_and_branches_are_not_local
FAILED tests/docs/test_repo_links.py::test_workflow_checks_repo_links_before_lychee_with_retries
9 failed, 1 passed in 0.16s

```

## Command results

All paths below are relative to this independent checkout unless stated otherwise. Read-only commands to inspect prompts, guidance, the plan, source/tests, Git state, logs and setup were also run; successful reads exited 0. Probe exceptions were Python 3.11/Rust initially absent from PATH (127), absent built site before build completion (checker 2), absent docs install log before bootstrap finished (tail 1), and reading the binary maturin launcher as text (1); none changed repository files.

| Command | Exit | Result |
|---|---:|---|
| `cd /workspace/shape && git remote get-url origin` | 0 | Verified `https://github.com/sqllocks/shape.git` before other repository actions. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-links` | 0 | Independent checkout. |
| `git remote set-url origin https://github.com/sqllocks/shape.git`; `git config user.name`; `git config user.email` | 0 each | Original origin and commit identity retained. |
| `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-links origin/release/0.9.3` | 0 each | Required lane/base. |
| `python -m venv .venv`; `.venv/bin/python -m pip install pytest ruff mypy` | 0 each | Python 3.12 local venv. |
| `make bootstrap` (initial unrestricted dependencies) | 2 | Terminated pip download of the 450 MB pyspark source archive; reused existing third-party dependencies instead. |
| `pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` (initial attempt) | 143 | Terminated when switching to isolated reused dependencies. |
| `CARGO_HOME=/workspace/.onboarding/cargo RUSTUP_HOME=/workspace/.onboarding/rustup PATH=/workspace/.onboarding/cargo/bin:$PATH PIP_NO_DEPS=1 make bootstrap` | 0 | Native core and domains built/installed from this clone. |
| `pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | Docs and all requested plugins installed from this clone using reused dependencies. |
| Python helper to symlink third-party dependencies, excluding Shape and editable imports | 0 | No original editable source is used. |
| `.venv/bin/python -m pytest -q tests/docs/test_repo_links.py` (red) | 1 | 9 failed, 1 passed; output above was saved before implementation. |
| `.venv/bin/python -m pytest -q tests/docs/test_repo_links.py` (green) | 0 | 10 passed. |
| `.venv/bin/ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | Full requested lint scope. |
| `.venv/bin/ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2152 files formatted. |
| `.venv/bin/mypy` (before reused dependencies) | 1 | 1159 missing dependency errors; setup incomplete. |
| `.venv/bin/python -m mypy`; explicit `--python-executable` retry | 1 each | 114 cached dependency errors in 37 existing core files. |
| `.venv/bin/python -m mypy --follow-imports=skip --strict scripts/check_repo_links.py tests/docs/test_repo_links.py` | 1 | Skipping imported pytest types makes decorators untyped; normal strict check below succeeds. |
| `.venv/bin/python -m mypy --strict scripts/check_repo_links.py` | 0 | New script is strictly typed. |
| `.venv/bin/python -m mypy --strict scripts/check_repo_links.py tests/docs/test_repo_links.py` | 0 | Both new files strictly typed. |
| `.venv/bin/python -m mypy -v --no-incremental --strict scripts/check_repo_links.py` | 0 | Confirmed local interpreter. |
| `.venv/bin/python -m mypy --no-incremental` | 0 | All 701 source files pass; fresh analysis resolves the stale dependency cache. |
| `.venv/bin/python -m pytest -q tests/docs/test_repo_links.py tests/docs_site/test_docs_site.py` | 0 | 30 passed, 41.21 seconds. |
| `make check` (run once) | 2 | Ruff lint/format passed; stopped at the cached mypy dependency errors described above. Fresh full mypy subsequently passed. Full test/coverage/Rust stages were not reached. |
| `PYTHONPATH=src:plugins/shape-domains/src:plugins/shape-sqlserver/src:plugins/shape-eventhubs/src:plugins/shape-fabric/src:plugins/shape-kafka/src SHAPE_KERNEL=python .venv/bin/python -m mkdocs build --strict` | 0 | Real site built in 131.27 seconds. |
| `.venv/bin/python scripts/check_repo_links.py --site site` | 0 | Zero broken own-repository URLs in the built site. |
| Python helper counting generated pages/matching URLs | 0 | 175 HTML pages, 383 locally verified repository links. |
| `.venv/bin/python scripts/check_secrets.py`; `.venv/bin/python scripts/check_user_facing.py` | 0 each | Clean. |
| `.venv/bin/python -m pytest -q tests/docs/test_repo_links.py tests/docs_site/test_docs_site.py` (final dependency-state verification) | 0 | 30 passed, 17.98 seconds. |
| Repeated full Ruff lint/format and `git diff --check` | 0 each | Final scope clean. |

Verified `shape.__file__` resolves to this clone at `/workspace/prompt-runs/checkouts/W-links/src/shape/__init__.py` (exit 0).

## Findings and open questions

No open questions. No status-code acceptance was broadened. No skipped/xfail tests, changed tolerances, regression data or golden corpus. No release/plan/main branch edits, PRs, merges, tags or force pushes.

Full `make check` did not reach the suite/coverage/native gates because it ran while mypy retained predependency resolution errors. Independent fresh full mypy and the scoped tests pass. The lead can rely on CI for the full suite.
