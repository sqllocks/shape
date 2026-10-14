"""P-BAR promotion rules and the shrink-only legacy exemption list."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = "docs/FEATURE.md"
CLAIM = "AV-FEATURE-01"
NODE = "tests/test_proof.py::test_proof"
LEGACY = "tests/docs/available_legacy.txt"


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def section(
    *,
    proof: str = f"`{NODE}`",
    evidence: str = "none",
    claim: str = "Works",
    status: str = "done",
    since: str = "2.4.6",
) -> str:
    return (
        f"## {PAGE}\n\nsince: {since}\n\n"
        "| Claim | Statement | Proof | Evidence | Status |\n|---|---|---|---|---|\n"
        f"| {CLAIM} | {claim} | {proof} | {evidence} | {status} |\n"
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    write(tmp_path, "pyproject.toml", '[project]\nversion = "2.4.6"\n')
    write(
        tmp_path,
        PAGE,
        f"# Feature\n\nStatus: available.\nCovers: shape profile\n\n"
        f"<!-- claim: {CLAIM} -->\nRun `shape profile input.csv`.\n",
    )
    write(tmp_path, "docs/AVAILABLE.md", "# Available\n\n" + section())
    write(tmp_path, LEGACY, "")
    write(tmp_path, "CHANGELOG.md", f"## Unreleased\n\n### Promoted\n\n- {PAGE}: profile.\n")
    write(tmp_path, "src/shape/cli/stability.py", 'STABLE = frozenset({"profile"})\n')
    write(tmp_path, ".github/workflows/ci.yml", "name: CI\njobs:\n  test:\n    runs-on: linux\n")
    write(
        tmp_path,
        "tests/test_proof.py",
        "import pytest\n\ndef test_proof():\n    assert True\n"
        "\n@pytest.mark.parametrize('x', [1, 2])\ndef test_param(x):\n    assert x > 0\n"
        "\nclass TestProof:\n    def test_member(self):\n        assert True\n",
    )
    write(tmp_path, "pytest.ini", "[pytest]\nmarkers = emulator: cloud emulator\n")
    (tmp_path / "scripts").mkdir()
    for name in ("check_available.py", "check_docs_owners.py"):
        source = ROOT / "scripts" / name
        if source.exists():
            shutil.copy(source, tmp_path / "scripts" / name)
    return tmp_path


def run(
    tree: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tree / "scripts/check_available.py"), *args],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def fail(tree: Path, rule: int, message: str = "") -> str:
    result = run(tree)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"AVAILABLE: {PAGE}: P-BAR {rule}:" in result.stdout
    assert message in result.stdout
    return result.stdout


def test_clean(tree: Path) -> None:
    result = run(tree)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "proof",
    [
        "",
        "`tests/missing.py::test_missing`",
        "`tests/test_proof.py::test_gone`",
        "`ci:ci.yml/unknown`",
        "`ci:missing.yml/test`",
        "`ci:test`",
    ],
)
def test_missing_proof(tree: Path, proof: str) -> None:
    write(tree, "docs/AVAILABLE.md", section(proof=proof))
    fail(tree, 2)


@pytest.mark.parametrize(
    "proof",
    [
        "`ci:ci.yml/test`",
        "`tests/test_proof.py::test_param`",
        "`tests/test_proof.py::test_param[1]`",
        "`tests/test_proof.py::TestProof`",
    ],
)
def test_existing_job_parameter_family_and_class(tree: Path, proof: str) -> None:
    write(tree, "docs/AVAILABLE.md", section(proof=proof))
    assert run(tree).returncode == 0


def test_partial_collection_error_fails_closed(tree: Path) -> None:
    write(tree, "tests/test_broken.py", "raise RuntimeError('broken collection')\n")
    write(tree, "docs/AVAILABLE.md", section(proof=f"`{NODE}`, `tests/test_broken.py::test_x`"))
    fail(tree, 2)


def test_experimental_command(tree: Path) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write("\nCovers: shape mask\nUse `shape mask`.\n")
    fail(tree, 1, "mask")


def test_undeclared_stable_command(tree: Path) -> None:
    write(tree, "src/shape/cli/stability.py", 'STABLE = {"profile", "check"}\n')
    with (tree / PAGE).open("a") as stream:
        stream.write("\nUse `shape check`.\n")
    fail(tree, 1, "add it to Covers or fence it")


def test_declared_unstable_command_without_mention(tree: Path) -> None:
    text = (
        (tree / PAGE)
        .read_text()
        .replace("Covers: shape profile", "Covers: shape profile, shape mask")
    )
    write(tree, PAGE, text)
    fail(tree, 1, "mask")


def test_optional_plugin_stability(tree: Path) -> None:
    write(
        tree,
        "src/shape/cli/stability.py",
        'STABLE = set()\nSTABLE_PLUGIN_COMMANDS = {"profile": "sqllocks-shape-fixture"}\n',
    )
    assert run(tree).returncode == 0


def test_experimental_fences_are_skipped(tree: Path) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write(
            "\n<!-- experimental:start -->\nCovers: shape mask\nUse `shape mask`.\n"
            "<!-- claim: AV-FEATURE-99 -->\n<!-- experimental:end -->\n"
        )
    assert run(tree).returncode == 0


def test_owner_note(tree: Path) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write("\n<!-- owner: needs a real run -->\n")
    fail(tree, 4, "needs a real run")


@pytest.mark.parametrize("mutation", ["missing_row", "missing_anchor", "duplicate_row", "open"])
def test_claim_bijection_and_done(tree: Path, mutation: str) -> None:
    if mutation == "missing_row":
        with (tree / PAGE).open("a") as stream:
            stream.write("\n<!-- claim: AV-FEATURE-02 -->\n")
    elif mutation == "missing_anchor":
        write(tree, PAGE, (tree / PAGE).read_text().replace(f"<!-- claim: {CLAIM} -->", ""))
    elif mutation == "duplicate_row":
        write(
            tree, "docs/AVAILABLE.md", section() + f"| {CLAIM} | twice | `{NODE}` | none | done |\n"
        )
    else:
        write(tree, "docs/AVAILABLE.md", section(status="open W1-01"))
    fail(tree, 2)


@pytest.mark.parametrize("heading", ["2.4.6", "Unreleased"])
def test_rule_five_numbered_and_unreleased(tree: Path, heading: str) -> None:
    write(tree, "CHANGELOG.md", f"## {heading}\n\n### Promoted\n\n- {PAGE}: profile.\n")
    assert run(tree).returncode == 0


def test_rule_five_missing(tree: Path) -> None:
    write(tree, "CHANGELOG.md", "## Unreleased\n\n### Added\n\n- docs/FEATURE.md\n")
    fail(tree, 5)


def test_rule_five_another_release_is_exempt(tree: Path) -> None:
    write(tree, "docs/AVAILABLE.md", section(since="2.4.5"))
    write(tree, "CHANGELOG.md", "## Unreleased\n")
    assert run(tree).returncode == 0


def test_rule_five_numbered_section_prevents_fallback(tree: Path) -> None:
    write(
        tree,
        "CHANGELOG.md",
        f"## Unreleased\n### Promoted\n- {PAGE}\n## 2.4.6\n### Added\n- other\n",
    )
    fail(tree, 5)


def test_demotion(tree: Path) -> None:
    write(tree, PAGE, "Status: experimental.\n")
    fail(tree, 1, "a page once available is not demoted")


@pytest.mark.parametrize(
    "text",
    [
        f"## {PAGE}\n{section().split('since:', 1)[1]}",
        section().replace("since: 2.4.6", ""),
        section().replace("| none | done |", "| done |"),
        f"## {PAGE}\nsince: 2.4.6\n",
        section() + section(),
    ],
)
def test_malformed_section(tree: Path, text: str) -> None:
    write(tree, "docs/AVAILABLE.md", text)
    fail(tree, 2, "malformed section")


def receipt(
    tree: Path,
    *,
    version: str = "2.4.6",
    exit_code: int = 0,
    service: str = "postgres",
    **extra: object,
) -> None:
    write(
        tree,
        "docs/AVAILABLE.md",
        section(claim="Writes service:postgres", evidence="`docs/evidence/run.json`"),
    )
    write(
        tree,
        "docs/evidence/run.json",
        json.dumps(
            dict(
                run_id="123",
                commit="a" * 40,
                command="shape profile",
                shape=version,
                date="2026-10-14",
                service=service,
                exit_code=exit_code,
                status="passed",
                **extra,
            )
        ),
    )


def test_service_with_fresh_real_run(tree: Path) -> None:
    receipt(tree)
    assert run(tree).returncode == 0


def test_owner_receipt(tree: Path) -> None:
    write(
        tree,
        "docs/AVAILABLE.md",
        section(claim="Writes service:postgres", evidence="`docs/plans/evidence/run.md`"),
    )
    write(
        tree,
        "docs/plans/evidence/run.md",
        "Command: shape profile\n"
        "<!-- receipt: shape=2.4.6 service=postgres exit=0 date=2026-10-14 -->\n",
    )
    assert run(tree).returncode == 0


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "failed",
        "stale",
        "emulator",
        "outside",
        "no_version",
        "no_date",
        "no_command",
        "not_passed",
    ],
)
def test_bad_service_evidence(tree: Path, case: str) -> None:
    receipt(
        tree,
        version="2.4.5" if case == "stale" else "2.4.6",
        exit_code=1 if case == "failed" else 0,
        service="postgres-emulator" if case == "emulator" else "postgres",
    )
    path = tree / "docs/evidence/run.json"
    if case == "missing":
        path.unlink()
    elif case == "outside":
        write(tree, "run.json", path.read_text())
        write(
            tree,
            "docs/AVAILABLE.md",
            section(claim="Writes service:postgres", evidence="`run.json`"),
        )
    elif case.startswith("no_") or case == "not_passed":
        data = json.loads(path.read_text())
        if case == "not_passed":
            data["status"] = "failed"
        else:
            del data[{"no_version": "shape", "no_date": "date", "no_command": "command"}[case]]
        write(tree, "docs/evidence/run.json", json.dumps(data))
    fail(tree, 3)


def test_emulator_test_alone_is_not_evidence(tree: Path) -> None:
    write(
        tree,
        "tests/test_proof.py",
        "import pytest\n@pytest.mark.emulator\ndef test_proof():\n    pass\n",
    )
    write(tree, "docs/AVAILABLE.md", section(claim="Writes service:postgres"))
    fail(tree, 3)


def git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=tree, capture_output=True, text=True, check=True
    ).stdout.strip()


def base(tree: Path, *, with_list: bool = True) -> str:
    git(tree, "init", "-q")
    git(tree, "config", "user.name", "P-BAR fixture")
    git(tree, "config", "user.email", "fixture@example.invalid")
    if not with_list:
        (tree / LEGACY).unlink()
    git(tree, "add", ".")
    git(tree, "commit", "-q", "-m", "W0-available: fixture base")
    sha = git(tree, "rev-parse", "HEAD")
    git(tree, "update-ref", "refs/remotes/origin/main", sha)
    return sha


@pytest.mark.parametrize("missing", [False, True])
def test_stale_legacy_entry(tree: Path, missing: bool) -> None:
    write(tree, LEGACY, PAGE + "\n")
    write(tree, "docs/AVAILABLE.md", "# Available\n")
    if missing:
        (tree / PAGE).unlink()
    else:
        write(tree, PAGE, "Status: experimental.\n")
    result = run(tree)
    assert result.returncode == 1
    assert (
        f"AVAILABLE: {PAGE}: ratchet: no longer available; remove it from {LEGACY}" in result.stdout
    )


def test_unlisted_available_page_needs_rules(tree: Path) -> None:
    write(tree, "docs/AVAILABLE.md", "# Available\n")
    fail(tree, 2)


def test_legacy_exemption(tree: Path) -> None:
    write(tree, LEGACY, PAGE + "\n")
    write(tree, "docs/AVAILABLE.md", "# Available\n")
    write(tree, PAGE, "Status: available.\nUse `shape mask`.\n<!-- owner: not promoted yet -->\n")
    assert run(tree).returncode == 0


def test_ratchet_growth(tree: Path) -> None:
    base(tree)
    write(tree, LEGACY, PAGE + "\n")
    result = run(tree, "--require-base")
    assert result.returncode == 1
    assert f"AVAILABLE: {PAGE}: ratchet: the legacy list may only shrink" in result.stdout


def test_ratchet_shrinks(tree: Path) -> None:
    write(tree, LEGACY, PAGE + "\n")
    base(tree)
    write(tree, LEGACY, "")
    assert run(tree, "--require-base").returncode == 0


def test_introducing_list_has_no_growth_check(tree: Path) -> None:
    base(tree, with_list=False)
    write(tree, LEGACY, PAGE + "\n")
    result = run(tree, "--require-base")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no baseline" not in result.stderr


@pytest.mark.parametrize("mode", ["no_git", "no_main", "no_merge_base"])
def test_unavailable_baseline(tree: Path, mode: str) -> None:
    if mode != "no_git":
        original = base(tree)
        git(tree, "update-ref", "-d", "refs/remotes/origin/main")
        if mode == "no_merge_base":
            git(tree, "checkout", "--orphan", "unrelated")
            git(tree, "commit", "-q", "-m", "W0-available: fixture unrelated history")
            git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(tree, "checkout", "--detach", original)
    result = run(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("AVAILABLE: ratchet growth not checked: no baseline (") == 1
    required = run(tree, "--require-base")
    assert required.returncode == 2
    assert "no baseline" in required.stderr


@pytest.mark.parametrize("changed", [False, True])
def test_old_receipt_requires_unchanged_source(tree: Path, changed: bool) -> None:
    write(tree, "src/feature.py", "SOURCE = 1\n")
    sha = base(tree)
    receipt(tree, version="2.4.5", paths=["src/feature.py"])
    data = json.loads((tree / "docs/evidence/run.json").read_text())
    data["commit"] = sha
    write(tree, "docs/evidence/run.json", json.dumps(data))
    if changed:
        write(tree, "src/feature.py", "SOURCE = 2\n")
        git(tree, "add", "src/feature.py")
        git(tree, "commit", "-q", "-m", "W0-available: fixture changed source")
    result = run(tree, "--require-base")
    assert result.returncode == (1 if changed else 0), result.stdout + result.stderr
    if changed:
        assert "P-BAR 3:" in result.stdout


def test_usage_and_unreadable_input(tree: Path) -> None:
    assert run(tree, "--unknown-option").returncode == 2
    (tree / "docs/AVAILABLE.md").unlink()
    result = run(tree)
    assert result.returncode == 2
    assert "AVAILABLE:" in result.stderr


def test_experimental_fences_render_as_a_warning() -> None:
    spec = importlib.util.spec_from_file_location(
        "_available_docs_hook", ROOT / "scripts/mkdocs_hooks.py"
    )
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    page = SimpleNamespace(
        file=SimpleNamespace(src_uri="FEATURE.md"), meta={}, url="FEATURE/", title="Feature"
    )
    config = {
        "site_url": "https://docs.example.invalid/",
        "repo_url": "https://example.invalid/repo",
        "docs_dir": str(ROOT / "docs"),
    }
    files = SimpleNamespace(get_file_from_path=lambda name: None)
    result = hook.on_page_markdown(
        "Before\n<!-- experimental:start -->\nExperimental text.\n"
        "\n```bash\nshape mask\n```\n<!-- experimental:end -->\nAfter\n",
        page,
        config,
        files,
    )
    assert '!!! warning "Experimental"' in result
    assert "    Experimental text." in result
    assert "    ```bash\n    shape mask\n    ```" in result
    assert "<!-- experimental:" not in result
    assert "Before\n" in result and "\nAfter\n" in result


@pytest.mark.parametrize(
    "block",
    [
        "<!-- experimental:start -->\nshape mask\n",
        "<!-- experimental:end -->\n",
        "<!-- experimental:start --><!-- experimental:start --><!-- experimental:end -->",
    ],
)
def test_unbalanced_experimental_fences_fail(tree: Path, block: str) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write(block)
    fail(tree, 1, "malformed experimental fences")


def test_missing_git_executable(tree: Path) -> None:
    env = dict(os.environ, PATH=str(tree / "no-executables"))
    result = run(tree, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no baseline (git unavailable)" in result.stderr
    result = run(tree, "--require-base", env=env)
    assert result.returncode == 2


def test_job_name_outside_jobs_is_not_a_job(tree: Path) -> None:
    write(
        tree, ".github/workflows/ci.yml", "env:\n  pretend:\njobs:\n  test:\n    runs-on: linux\n"
    )
    write(tree, "docs/AVAILABLE.md", section(proof="`ci:ci.yml/pretend`"))
    fail(tree, 2)


def test_rule_five_does_not_match_a_page_name_prefix(tree: Path) -> None:
    write(tree, "CHANGELOG.md", "## Unreleased\n### Promoted\n- docs/FEATURE.md.other\n")
    fail(tree, 5)


def test_plans_and_talks_are_outside_the_page_inventory(tree: Path) -> None:
    for path in ("docs/plans/private.md", "docs/talks/talk.md"):
        write(tree, path, "Status: available.\nshape mask\n<!-- owner: private -->\n")
    assert run(tree).returncode == 0


def test_since_line_must_precede_the_table(tree: Path) -> None:
    write(tree, "docs/AVAILABLE.md", section().replace("since: 2.4.6\n", "") + "since: 2.4.6\n")
    fail(tree, 2, "malformed section")


def test_owner_note_inside_experimental_block_still_blocks_promotion(tree: Path) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write(
            "\n<!-- experimental:start -->\n<!-- owner: needs a real run -->\n"
            "<!-- experimental:end -->\n"
        )
    fail(tree, 4)


def test_warning_hook_preserves_inline_fence_documentation() -> None:
    spec = importlib.util.spec_from_file_location(
        "_available_docs_hook", ROOT / "scripts/mkdocs_hooks.py"
    )
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    text = "Use `<!-- experimental:start -->` and `<!-- experimental:end -->`.\n"
    assert hook.experimental_markdown(text) == text


def test_checker_loads_like_the_existing_repository_checks() -> None:
    spec = importlib.util.spec_from_file_location(
        "_available_check", ROOT / "scripts/check_available.py"
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.main(["--unknown-option"]) == 2


def test_commandless_page_can_be_available(tree: Path) -> None:
    write(tree, PAGE, f"Status: available.\n<!-- claim: {CLAIM} -->\nA documented property.\n")
    assert run(tree).returncode == 0


def test_command_page_needs_a_covers_declaration(tree: Path) -> None:
    write(tree, PAGE, (tree / PAGE).read_text().replace("Covers: shape profile\n", ""))
    fail(tree, 1, "add it to Covers or fence it")


def test_inline_delimiters_cannot_exempt_an_unrendered_command(tree: Path) -> None:
    with (tree / PAGE).open("a") as stream:
        stream.write("\n<!-- experimental:start --> shape mask <!-- experimental:end -->\n")
    fail(tree, 1, "malformed experimental fences")
