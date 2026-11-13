"""Isolate frozen transcript registries and validate command-runtime metadata."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from importlib import metadata
from pathlib import Path

FIRST_PARTY_DISTRIBUTIONS = (
    "sqllocks-shape",
    "sqllocks-shape-behavior",
    "sqllocks-shape-databases",
    "sqllocks-shape-dbt",
    "sqllocks-shape-domains",
    "sqllocks-shape-eventhubs",
    "sqllocks-shape-fabric",
    "sqllocks-shape-healthcare-codes",
    "sqllocks-shape-healthcare-standards",
    "sqllocks-shape-integrations",
    "sqllocks-shape-kafka",
    "sqllocks-shape-simulation",
    "sqllocks-shape-sqlserver",
)
PANDAS_DOCTOR = re.compile(r"(?m)^(  OK[ \t]+pandas[ \t]+)(\S+)([ \t]*)$")


def is_doctor_command(arguments: list[str], original: list[str]) -> bool:
    """Recognize the top-level console/module command, not another command's arguments."""
    if len(arguments) > 1 and Path(arguments[0]).name == "shape":
        return arguments[1] == "doctor"
    if "-m" in original:
        module = original.index("-m")
        return original[module + 1 : module + 3] == ["shape", "doctor"]
    return False


def install_reference_registry(startup: Path) -> None:
    """Replace discovery for this subprocess's default host, leaving other hosts alone."""
    from shape.plugins import host
    from shape.plugins.registry import register_builtins

    entries = tuple(
        entry
        for name in FIRST_PARTY_DISTRIBUTIONS
        for entry in metadata.distribution(name).entry_points
    )
    config, error = host.project_plugins_config()
    baseline = host.PluginHost(entry_points=lambda: entries, project_config=config)
    baseline._project_error = error
    register_builtins(baseline)
    host._default = baseline
    if is_doctor_command(sys.argv, sys.orig_argv):
        pandas: str | None
        try:
            pandas = metadata.version("pandas")
        except metadata.PackageNotFoundError:
            pandas = None
        with (startup / f"doctor-{os.getpid()}.json").open("x") as receipt:
            json.dump({"pandas": pandas}, receipt)


def reference_environment(environment: dict[str, str], startup: Path) -> dict[str, str]:
    """Return a child-only environment; installed packages and the caller are unchanged."""
    # A fresh directory per command prevents receipts from a prior block being reused.
    startup.mkdir(parents=True)
    (startup / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from docs_reference_environment import install_reference_registry\n"
        f"install_reference_registry(Path({str(startup)!r}))\n"
    )
    paths = [str(startup), str(Path(__file__).resolve().parent)]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    return {**environment, "PYTHONPATH": os.pathsep.join(paths)}


def validated_doctor_pandas(actual: str, recorded: str, startup: Path) -> tuple[str, str]:
    """Compare only the Pandas version as runtime data after exact interpreter checks."""
    from packaging.version import Version

    versions = []
    for path in sorted(startup.glob("doctor-*.json")):
        receipt = json.loads(path.read_text())
        assert isinstance(receipt, dict) and set(receipt) == {"pandas"}, path
        version = receipt["pandas"]
        assert isinstance(version, str), "doctor runtime has no installed pandas metadata"
        Version(version)
        versions.append(version)
    actual_versions = [match[1] for match in PANDAS_DOCTOR.findall(actual)]
    recorded_versions = [match[1] for match in PANDAS_DOCTOR.findall(recorded)]
    assert len(actual_versions) == len(recorded_versions) == len(versions), (
        "doctor Pandas fields must each have a fresh command-interpreter receipt"
    )
    assert Counter(actual_versions) == Counter(versions), (
        "doctor Pandas version differs from its command interpreter's installed metadata"
    )
    for version in recorded_versions:
        Version(version)

    def canonical(text: str) -> str:
        return PANDAS_DOCTOR.sub(r"\g<1><installed-pandas-version>\g<3>", text)

    return canonical(actual), canonical(recorded)
