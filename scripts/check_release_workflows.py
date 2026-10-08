"""Check that the release workflows keep T-25 and T-04 (P8-04).

T-25: GitHub Actions trusted publishing (TestPyPI, then PyPI), a CycloneDX SBOM and Sigstore
build attestations. O-01 registered the trusted publisher as workflow ``publish.yml`` with the
environments ``pypi`` and ``testpypi``, so those names are part of the contract. What is checked:

``publish.yml``
  * triggers: a manual run choosing ``testpypi`` or ``pypi``, and ``v*`` tags; nothing else;
  * workflow permissions are ``contents: read``; only the build call and the publishing job get
    ``id-token: write``;
  * the archives come from ``release.yml`` (one build for every index);
  * every ``pypa/gh-action-pypi-publish`` step is ``@release/v1`` with no password, user or token,
    in a job that needs the build and runs in the environment ``testpypi`` or ``pypi``;
  * after publishing, ``scripts/release_index_check.py`` proves the index serves exactly the
    archives that were built (by SHA-256; this is what makes ``skip-existing`` re-runs safe), and
    ``scripts/release_smoke.py`` installs ``[all]`` back from the index.

``release.yml``
  * callable (``workflow_call`` with a ``version`` output), never triggered by a push or PR;
  * the T-04 wheels come from ``wheels.yml``; the versions, the pure wheel and sdists, the full
    release set, the per-archive SBOMs (with the CycloneDX schema), the environment SBOM and the
    local smoke test are each run by their script;
  * ``actions/attest-build-provenance`` and ``actions/attest-sbom`` run in the one job that
    holds ``id-token: write`` and ``attestations: write``; nothing in it publishes.

``wheels.yml``
  * callable, and its matrix builds exactly the T-04 targets
    (``scripts/build_release_dist.py``'s ``T04_TARGETS``) with the right manylinux/musllinux
    policy, each tested on Python 3.11 and 3.14.

No workflow here may read a repository secret.

    python scripts/check_release_workflows.py                    # .github/workflows
    python scripts/check_release_workflows.py --dir path/to/workflows

Exit status: 0 when every check holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_release_dist import ROOT, T04_TARGETS  # noqa: E402

PUBLISH_ACTION = "pypa/gh-action-pypi-publish"
SECRET_INPUTS = {"password", "user", "api-token", "token"}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a workflow")
    # YAML 1.1 reads the bare key `on` as the boolean true
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in job.get("steps", []) if isinstance(s, dict)]


def _runs(wf: dict[str, Any]) -> str:
    return "\n".join(
        str(s.get("run", "")) for job in wf.get("jobs", {}).values() for s in _steps(job)
    )


def _uses(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [s for s in _steps(job) if str(s.get("uses", "")).split("@")[0] == action]


def _perms(job: dict[str, Any]) -> dict[str, Any]:
    perms = job.get("permissions", {})
    return perms if isinstance(perms, dict) else {}


def _writes(job: dict[str, Any], scope: str) -> bool:
    return _perms(job).get(scope) == "write" or job.get("permissions") == "write-all"


def check_publish(wf: dict[str, Any], text: str) -> list[str]:
    where = "publish.yml"
    problems: list[str] = []
    on = wf.get("on", {})
    if set(on) != {"workflow_dispatch", "push"}:
        problems.append(
            f"{where}: triggers must be workflow_dispatch and tag pushes, not {sorted(on)}"
        )
    repo_input = (on.get("workflow_dispatch") or {}).get("inputs", {}).get("repository", {})
    if repo_input.get("options") != ["testpypi", "pypi"]:
        problems.append(f"{where}: the manual run must choose repository testpypi or pypi")
    push = on.get("push") or {}
    if push.get("tags") != ["v*"] or set(push) != {"tags"}:
        problems.append(f"{where}: pushes must trigger on v* tags only")
    if wf.get("permissions") != {"contents": "read"}:
        problems.append(f"{where}: workflow permissions must be contents: read")
    jobs: dict[str, dict[str, Any]] = wf.get("jobs", {})
    builds = [n for n, j in jobs.items() if j.get("uses") == "./.github/workflows/release.yml"]
    if len(builds) != 1:
        problems.append(f"{where}: one job must call ./.github/workflows/release.yml")
    publishers = [n for n, j in jobs.items() if _uses(j, PUBLISH_ACTION)]
    if not publishers:
        problems.append(f"{where}: no {PUBLISH_ACTION} step")
    for name, job in jobs.items():
        if _writes(job, "id-token") and name not in {*builds, *publishers}:
            problems.append(f"{where}: job {name} must not hold id-token: write")
    for name in publishers:
        job = jobs[name]
        env = str(job.get("environment", ""))
        if "'pypi'" not in env or "'testpypi'" not in env:
            problems.append(f"{where}: job {name} must run in the environment pypi or testpypi")
        if not _writes(job, "id-token"):
            problems.append(f"{where}: job {name} needs id-token: write (trusted publishing)")
        needs = job.get("needs", [])
        if not set(builds) <= set([needs] if isinstance(needs, str) else needs):
            problems.append(f"{where}: job {name} must need the build")
        if _steps(job) and any("checkout" in str(s.get("uses", "")) for s in _steps(job)):
            problems.append(f"{where}: job {name} must publish the built archives, not a checkout")
        for step in _uses(job, PUBLISH_ACTION):
            if not str(step["uses"]).endswith("@release/v1"):
                problems.append(f"{where}: {step['uses']} must be @release/v1")
            if SECRET_INPUTS & set(step.get("with") or {}):
                problems.append(f"{where}: {step['uses']} must use trusted publishing, no token")
    for script, what in (
        ("scripts/release_index_check.py", "check the index's SHA-256 against SHA256SUMS"),
        ("scripts/release_smoke.py", "install from the index"),
    ):
        after = [
            n
            for n, j in jobs.items()
            if script in "\n".join(str(s.get("run", "")) for s in _steps(j))
        ]
        if not after or not all(set(publishers) <= set(jobs[n].get("needs", [])) for n in after):
            problems.append(f"{where}: {script} must {what} after publishing")
    return problems


def check_release(wf: dict[str, Any], text: str) -> list[str]:
    where = "release.yml"
    problems: list[str] = []
    on = wf.get("on", {})
    call = on.get("workflow_call") if isinstance(on, dict) else None
    if not isinstance(call, dict) or "version" not in (call.get("outputs") or {}):
        problems.append(f"{where}: must be callable (workflow_call) with a version output")
    if isinstance(on, dict) and {"push", "pull_request", "pull_request_target"} & set(on):
        problems.append(f"{where}: must not run on a push or a pull request")
    if wf.get("permissions") != {"contents": "read"}:
        problems.append(f"{where}: workflow permissions must be contents: read")
    jobs: dict[str, dict[str, Any]] = wf.get("jobs", {})
    if not any(j.get("uses") == "./.github/workflows/wheels.yml" for j in jobs.values()):
        problems.append(f"{where}: the T-04 wheels must come from ./.github/workflows/wheels.yml")
    runs = _runs(wf)
    for needle in (
        "scripts/check_versions.py",
        "scripts/build_release_dist.py --out",
        "scripts/build_release_dist.py --check-set",
        "scripts/release_sbom.py build",
        "scripts/release_sbom.py environment",
        "scripts/release_smoke.py",
        "twine check --strict",
    ):
        if needle not in runs:
            problems.append(f"{where}: no step runs {needle}")
    if not any("release_sbom.py check" in r and "--schema" in r for r in runs.splitlines()):
        problems.append(f"{where}: the SBOMs must be checked against the CycloneDX schema")
    attesting = [
        n
        for n, j in jobs.items()
        if _uses(j, "actions/attest-build-provenance") and _uses(j, "actions/attest-sbom")
    ]
    if len(attesting) != 1:
        problems.append(f"{where}: one job must run attest-build-provenance and attest-sbom")
    for name, job in jobs.items():
        writes = _writes(job, "id-token") or _writes(job, "attestations")
        if name in attesting and not (_writes(job, "id-token") and _writes(job, "attestations")):
            problems.append(f"{where}: job {name} needs id-token and attestations: write")
        if writes and name not in attesting:
            problems.append(f"{where}: job {name} must not hold id-token or attestations: write")
        if _uses(job, PUBLISH_ACTION):
            problems.append(f"{where}: job {name} publishes; only publish.yml may")
    return problems


def check_wheels(wf: dict[str, Any], text: str) -> list[str]:
    where = "wheels.yml"
    problems: list[str] = []
    on = wf.get("on", {})
    if not isinstance(on, dict) or "workflow_call" not in on:
        problems.append(f"{where}: must be callable (workflow_call) from release.yml")
    jobs: dict[str, dict[str, Any]] = wf.get("jobs", {})
    include = (jobs.get("wheel", {}).get("strategy", {}).get("matrix", {}) or {}).get("include", [])
    names = [str(e.get("name")) for e in include if isinstance(e, dict)]
    if sorted(names) != sorted(T04_TARGETS):
        problems.append(
            f"{where}: the wheel matrix is {sorted(names)}, T-04 is {sorted(T04_TARGETS)}"
        )
    for entry in include:
        name = str(entry.get("name", ""))
        want = {"manylinux": "2_28", "musllinux": "musllinux_1_2"}.get(name.split("-")[0])
        if want is not None and entry.get("manylinux") != want:
            problems.append(f"{where}: {name} must build with manylinux: {want!r}")
    if "'3.11'" not in text or "'3.14'" not in text:
        problems.append(f"{where}: the wheels must be tested on Python 3.11 and 3.14")
    return problems


CHECKS = {"publish.yml": check_publish, "release.yml": check_release, "wheels.yml": check_wheels}


def check(directory: Path) -> list[str]:
    problems: list[str] = []
    for name, fn in CHECKS.items():
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        if "secrets." in text:
            problems.append(f"{name}: reads a repository secret")
        try:
            problems += fn(_load(path), text)
        except (ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
            problems.append(f"{name}: unreadable ({exc})")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", type=Path, default=ROOT / ".github" / "workflows")
    ns = p.parse_args(argv)
    problems = check(ns.dir)
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if not problems:
        print(f"release workflows OK: {', '.join(CHECKS)} keep T-25 and T-04")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
