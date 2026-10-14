# W-pdb-1: reference-only profile-db client secret

Scope: 0.9.4, decision 13 and section 4.11 profile-db (a). The approved plan
was read from origin/plan/promotion-093 without merging or committing it.
The lead resolved setup: use CONTRIBUTING.md, not uv.

## Baseline

Before any edits, `make bootstrap` exited 0 and built the native Rust kernel.
`make check` exited 2: 18 failed, 18370 passed, 25 skipped, 69 deselected,
16 errors; coverage 47.43% (required 86%). These failures predate this change.
The baseline log is /tmp/W-pdb-1-baseline.log in this workspace.

## Red evidence (before implementation)

Command: `. .venv/bin/activate && PYTHONPATH=plugins/shape-sqlserver/src pytest -q plugins/shape-sqlserver/tests/test_client_secret_references.py`

Exit: 1. All seven new cases failed against the unchanged command implementation.
The complete captured output follows (all credentials are synthetic test strings):

```text
FFFFFFF                                                                  [100%]
=================================== FAILURES ===================================
__________________ test_client_secret_reference_resolves[env] __________________

scheme = 'env'
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf3a2687a0>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r0')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf3a2691f0>

    @pytest.mark.parametrize("scheme", ["env", "file", "kv"])
    def test_client_secret_reference_resolves(scheme, monkeypatch, tmp_path, capsys):
        seen = {}

        def connect(text, creds):
            seen["secret"] = creds.client_secret
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", "fallback-must-not-win")
        secret = "resolved-private-credential"
        monkeypatch.setenv("PROFILE_DB_TEST_SECRET", secret)
        path = tmp_path / "credential"
        path.write_text(secret + "\n", encoding="utf-8")
        path.chmod(0o600)
        lookups = []

        def resolve_keyvault(name):
            lookups.append(name)
            return secret

        refs = {
            "env": "env://PROFILE_DB_TEST_SECRET",
            "file": f"file://{path}",
            "kv": "kv://test-vault/test-secret",
        }
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        args = parser.parse_args(
            [
                "--server",
                "srv",
                "--database",
                "shop",
                "--auth",
                "spn",
                "--tenant-id",
                "tenant",
                "--client-id",
                "client",
                "--client-secret",
                refs[scheme],
                "-o",
                str(tmp_path / "db.shape"),
            ]
        )
        credrefs.register_resolver("kv", resolve_keyvault)
        try:
            assert cmd.run(args) == 0
        finally:
            credrefs.unregister_resolver("kv")
>       assert seen["secret"] == secret
E       AssertionError: assert 'env://PROFILE_DB_TEST_SECRET' == 'resolved-private-credential'
E
E         - resolved-private-credential
E         + env://PROFILE_DB_TEST_SECRET

plugins/shape-sqlserver/tests/test_client_secret_references.py:66: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r0/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
_________________ test_client_secret_reference_resolves[file] __________________

scheme = 'file'
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf3a11fe90>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r1')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf3a11df40>

    @pytest.mark.parametrize("scheme", ["env", "file", "kv"])
    def test_client_secret_reference_resolves(scheme, monkeypatch, tmp_path, capsys):
        seen = {}

        def connect(text, creds):
            seen["secret"] = creds.client_secret
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", "fallback-must-not-win")
        secret = "resolved-private-credential"
        monkeypatch.setenv("PROFILE_DB_TEST_SECRET", secret)
        path = tmp_path / "credential"
        path.write_text(secret + "\n", encoding="utf-8")
        path.chmod(0o600)
        lookups = []

        def resolve_keyvault(name):
            lookups.append(name)
            return secret

        refs = {
            "env": "env://PROFILE_DB_TEST_SECRET",
            "file": f"file://{path}",
            "kv": "kv://test-vault/test-secret",
        }
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        args = parser.parse_args(
            [
                "--server",
                "srv",
                "--database",
                "shop",
                "--auth",
                "spn",
                "--tenant-id",
                "tenant",
                "--client-id",
                "client",
                "--client-secret",
                refs[scheme],
                "-o",
                str(tmp_path / "db.shape"),
            ]
        )
        credrefs.register_resolver("kv", resolve_keyvault)
        try:
            assert cmd.run(args) == 0
        finally:
            credrefs.unregister_resolver("kv")
>       assert seen["secret"] == secret
E       AssertionError: assert 'file:///tmp/...r1/credential' == 'resolved-private-credential'
E
E         - resolved-private-credential
E         + file:///tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r1/credential

plugins/shape-sqlserver/tests/test_client_secret_references.py:66: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r1/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
__________________ test_client_secret_reference_resolves[kv] ___________________

scheme = 'kv'
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf3a102930>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r2')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf3a101400>

    @pytest.mark.parametrize("scheme", ["env", "file", "kv"])
    def test_client_secret_reference_resolves(scheme, monkeypatch, tmp_path, capsys):
        seen = {}

        def connect(text, creds):
            seen["secret"] = creds.client_secret
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        monkeypatch.setenv("SHAPE_SQLSERVER_CLIENT_SECRET", "fallback-must-not-win")
        secret = "resolved-private-credential"
        monkeypatch.setenv("PROFILE_DB_TEST_SECRET", secret)
        path = tmp_path / "credential"
        path.write_text(secret + "\n", encoding="utf-8")
        path.chmod(0o600)
        lookups = []

        def resolve_keyvault(name):
            lookups.append(name)
            return secret

        refs = {
            "env": "env://PROFILE_DB_TEST_SECRET",
            "file": f"file://{path}",
            "kv": "kv://test-vault/test-secret",
        }
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        args = parser.parse_args(
            [
                "--server",
                "srv",
                "--database",
                "shop",
                "--auth",
                "spn",
                "--tenant-id",
                "tenant",
                "--client-id",
                "client",
                "--client-secret",
                refs[scheme],
                "-o",
                str(tmp_path / "db.shape"),
            ]
        )
        credrefs.register_resolver("kv", resolve_keyvault)
        try:
            assert cmd.run(args) == 0
        finally:
            credrefs.unregister_resolver("kv")
>       assert seen["secret"] == secret
E       AssertionError: assert 'kv://test-vault/test-secret' == 'resolved-private-credential'
E
E         - resolved-private-credential
E         + kv://test-vault/test-secret

plugins/shape-sqlserver/tests/test_client_secret_references.py:66: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_reference_r2/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
_ test_client_secret_literal_is_refused_without_disclosure[literal-private-credential] _

value = 'literal-private-credential'
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf4f541220>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_0')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf4f543a40>

    @pytest.mark.parametrize("value", ["literal-private-credential", "", "other://private-value"])
    def test_client_secret_literal_is_refused_without_disclosure(value, monkeypatch, tmp_path, capsys):
        connections = []

        def connect(text, creds):
            connections.append(text)
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        out = tmp_path / "db.shape"
        args = parser.parse_args(["--server", "srv", "--client-secret", value, "-o", str(out)])
>       assert cmd.run(args) == 2
E       AssertionError: assert 0 == 2
E        +  where 0 = run(Namespace(connection_string=None, server='srv', database=None, auth='cli', tenant_id=None, client_id=None, client_secr...les=None, sample_rows=1000, output='/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_0/db.shape', json=None))
E        +    where run = <shape_sqlserver.command.ProfileDbCommand object at 0x7fbf4f540da0>.run

plugins/shape-sqlserver/tests/test_client_secret_references.py:87: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_0/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
__________ test_client_secret_literal_is_refused_without_disclosure[] __________

value = ''
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf32b61370>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_1')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf32b62a50>

    @pytest.mark.parametrize("value", ["literal-private-credential", "", "other://private-value"])
    def test_client_secret_literal_is_refused_without_disclosure(value, monkeypatch, tmp_path, capsys):
        connections = []

        def connect(text, creds):
            connections.append(text)
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        out = tmp_path / "db.shape"
        args = parser.parse_args(["--server", "srv", "--client-secret", value, "-o", str(out)])
>       assert cmd.run(args) == 2
E       AssertionError: assert 0 == 2
E        +  where 0 = run(Namespace(connection_string=None, server='srv', database=None, auth='cli', tenant_id=None, client_id=None, client_secr...les=None, sample_rows=1000, output='/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_1/db.shape', json=None))
E        +    where run = <shape_sqlserver.command.ProfileDbCommand object at 0x7fbf32b61760>.run

plugins/shape-sqlserver/tests/test_client_secret_references.py:87: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_1/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
_ test_client_secret_literal_is_refused_without_disclosure[other://private-value] _

value = 'other://private-value'
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fbf30436b70>
tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_2')
capsys = <_pytest.capture.CaptureFixture object at 0x7fbf30435910>

    @pytest.mark.parametrize("value", ["literal-private-credential", "", "other://private-value"])
    def test_client_secret_literal_is_refused_without_disclosure(value, monkeypatch, tmp_path, capsys):
        connections = []

        def connect(text, creds):
            connections.append(text)
            return scenario("retail")

        monkeypatch.setattr("shape_sqlserver.profiler.connect", connect)
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser()
        cmd.configure(parser)
        out = tmp_path / "db.shape"
        args = parser.parse_args(["--server", "srv", "--client-secret", value, "-o", str(out)])
>       assert cmd.run(args) == 2
E       AssertionError: assert 0 == 2
E        +  where 0 = run(Namespace(connection_string=None, server='srv', database=None, auth='cli', tenant_id=None, client_id=None, client_secr...les=None, sample_rows=1000, output='/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_2/db.shape', json=None))
E        +    where run = <shape_sqlserver.command.ProfileDbCommand object at 0x7fbf30437c20>.run

plugins/shape-sqlserver/tests/test_client_secret_references.py:87: AssertionError
----------------------------- Captured stdout call -----------------------------
{"written": "/tmp/pytest-of-agent/pytest-4/test_client_secret_literal_is_2/db.shape", "shape_content_id": "96f502c0d49ce7eac2ae1a7eaada083fa532faf685a89ca0007ccb2ad64214fc", "tables": 6, "relationships": 3}
____________ test_client_secret_help_describes_reference_only_input ____________

capsys = <_pytest.capture.CaptureFixture object at 0x7fbf32a8a750>

    def test_client_secret_help_describes_reference_only_input(capsys):
        cmd = ProfileDbCommand()
        parser = argparse.ArgumentParser(prog="shape profile-db")
        cmd.configure(parser)
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--help"])
        assert exc.value.code == 0
        help_text = " ".join(capsys.readouterr().out.split())
>       assert "--client-secret REF" in help_text
E       AssertionError: assert '--client-secret REF' in 'usage: shape profile-db [-h] [--connection-string ODBC] [--server SERVER] [--database DATABASE] [--auth {sql,cli,msi,... (default: 1000) -o OUT.shape, --output OUT.shape write the profile here --json SUMMARY.json also write a JSON summary'

plugins/shape-sqlserver/tests/test_client_secret_references.py:107: AssertionError
=========================== short test summary info ============================
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_reference_resolves[env]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_reference_resolves[file]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_reference_resolves[kv]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_literal_is_refused_without_disclosure[literal-private-credential]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_literal_is_refused_without_disclosure[]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_literal_is_refused_without_disclosure[other://private-value]
FAILED plugins/shape-sqlserver/tests/test_client_secret_references.py::test_client_secret_help_describes_reference_only_input
7 failed in 4.15s

```

