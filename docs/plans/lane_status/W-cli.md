# W-cli lane status

Base: origin/main at c10923be9278bc63f2713ac0ed32a548b59a7afb. Lane: lane/W-cli. Scope: 0.9.4 preparation; no promotions.

## Changes

Split the command guide by the current STABLE set. Experimental command documentation,
including vault, migration, history, audit, project, contracts and CI helpers, now lives
in CLI_EXPERIMENTAL.md. Keep both pages labelled experimental: the stable-command page
is prepared for later promotion, without changing command stability or release labels.
Preserve substantive stable capture, generation, identifier precedence and safety details
from mixed paragraphs. Clarify that core JSON support and parser-supported dry runs do
not imply universal support in plugins. Register the new page in MkDocs navigation.
Move the migration and history example inventory entries with their documentation,
retain the CLI fixture bootstrap on each page, and resolve cross-page example links.
No incoming links to removed named section anchors were found; the source/site link
checks verify the final links. Historical evidence receipts and the promotion plan were
not rewritten. No changelog or release files changed.

## Red evidence

Added split guards before implementation. The first pytest run exited 1 with two failures:
experimental command references remained on CLI.md, and CLI_EXPERIMENTAL.md did not exist.
The command matcher was subsequently narrowed to command/code prefixes to exclude prose
and artifact filenames. No test was skipped or xfailed and no tolerance was changed.

```text
FF                                                                       [100%]
=================================== FAILURES ===================================
_______________ test_stable_guide_documents_only_stable_commands _______________

    def test_stable_guide_documents_only_stable_commands() -> None:
        text = (ROOT / "docs/CLI.md").read_text()
        commands = set(re.findall(r"\bshape ([a-z][a-z-]+)\b", text))
>       assert commands <= STABLE, commands - STABLE
E       AssertionError: {'as', 'badge', 'bisect', 'bridge', 'canary', 'changes', ...}
E       assert {'as', 'badge...capture', ...} <= frozenset({'c... 'diff', ...})
E         
E         Extra items in the left set:
E         'learn'
E         'contract'
E         'badge'
E         'proposals'
E         'vault'...
E         
E         ...Full output truncated (46 lines hidden), use '-vv' to show

tests/docs/test_cli_split.py:14: AssertionError
____________________ test_experimental_guide_and_navigation ____________________

    def test_experimental_guide_and_navigation() -> None:
>       text = (ROOT / "docs/CLI_EXPERIMENTAL.md").read_text()
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/docs/test_cli_split.py:18: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1027: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/workspace/prompt-runs/checkouts/W-cli/docs/CLI_EXPERIMENTAL.md')
mode = 'r', buffering = -1, encoding = 'locale', errors = None, newline = None

    def open(self, mode='r', buffering=-1, encoding=None,
             errors=None, newline=None):
        """
        Open the file pointed to by this path and return a file object, as
        the built-in open() function does.
        """
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
>       return io.open(self, mode, buffering, encoding, errors, newline)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E       FileNotFoundError: [Errno 2] No such file or directory: '/workspace/prompt-runs/checkouts/W-cli/docs/CLI_EXPERIMENTAL.md'

/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1013: FileNotFoundError
=========================== short test summary info ============================
FAILED tests/docs/test_cli_split.py::test_stable_guide_documents_only_stable_commands
FAILED tests/docs/test_cli_split.py::test_experimental_guide_and_navigation
2 failed in 0.18s
```

## Setup and command ledger

All subsequent commands ran in /workspace/prompt-runs/checkouts/W-cli. Independent clone
and editable package paths keep this lane separate from the original checkout.

