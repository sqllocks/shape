"""Shared helpers for the Fabric lane tests (imported by conftest and the test modules).

* Always uses the real ``shape`` API (section 12.2); a non-conforming API fails loudly
  (the stub used during parallel development was removed at integration).
* Provides a ``notebookutils`` stub that captures the exit value.
* Provides day-1 / day-2 fixture Delta tables standing in for the lane-L3 demo data.
"""

from __future__ import annotations

import contextlib
import sys
import types
from collections.abc import Iterator
from pathlib import Path, PurePath
from typing import Any

import nbformat
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NOTEBOOKS = REPO / "integrations" / "fabric" / "notebooks"
UDF_DIR = REPO / "integrations" / "fabric" / "udf"
PIPELINES = REPO / "integrations" / "fabric" / "pipelines"


def _real_api_conforms() -> bool:
    try:
        import shape

        df = pd.DataFrame({"a": [1, 2, 3]})
        p = shape.profile(df, name="t")
        return all(hasattr(p, m) for m in ("to_dict", "summary", "to_html")) and all(
            hasattr(shape, m) for m in ("save", "load", "check", "diff")
        )
    except Exception:
        return False


def _is_shape(name: str) -> bool:
    return name == "shape" or name.startswith("shape.")


SHAPE_API = "unselected"


@contextlib.contextmanager
def shape_api() -> Iterator[str]:
    """Always the real section 12.2 API.

    The test-only stub that let this lane develop in parallel with lane L1 was removed at
    integration: the Fabric artifacts must be tested against the real ``shape`` package,
    and a non-conforming API must fail loudly rather than fall back.
    """
    global SHAPE_API
    if not _real_api_conforms():
        raise AssertionError("the installed shape package does not conform to the section 12.2 API")
    SHAPE_API = "real"
    yield SHAPE_API


def report_header() -> str:
    return "fabric lane: shape API = real (section 12.2)"


# ------------------------------------------------------------------ notebookutils stub


class NotebookExit(BaseException):
    """Real ``notebook.exit`` ends the notebook; the stub does the same."""

    def __init__(self, value: str):
        super().__init__(value)
        self.value = value


def _install_notebookutils() -> None:
    mod = types.ModuleType("notebookutils")

    def _exit(value: str = "") -> None:
        raise NotebookExit(value)

    mod.notebook = types.SimpleNamespace(exit=_exit)  # type: ignore[attr-defined]
    sys.modules["notebookutils"] = mod


_install_notebookutils()


def code_cells(path: Path) -> list[nbformat.NotebookNode]:
    nb = nbformat.read(path, as_version=4)
    return [c for c in nb.cells if c.cell_type == "code"]


def _strip_magics(src: str) -> str | None:
    if src.lstrip().startswith("%%"):
        return None  # cell magic (%%configure): skipped
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("%"))


def fabric_source(src: str, lakehouse: PurePath) -> str:
    """Point a notebook cell at ``lakehouse``; Fabric paths use forward slashes everywhere."""
    return src.replace("/lakehouse/default", lakehouse.as_posix())


def run_notebook(
    path: Path,
    lakehouse: Path,
    params: dict[str, Any] | None = None,
    extra_globals: dict[str, Any] | None = None,
    replacements: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Execute a notebook's code cells; return (exit value or None, namespace)."""
    ns: dict[str, Any] = {"__name__": "__main__"}
    ns.update(extra_globals or {})
    exit_value: str | None = None
    for cell in code_cells(path):
        src = _strip_magics(cell.source)
        if src is None:
            continue
        src = fabric_source(src, lakehouse)
        for old, new in (replacements or {}).items():
            src = src.replace(old, new)
        try:
            exec(compile(src, f"{path.name}[cell]", "exec"), ns)
        except NotebookExit as e:
            exit_value = e.value
            break
        if "parameters" in cell.metadata.get("tags", []) and params:
            ns.update(params)  # what Fabric/papermill inject after the parameters cell
    return exit_value, ns


def pip_line(source: str) -> str:
    """The ``%pip install`` line of a notebook cell."""
    return next(ln for ln in source.splitlines() if ln.lstrip().startswith("%pip install"))


def installs_by_path(source: str, *dists: str) -> bool:
    """True when the cell's ``%pip`` line installs Shape and each of ``dists`` (``shape_domains``
    ...) from a wheel file in ``builtin/``, and nothing by name and version: PyPI has an older
    ``sqllocks-shape`` 0.9.0, which a requirement by name can resolve to (DEMO-LIVE F-4)."""
    args = pip_line(source).split()[2:]
    if "--find-links" in args or any("==" in a for a in args):
        return False
    wheels = [a for a in args if a.startswith("builtin/") and a.endswith(".whl")]
    others = [a for a in args if a not in wheels]
    # numpy 2, and a pandas built for it: the runtime's pandas 2.1.4 cannot load with numpy 2
    if others != ['"numpy>=2,<3"', '"pandas>=2.2.2,<3"']:
        return False
    names = {w.removeprefix("builtin/").split("-")[0] for w in wheels}
    return names == {"sqllocks_shape", *(f"sqllocks_{d}" for d in dists)}


# The default lakehouse's OneLake Tables URI under the ``fabric_onelake`` fixture (conftest.py).
ONELAKE_TABLES = (
    "abfss://11111111-2222-3333-4444-555555555555@onelake.dfs.fabric.microsoft.com/"
    "66666666-7777-8888-9999-000000000000/Tables"
)


# ------------------------------------------------------------------- fixture data


def make_orders(day: int, n: int = 2000, seed: int = 7) -> pd.DataFrame:
    """Stand-in for the lane-L3 retail data, with the same kind of drift as demo/DRIFT.md."""
    rng = np.random.default_rng(seed)
    status = rng.choice(["placed", "shipped", "returned"], size=n, p=[0.5, 0.4, 0.1])
    amount = rng.lognormal(mean=3.0, sigma=0.5, size=n)
    email = np.array([f"user{i}@example.com" for i in range(n)], dtype=object)
    null_rate = 0.05 if day == 1 else 0.20
    email[rng.random(n) < null_rate] = None
    if day == 2:
        status[:20] = "lost"
        amount = amount * 1.4
    return pd.DataFrame(
        {
            "customer_id": np.arange(1, n + 1, dtype="int64"),
            "email": email,
            "status": status,
            "amount": amount,
        }
    )


CONTRACT = {
    "row_count": {"min": 1000, "max": 5_000_000},
    "columns": {
        "customer_id": {"dtype": "integer", "nullable": False, "unique": True},
        "email": {"max_null_rate": 0.10},
        "status": {"allowed_values": ["placed", "shipped", "returned"]},
        "amount": {"min": 0, "max": 100000},
    },
    "required_columns": ["customer_id"],
    "allow_extra_columns": True,
}