## Implementation and focused verification

- Only `profile-db --client-secret` changed: its explicit value must start with
  env://, file:// or kv:// and is resolved by shape.security.credrefs.resolve_reference,
  the resolver used by publish. The existing environment fallback is preserved.
- A literal (including empty input or an unsupported scheme) returns 2 before
  connecting or writing, with exactly one constant shape: error: line.
- Help uses REF and documents references, literal refusal and the environment fallback.
- Updated docs/plugins/sqlserver.md, the plugin README example, and CHANGELOG.md
  under Unreleased. No release/version, dependency, regression-store or golden-corpus edits.
- Seven acceptance cases were written and run before implementation; all seven failed.
  After implementation the full local SQL Server plugin suite passed: 173 passed,
  19 emulator/live cases deselected, four existing artifact-verification warnings (exit 0).
- Real shape CLI subprocess checks passed (exit 0): literal invocation returned 2,
  empty stdout, exactly one sanitized error line, no literal disclosure; help returned 0
  and described the three reference schemes.
- Ruff lint and formatting checks passed (exit 0). An initial git diff --check found
  trailing whitespace in the copied red transcript (exit 2). Only trailing whitespace
  in the report was normalized; the subsequent diff check passed (exit 0).
- mkdocs build --strict passed (exit 0). An initial link check ran before the build
  completed and exited 2 (no built site); it was rerun after the successful build.

