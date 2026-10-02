"""``shape doctor``: what this installation can and cannot do, and why."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import platform
import sys
from typing import Any

# (import name, distribution name, what needs it)
REQUIRED = (
    ("numpy", "numpy", "everything"),
    ("pyarrow", "pyarrow", "reading and profiling data"),
)
OPTIONAL = (
    ("cryptography", "cryptography", "signing and verifying artifacts (--sign, keygen, verify)"),
    ("yaml", "pyyaml", "YAML generation schemas and scenario packs"),
    ("pandas", "pandas", "profiling a pandas DataFrame"),
    ("scipy", "scipy", "the statistical tests of `shape verify --statistical`"),
    ("deltalake", "deltalake", "reading and writing Delta tables"),
    ("openpyxl", "openpyxl", "Excel output (`shape generate --format excel`)"),
    ("sklearn", "scikit-learn", "fidelity tiers 2 and 3 (`shape fidelity --tier`)"),
    ("tzdata", "tzdata", "time zones on a system without a time-zone database (Windows)"),
)


def _installed(module: str, dist: str) -> str | None:
    """The installed version, or None when the package is not importable."""
    try:
        if importlib.util.find_spec(module) is None:
            return None
    except (ImportError, ValueError):
        return None
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def report() -> dict[str, Any]:
    """The facts, as a dictionary: ``ok`` is False when a required package is missing."""
    from shape import __version__
    from shape.kernel import kernel_name

    required = {m: _installed(m, d) for m, d, _ in REQUIRED}
    optional = {m: _installed(m, d) for m, d, _ in OPTIONAL}
    py_ok = sys.version_info >= (3, 11)
    return {
        "shape": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "kernel": kernel_name(),
        "required": required,
        "optional": optional,
        "ok": py_ok and all(v is not None for v in required.values()),
        # flat, for scripts that read the earlier output
        **{
            m: v
            for m, v in {**required, **optional}.items()
            if m in ("pyarrow", "cryptography", "yaml")
        },
    }


def render(rep: dict[str, Any]) -> str:
    lines = [
        f"Shape {rep['shape']}",
        f"  python    {rep['python']}  ({rep['platform']})",
        f"  kernel    {rep['kernel']}"
        + ("  (compiled)" if rep["kernel"] == "rust" else "  (pure Python; set SHAPE_KERNEL=rust)"),
        "",
        "Required",
    ]
    for m, _d, why in REQUIRED:
        v = rep["required"][m]
        lines.append(
            f"  {'OK' if v else 'MISSING':<8}{m:<14}{v or 'not installed (needed for ' + why + ')'}"
        )
    lines += ["", "Optional"]
    for m, _d, why in OPTIONAL:
        v = rep["optional"][m]
        lines.append(f"  {'OK' if v else 'missing':<8}{m:<14}{v or 'needed for ' + why}")
    lines += ["", "Result: " + ("OK" if rep["ok"] else "FAILED (a required package is missing)")]
    return "\n".join(lines)


def run(a: Any) -> int:
    rep = report()
    if a.json:
        print(json.dumps(rep, sort_keys=True))
    else:
        print(render(rep))
    return 0 if rep["ok"] else 1
