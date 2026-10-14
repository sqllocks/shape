# W-canary-1 lane status

Base: origin/main. No promotion or baseline write.

## Red evidence

Tests were added before schema/sample implementation.

Command: `SHAPE_KERNEL=python PYTHONPATH=src:plugins/shape-domains/src .venv/bin/python -m pytest -q tests/scenario/test_canary.py -k "key_set_is_pinned or frozen_version_one or live_make_json_canary or canary_schema_rejects"`; exit 1.

```text
_ _ _ _ _ _ _ _ _ _ _ 
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1027: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

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
E       FileNotFoundError: [Errno 2] No such file or directory: '/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json'

/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1013: FileNotFoundError
_ ERROR at setup of test_canary_schema_rejects_malformed_documents[<lambda>7] __

    @pytest.fixture(scope="module")
    def canary_schema():
        from jsonschema import Draft202012Validator
    
        path = Path(shape.__file__).parent / "schemas" / "shape-canary-v1.schema.json"
>       schema = json.loads(path.read_text(encoding="utf-8"))
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/scenario/test_canary.py:466: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1027: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

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
E       FileNotFoundError: [Errno 2] No such file or directory: '/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json'

/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1013: FileNotFoundError
_ ERROR at setup of test_canary_schema_rejects_malformed_documents[<lambda>8] __

    @pytest.fixture(scope="module")
    def canary_schema():
        from jsonschema import Draft202012Validator
    
        path = Path(shape.__file__).parent / "schemas" / "shape-canary-v1.schema.json"
>       schema = json.loads(path.read_text(encoding="utf-8"))
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/scenario/test_canary.py:466: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1027: in read_text
    with self.open(mode='r', encoding=encoding, errors=errors) as f:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = PosixPath('/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json')
mode = 'r', buffering = -1, encoding = 'utf-8', errors = None, newline = None

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
E       FileNotFoundError: [Errno 2] No such file or directory: '/workspace/prompt-runs/checkouts/W-canary-1/src/shape/schemas/shape-canary-v1.schema.json'

/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1013: FileNotFoundError
=============================== warnings summary ===============================
tests/scenario/test_canary.py::test_check_json_key_set_is_pinned[False]
  /workspace/prompt-runs/checkouts/W-canary-1/tests/scenario/test_canary.py:290: ArtifactNotVerifiedWarning: /tmp/pytest-of-agent/pytest-9/pipeline0/work/base.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    profile = shape.load(str(base))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
ERROR tests/scenario/test_canary.py::test_frozen_version_one_canary_validates_and_still_loads
ERROR tests/scenario/test_canary.py::test_live_make_json_canary_payload_and_written_document_validate
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>0]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>1]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>2]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>3]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>4]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>5]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>6]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>7]
ERROR tests/scenario/test_canary.py::test_canary_schema_rejects_malformed_documents[<lambda>8]
4 passed, 54 deselected, 1 warning, 11 errors in 17.73s

```

## Implementation and findings

- Added the packaged Draft 2020-12 `shape-canary-v1.schema.json` and a frozen v1 null-flood canary under `tests/fixtures/canary/v1/`.
- Pinned the exact make/check JSON key sets, including make dry-run and both successful/failed check verdicts. The existing machine envelope replaces the top-level format/version; the make payload retains the shape-canary format. Tests validate that live payload and the written document, without changing CLI behavior.
- The schema accepts either the seven-field document or the complete four-field output plan extension, checks marker/expected structure, and rejects newer versions and malformed fields. Detection names are checked semantically by the existing reader against the installed catalog, rather than duplicating the catalog in the schema.
- No command promotion, changelog promotion, CLI baseline rewrite, plan import, recorded corpus changes, or canary runtime behavior changes.
- No plan/code ambiguity; open questions: none.

## Command ledger (completed so far)

All subsequent repository commands use the isolated checkout `/workspace/prompt-runs/checkouts/W-canary-1`.