## Baseline failure detail

The baseline failed before implementation, after 4109.02 seconds. It reached the
core tests and failed their coverage gate (47.43%, required 86%). Later make check
stages therefore did not run. No gate was lowered and no test was modified or disabled.

Baseline failed tests:

```text
ERROR tests/packaging/test_pure_wheel_metadata.py::test_requires_dist_equals_the_sdist
ERROR tests/packaging/test_pure_wheel_metadata.py::test_every_extra_is_provided
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[Name]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[Version]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[Summary]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[Requires-Python]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[License-Expression]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_single_valued_fields_equal_the_sdist[Description-Content-Type]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_multi_valued_fields_equal_the_sdist[License-File]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_multi_valued_fields_equal_the_sdist[Project-URL]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_multi_valued_fields_equal_the_sdist[Classifier]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_multi_valued_fields_equal_the_sdist[Provides-Extra]
ERROR tests/packaging/test_pure_wheel_metadata.py::test_description_equals_the_sdist
ERROR tests/release/test_packaging.py::test_core_sdist_holds_only_build_inputs_and_user_files
ERROR tests/release/test_packaging.py::test_core_sdist_has_what_a_build_needs
ERROR tests/release/test_packaging.py::test_core_sdist_passes_the_user_facing_check
FAILED tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left
FAILED tests/docs/test_brand.py::test_branded_site - ModuleNotFoundError: No ...
FAILED tests/docs/test_launch_tutorials.py::test_tutorial[06-domain-duckdb]
FAILED tests/docs/test_reference_examples.py::test_reference_page[CLI] - Fail...
FAILED tests/docs/test_reference_examples.py::test_reference_page[CONTAINER]
FAILED tests/docs/test_reference_examples.py::test_reference_page[DBT] - Asse...
FAILED tests/docs/test_reference_examples.py::test_reference_page[IMPORTERS]
FAILED tests/docs/test_reference_examples.py::test_reference_page[INSTALL] - ...
FAILED tests/docs/test_reference_examples.py::test_reference_page[PROJECT] - ...
FAILED tests/docs/test_reference_examples.py::test_reference_page[SIGNING] - ...
FAILED tests/docs/test_reference_examples.py::test_reference_page[SIMULATION_FILES_EVENTS]
FAILED tests/docs/test_reference_examples.py::test_reference_page[SINKS] - As...
FAILED tests/docs/test_reference_examples.py::test_reference_page[TESTING_WITH_SHAPE]
FAILED tests/docs/test_reference_examples.py::test_reference_page[authoring]
FAILED tests/docs/test_reference_examples.py::test_reference_page[behavior]
FAILED tests/docs/test_reference_examples.py::test_reference_page[healthcare-codes]
FAILED tests/docs/test_reference_examples.py::test_reference_page[simulation]
FAILED tests/scale/test_router.py::test_cancel_stops_the_run_between_chunks
```