| Command / operation | Exit | Result |
|---|---:|---|
| cd /workspace/shape; git remote get-url origin; git config user.name; git config user.email | 0 | Verified https://github.com/sqllocks/shape.git and copied configured identity. |
| git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-cli; set origin/config; git fetch origin; fetch plan/promotion-093; checkout -B lane/W-cli origin/main | 0 | Isolated lane from origin/main. |
| Read CLAUDE.md, CONTRIBUTING.md, docs/contributing/DOCS_STYLE.md, plan Decisions/3/4.10; search AGENTS.md | 0 | No AGENTS.md in checkout; plan read via git show only. |
| python3.11 -m venv .venv | 127 | Python 3.11 absent; used Python 3.12.14, supported by repository. |
| python --version; rustc --version | 127 | Default PATH lacked Rust; discovered onboarding Rust installation. |
| python -m venv .venv; make bootstrap (unbounded dependency download) | 130 | Interrupted duplicate 450 MB PySpark download. |
| make bootstrap with onboarding Rust, fresh dependencies | 130 | Interrupted repeated dependency download; reused installed third-party dependencies. |
| cp -a --reflink=auto original site-packages into isolated venv; rewrite .pth and editable finder mappings | 0 | Copied package directories, no shared numpy/pyarrow symlinks. |
| PIP_NO_DEPS=1 make bootstrap with onboarding Rust, own target | 130 | Stopped redundant Arrow compilation after several minutes; reused a completed sibling build cache below. |
| PIP_NO_DEPS=1 make bootstrap using onboarding Rust and CARGO_TARGET_DIR=/workspace/prompt-runs/checkouts/W-parity-time/rust/shape-kernel/target | 0 | Reused compiled dependencies; editable sources remain this clone. |
| pip install --no-deps -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka | 0 | Requested core/docs/plugin source installation completed. |
| Copy shape console launchers, replacing shebang with isolated venv Python | 0 | Enabled pure-Python docs execution while native setup runs. |
| pytest -q tests/docs/test_cli_split.py (red) | 1 | 2 expected failures, above. |
| pytest -q tests/docs/test_cli_split.py (initial implementation) | 1 | Command matcher caught prose; removed remaining experimental doctor link and narrowed matcher. |
| ruff check tests/docs/test_cli_split.py --fix; ruff format tests/docs/test_cli_split.py | 0 | Import order fixed; formatting clean. |
| pytest -q tests/docs/test_cli_split.py tests/docs_site/test_docs_site.py | 0 | 22 passed. |
| pytest -q split/inventory/docs_site/CLI CI docs neighbors | 0 | 29 passed. |
| python scripts/check_doc_links.py | 0 | 0 broken source links. |
| python scripts/check_user_facing.py | 0 | Clean. |
| python -m ruff check/format (before copying executable) | 1 | Missing ruff binary in copied venv; copied binary and reran. |
| ruff check src tests plugins benchmarks/vs_refengine scripts | 0 | All checks passed. |
| ruff format --check src tests plugins benchmarks/vs_refengine scripts | 0 | 2140 files already formatted. |
| python -m mypy | 0 | Success: no issues found in 698 source files. |
| timeout 180 make check | 124 | Lint, format, mypy and compileall passed; timeout during Vulture. Full suite not reached in this resource-shared environment. |
| pytest -q split/reference examples -k 'CLI or restored_example_inventory' (before launcher) | 1 | shape console script not yet installed; fixed environment only. |
| pytest -q split/reference examples -k 'CLI or restored_example_inventory' (before docs dependency pins) | 1 | Migration and inventory passed; doctor transcript exposed numpy/pyarrow version mismatch and absent tzdata. |
| pip install numpy==2.5.3 pyarrow==25.0.1 tzdata==2026.3 | 0 | Exact existing requirements-docs.txt pins; no transcript changes. |
| pytest -q split/reference examples -k 'CLI or restored_example_inventory' (final) | 0 | 5 passed, 66 deselected. |
| pytest -q tests/docs_site tests/cli/test_ci_docs.py | 0 | 26 passed. |
| NO_MKDOCS_2_WARNING=1 python -m mkdocs build --strict | 0 | Strict initial build passed. |
| python scripts/check_doc_links.py --site site (initial) | 0 | 0 broken links in sources and site. |
| python scripts/check_user_facing.py --site site (initial) | 0 | Clean. |
| Final NO_MKDOCS_2_WARNING=1 python -m mkdocs build --strict | 0 | Final source including streaming sign-in rendered successfully. |
| Final python scripts/check_doc_links.py --site site | 0 | 0 broken links in sources and site. |
| Final python scripts/check_user_facing.py --site site | 0 | Clean. |
| pip install --no-deps -e local first-party plugins | 0 | All five plugin imports verified from this clone. |
| Verify shape and five plugin __file__ locations | 0 | All isolated source paths. |
| git diff --check | 0 | Clean. |
| Search incoming links to moved named anchors | 1 | No matches; no stale incoming links. |
| Compare original experimental command mentions with new page | 0 | All command documentation retained after restoring the emit authentication reference. |

## Findings and open questions

No open plan or product questions. The full make check suite was not completed locally;
the lead must use CI for the full suite. Native bootstrap and the requested docs/plugin
installation completed using a shared build cache. No native source code changed.
