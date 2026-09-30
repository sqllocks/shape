"""Shared fixtures for the Fabric lane tests.

* Uses the real ``shape`` API (section 12.2) when it is importable and conforms;
  otherwise falls back to the test-only stub in ``stub_shape/``.
* Provides a ``notebookutils`` stub that captures the exit value.
* Provides day-1 / day-2 fixture Delta tables standing in for the lane-L3 demo data.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import nbformat
import numpy as np
import pandas as pd
import pytest

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


def _select_api() -> str:
    if _real_api_conforms():
        return "real"
    for m in [m for m in sys.modules if m == "shape" or m.startswith("shape.")]:
        del sys.modules[m]
    sys.path.insert(0, str(HERE / "stub_shape"))
    return "stub"


SHAPE_API = _select_api()


def pytest_report_header(config: pytest.Config) -> str:
    return f"fabric lane: shape API under test = {SHAPE_API}"


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
        src = src.replace("/lakehouse/default", str(lakehouse))
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


@pytest.fixture()
def lakehouse(tmp_path: Path) -> Path:
    """A directory standing in for /lakehouse/default, with day-1/day-2 Delta tables."""
    from deltalake import write_deltalake

    root = tmp_path / "lakehouse"
    (root / "Files" / "contracts").mkdir(parents=True)
    (root / "Tables").mkdir()
    for day in (1, 2):
        write_deltalake(str(root / "Tables" / f"orders_day{day}"), make_orders(day))
    (root / "Files" / "contracts" / "orders.json").write_text(json.dumps(CONTRACT))
    import shape

    base = shape.profile(make_orders(1), name="orders_day1")
    (root / "Files" / "baselines").mkdir()
    shape.save(base, str(root / "Files" / "baselines" / "orders_day1.shape"))
    return root