The packaging errors came from maturin sdist failures; the captured failure text
includes Cargo metadata failed / cargo not in PATH. Other diagnostics include
missing playwright, puccinialin and shape_healthcare_codes, documentation transcript
mismatches, a file-permission assertion and a cancellation assertion. Bootstrap itself
successfully compiled the native kernel. These unrelated baseline failures are reported,
not changed by this lane.

## Command record (this session)

Every shell command ran from /workspace/shape. Repeated log-tail and process-status
inspections below all exited 0. Shell activation applies to each Python/check command.
No fetch needed a network retry. Delivery commands and their results are reported in the final chat message.

| Command or inspection batch | Exit / result |
|---|---|
| cd /workspace/shape; git remote get-url origin | 0; https://github.com/sqllocks/shape.git |
| cat /etc/codex/network-policy.json | 0 |
| git status --short; git fetch origin (retry wrapper with 0,2,4,8,16-second delays) | 0; succeeded first attempt |
| git checkout -B lane/W-pdb-1 origin/lane/W-pdb-1 | 128; default refspec fetched main only |
| git config --get-all remote.origin.fetch; git branch -a; git ls-remote --heads origin lane/W-pdb-1 | 0 each; lane exists remotely |
| git fetch origin refs/heads/lane/W-pdb-1:refs/remotes/origin/lane/W-pdb-1 (retry wrapper) | 0 |
| git checkout -B lane/W-pdb-1 origin/lane/W-pdb-1 | 0 |
| git -c core.editor=true merge -m 'W-pdb-1: merge main into builder lane' origin/main | 0; already up to date, no merge commit needed |
| cat CLAUDE.md; conditional cat AGENTS.md; cat CONTRIBUTING.md | 0; no AGENTS.md present |
| git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093 (retry wrapper) | 0 |
| git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md > /tmp/PROMOTION_PLAN.md | 0 |
| sed -n '1,45p' /tmp/PROMOTION_PLAN.md; rg section/decision/profile-db locations in the plan | 0 each |
| python -m venv .venv; . .venv/bin/activate; make bootstrap (log /tmp/W-pdb-1-bootstrap.log) | 0 each |
| command -v cargo; command -v rustc | 1 each; bootstrap build isolation supplied its own Rust toolchain |
| make check (log /tmp/W-pdb-1-baseline.log), before edits | 2; baseline above |
| git config user.name; git config user.email | 0 each |
| rg --files for profile files and AGENTS.md; client-secret/resolver searches in src and SQL Server/Fabric plugins | 0 for matches; AGENTS-only search 1 (no file) |
| cat docs/plans/lane_status/W-pdb-1.md; sed Makefile; head CHANGELOG.md | 0 each |
| sed SQL Server command.py, auth.py, test_source.py, pyproject.toml, README.md and docs/plugins/sqlserver.md | 0 each |
| cat src/shape/security/credrefs.py; sed src/shape/cli/auth.py and Fabric auth.py | 0 each |
| rg against Fabric publish glob | 2; glob did not match a file; subsequent commands.py/auth.py searches succeeded (0) |
| sed plugins/shape-sqlserver/tests/test_cli.py; cat plugins/shape-sqlserver/tests/conftest.py | 2 and 1 respectively; absent files, test_source.py located instead |
| rg pythonpath/testpaths with pytest.ini; rg sys.path/plugins with root conftest.py | 2 each; absent optional filenames; actual pyproject.toml/tests/conftest.py read successfully (0) |
| rg docs/examples/scripts for --client-secret and profile-db; rg docs example workflow path | 0 for matches; 2 where .github/workflows/plugins.yml was absent |
| tail bootstrap/baseline/final logs; ps process inspections; git status --short | 0 each, including repeated progress polls |
| cat heredoc creating test_client_secret_references.py | 0 |
| ruff format plugins/shape-sqlserver/tests/test_client_secret_references.py | 0; formatted before red run |
| PYTHONPATH=plugins/shape-sqlserver/src pytest -q plugins/shape-sqlserver/tests/test_client_secret_references.py | 1; 7 failed, complete red transcript above |
| Python write of lane status with full red evidence before implementation | 0 |
| apply_patch: command, SQL Server guide, README example, Unreleased changelog | successful |
| PYTHONPATH=plugins/shape-sqlserver/src pytest -q plugins/shape-sqlserver/tests -m 'not emulator and not live' | 0; 173 passed, 19 deselected |
| ruff check command.py and test_client_secret_references.py (full repository paths) | 0 |
| ruff format --check on those same two files | 0 |
| git diff --check (first report copy) | 2; copied red transcript trailing whitespace |
| Python normalization of report trailing whitespace; git diff --check | 0 each |
| make check (log /tmp/W-pdb-1-final-check.log) | 2; 17 failed, 18371 passed, 25 skipped, 69 deselected, 16 errors; coverage 47.43% |
| mkdocs build --strict (log /tmp/W-pdb-1-docs-build.log) | 0 |
| python scripts/check_doc_links.py --site site (before build completed) | 2; no built site yet |
| python scripts/check_user_facing.py --site site (initial run) | 0 |
| git diff --stat; git diff -- command/docs/README/changelog; rg baseline diagnostics; sed baseline traceback | 0 each |
| PYTHONPATH=plugins/shape-sqlserver/src python heredoc asserting real CLI literal rejection and --help | 0 |
| python scripts/check_doc_links.py --site site (after build) | 0; zero broken links |
| python scripts/check_user_facing.py --site site (after build) | 0; clean |
| Python append implementation, baseline diagnostics and command record | 0 |