| Command / operation | Exit | Finding |
| --- | --- | --- |
| `cd /workspace/shape && git remote get-url origin && git config user.name && git config user.email` | 0 | Verified sqllocks/shape origin and copied repository identity. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-canary-1`; origin/config setup; `git fetch origin`; plan fetch; `git checkout -B lane/W-canary-1 origin/main` | 0 | Isolated origin/main lane. |
| Read CLAUDE.md, CONTRIBUTING.md, plan Decisions and section 4.1, canary modules/tests, schema conventions, Makefile and pyproject with `cat`, `git show`, `sed`, `rg` | 0 | No AGENTS.md in checkout; plan agrees with code. Some optional search paths do not exist. |
| `python3.11 --version`; `rustc --version` on initial PATH | 127 | Python 3.11 not installed; Python 3.12.14 is available and supported. Rust is provisioned outside initial PATH. |
| `python -m venv .venv` | 0 | Isolated Python 3.12 environment. |
| Initial `make bootstrap` | 2 | Stopped dependency download to avoid redundant 450MB pyspark downloads across concurrent lanes. |
| Symlink preinstalled third-party dependencies, excluding core/plugin packages and editable path files | 0 | No original editable sources imported. |
| Configure `CARGO_HOME=/workspace/.onboarding/cargo`, `RUSTUP_HOME=/workspace/.onboarding/rustup`, Rust PATH; `rustc --version` | 0 | Rust 1.99.0. |
| `python -m ruff format tests/scenario/test_canary.py` before binary setup | 1 | Missing binary in new venv; installed local tool launchers next. |
| Create local tool launchers with isolated interpreter and symlink existing Ruff binary | 0 | Tools use lane interpreter. |
| `.venv/bin/ruff format tests/scenario/test_canary.py` | 0 | One file formatted. |
| `.venv/bin/ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed. |
| `.venv/bin/ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2139 files already formatted. |
| `.venv/bin/mypy` | 0 | No issues in 698 source files. |
| `PYTHONPATH=src:plugins/shape-domains/src .venv/bin/python -m pytest --collect-only -q tests/scenario/test_canary.py` | 0 | 69 tests collected (15 new). |
| `PYTHONPATH=src:plugins/shape-domains/src .venv/bin/python scripts/check_shipped_data.py` | 0 | 168 shipped files; no runtime download. |
| `git diff --check` | 0 | Clean whitespace. |

| `PYTHONPATH=src:plugins/shape-domains/src .venv/bin/python scripts/check_secrets.py` | 0 | Basic secret check passed. |
| `PIP_NO_DEPS=1 make bootstrap` | 0 | Native kernel built with Rust 1.99 and local editable core/domains installed. |
| `pip install --no-deps -e ".[docs]" -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | All requested local editable packages installed; preinstalled third-party dependencies reused. |
| `SHAPE_KERNEL=python PYTHONPATH=src:plugins/shape-domains/src .venv/bin/python -m pytest -q tests/scenario/test_canary.py tests/scenario/test_library.py tests/scenario/test_failure_catalog.py` | 0 | 299 passed, one unsigned-fixture warning, 459.10 seconds. |

Diagnostic reads (`pwd`, `ls`, `head`, `tail`, `ps`, `du`, `df`, `free`) inspected only setup/logs/check progress; optional absent files returned 1/2. No repository changes were made by those reads.

## Native confirmation

After setup, `.venv/bin/python -m pytest -q tests/scenario/test_canary.py -k 'key_set_is_pinned or frozen_version_one or live_make_json_canary or canary_schema_rejects'` exited 0: 15 passed, 54 deselected, one unsigned-fixture warning in 4.92 seconds. This selector runs all additions; it does not skip or xfail any test.

The installed core and all five plugin module paths were verified under this lane checkout (exit 0); no original editable source path was imported.

## Additional registration red evidence

`make check` was run once and exited 2 after the coverage stage was interrupted at 203.51 seconds: 1202 passed, two schema-registration failures, one existing Pydantic dependency skip. All preceding gates passed. These failures were addressed, not classified as sandbox artifacts. The full suite did not complete; no coverage or test gates were lowered.

A schema added under the packaged schemas directory must be named in the bridge format registry. Added a test before registration:

`.venv/bin/python -m pytest -q tests/bridge/test_formats_1_1.py -k "canary or every_shipped or new_schema"` exited 1.