## Final verification

`make check` exited 2 after 4205.61 seconds: 17 failed, 18371 passed, 25 skipped,
69 deselected, 16 errors. Coverage remained 47.43% against the unchanged 86% gate.
Comparing failing/error test IDs with the pre-edit baseline found **no new failures
or errors**. The only baseline-only failure was
`tests/scale/test_router.py::test_cancel_stops_the_run_between_chunks`, which passed
in the final run. All final failing/error IDs are already listed in the baseline
section above. Later heavy, pure-Python kernel and Rust stages were not reached
because the core test stage failed; no passing result is claimed for them.

Both make check runs passed each static/repository stage below (exit 0 each):

```text
ruff check src tests plugins benchmarks/vs_refengine
ruff format --check src tests plugins benchmarks/vs_refengine
mypy
python -m compileall -q src/shape
vulture src/shape scripts/vulture_whitelist.py --min-confidence 80
lint-imports
python scripts/check_requirements.py
python scripts/check_secrets.py
python scripts/check_user_facing.py
python scripts/gen_exit_codes.py --check
python scripts/gen_failure_modes.py --check
python scripts/check_shipped_data.py
python scripts/check_plugin_skeletons.py
python scripts/check_versions.py
python scripts/check_release_workflows.py
python scripts/check_conformance_coverage.py
python scripts/cli_surface.py --check
python scripts/check_v1_done.py
```

The new acceptance cases passed as part of the full SQL Server plugin run:
173 passed, 19 emulator/live cases deselected, four existing warnings, exit 0.
The real CLI smoke assertions, Ruff lint/format, strict MkDocs build, post-build
link check and post-build user-facing wording check all passed, exit 0 each.
The full seven-failure red transcript above is preserved with trailing whitespace
normalized so git diff --check can pass.

Additional command record:

| Command | Exit / result |
|---|---|
| Repeated tail -n 4/5/8 final-check log inspections and write_stdin progress polls | 0 for shell reads; polls observed the running check until exit 2 |
| One final-log read through exec_command | tool transport disconnected before the command ran; immediate retry exited 0, running check unaffected |
| Python comparison of baseline/final failing test IDs and coverage summaries | 0; no new failing/error IDs |
| git diff --check; git status --short; git branch --show-current after final check | 0 each; only the six intended files changed, branch lane/W-pdb-1 |
| Python final update of this lane status | 0 |

Open questions: none. The earlier setup blocker is resolved.

Delivery: git diff --check, git add of the six intended files, a signed-off
commit starting with W-pdb-1:, then git push -u origin lane/W-pdb-1.
The final chat message records the resulting commit hash and delivery outcomes,
so this report does not attempt to record its own future commit hash.