```text
FFF                                                                      [100%]
=================================== FAILURES ===================================
_ test_every_shipped_schema_file_is_in_the_table_and_the_table_names_only_shipped_files _

    def test_every_shipped_schema_file_is_in_the_table_and_the_table_names_only_shipped_files():
        shipped = set(shipped_files())
>       assert shipped == named_files()
E       AssertionError: assert {'decisions-v...ma.json', ...} == {'decisions-v...ma.json', ...}
E         
E         Extra items in the left set:
E         'shape-canary-v1.schema.json'
E         Use -v to get more diff

tests/bridge/test_formats_1_1.py:15: AssertionError
______________ test_a_new_schema_file_without_a_name_is_reported _______________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fe9df640650>

    def test_a_new_schema_file_without_a_name_is_reported(monkeypatch):
        import shape.bridge.handlers.formats as formats
    
        real = formats.shipped_files()
        monkeypatch.setattr(formats, "shipped_files", lambda: [*real, "new-v1.json"])
>       assert set(formats.shipped_files()) - named_files() == {"new-v1.json"}
E       AssertionError: assert {'new-v1.json....schema.json'} == {'new-v1.json'}
E         
E         Extra items in the left set:
E         'shape-canary-v1.schema.json'
E         Use -v to get more diff

tests/bridge/test_formats_1_1.py:25: AssertionError
________ test_canary_schema_is_published_in_1_2_and_keeps_1_1_unchanged ________

api11 = <bridge_helpers.Caller11 object at 0x7fe9df6408c0>
api12 = <bridge_helpers.Caller12 object at 0x7fe9df640200>

    def test_canary_schema_is_published_in_1_2_and_keeps_1_1_unchanged(api11, api12):
        assert "canary" not in api11.ok("format_schema")["names"]
        api11.fail("format_schema", "input.unknown_format", name="canary")
>       result = api12.ok("format_schema", name="canary")
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/bridge/test_formats_1_1.py:90: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <bridge_helpers.Caller12 object at 0x7fe9df640200>
command = 'format_schema', options = None, args = {'name': 'canary'}

    def ok(self, command: str, options: dict[str, Any] | None = None, **args: Any) -> Any:
        response = self.call(command, options, **args)
>       assert response["ok"], response
               ^^^^^^^^^^^^^^
E       AssertionError: {'api_version': '1.2', 'id': None, 'command': 'format_schema', 'ok': False, 'error': {'code': 'input.unknown_format', 'group': 'input', 'message': "no published format named 'canary' (formats: backtest-report, consumer-check, consumer-contract, decisions, design-input, generation-schema, generation-spec, incidents, model, model-v1, model-v1-ga, mutation-plan, mutation-report, parity-report, planned-changes, plugin-allowlist, profile-engine, project, reference-pack, scorecard, scorecard-v1, skew-report)", 'hint': 'call format_schema without a name for the list'}, 'warnings': []}

tests/bridge/bridge_helpers.py:42: AssertionError
=========================== short test summary info ============================
FAILED tests/bridge/test_formats_1_1.py::test_every_shipped_schema_file_is_in_the_table_and_the_table_names_only_shipped_files
FAILED tests/bridge/test_formats_1_1.py::test_a_new_schema_file_without_a_name_is_reported
FAILED tests/bridge/test_formats_1_1.py::test_canary_schema_is_published_in_1_2_and_keeps_1_1_unchanged
3 failed, 32 deselected in 0.85s

```

## Final validation and delivery

The required schema registry change adds `canary` to bridge 1.2's format table with `shape.scenario.canary.load_canary` as reader. Bridge 1.1 still has its original name set. Existing frozen bridge vectors/corpora were not changed.

| Command | Exit | Result |
| --- | --- | --- |
| `make check` | 2 | Run once; every pre-pytest gate passed. Full coverage stage interrupted after 203.51 seconds (1202 passed, two registration failures, one preexisting Pydantic dependency skip). Registration failures subsequently fixed below; full coverage suite incomplete. |
| `kill -INT 22131` | 0 | Bounded the full coverage stage; pytest reported the two failures used as registration red evidence above. |
| `.venv/bin/ruff check src tests plugins benchmarks/vs_refengine scripts` (final) | 0 | All checks passed. |
| `.venv/bin/ruff format --check src tests plugins benchmarks/vs_refengine scripts` (final) | 0 | All files formatted. |
| `.venv/bin/mypy` (final) | 0 | No issues in 698 source files. |
| `.venv/bin/python -m pytest -q tests/bridge/test_formats_1_1.py tests/bridge/test_schemas.py` | 0 | 138 passed in 1.04 seconds; the two make-check failures and new registration test are green. |
| `.venv/bin/python -m pytest -q tests/bridge/test_compat_1_0.py tests/bridge/test_compat_1_1.py tests/bridge/test_vectors.py` | 0 | 258 passed, four NumPy deprecation warnings in 8.67 seconds. Frozen contracts unchanged. |

All 16 added tests passed (15 canary tests plus bridge registration). All changed-code and neighboring tests passed; full `make check` completion remains for lead CI. No open questions.

Delivery uses a signed-off commit with prefix `W-canary-1:` and only `git push -u origin lane/W-canary-1`; the lead's report records the final commit SHA and verifies it against `git ls-remote origin refs/heads/lane/W-canary-1`.
