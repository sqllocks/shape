"""The interfaces P4-04b/c/d, P4-06 and P4-07 build on are documented, and what the docs say
exists: every name in a module's ``Stable interface`` docstring line appears in the docs page, and
every function of the kernel contract table exists in both implementations."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _read(name: str) -> str:
    return (DOCS / name).read_text("utf-8")


def _stable_names(module: str) -> list[str]:
    doc = importlib.import_module(module).__doc__ or ""
    m = re.search(r"Stable interface:(.*?)(?:\n\n|\Z)", doc, re.S)
    assert m, f"{module} has no 'Stable interface' line"
    return re.findall(r"``(\w+)``", m.group(1))


@pytest.mark.parametrize(
    ("module", "page"),
    [
        ("shape.generation.strategy_kit", "GENERATION_STRATEGIES.md"),
        ("shape.generation.kernel_ops", "GENERATION_STRATEGIES.md"),
        ("shape.generation.fanout", "GENERATION_STRATEGIES.md"),
        ("shape.generation.rng", "GENERATION_ENGINE.md"),
    ],
)
def test_stable_names_exist_and_are_documented(module, page):
    names = _stable_names(module)
    assert names
    mod = importlib.import_module(module)
    text = _read(page) + _read("GENERATION_KERNEL.md")
    for name in names:
        assert hasattr(mod, name), f"{module}.{name} is listed as stable but missing"
        assert name in text, f"{module}.{name} is not in docs/{page}"


def test_every_kernel_function_of_the_contract_exists_twice():
    from shape import _kernel as native
    from shape.kernel import reference

    doc = _read("GENERATION_KERNEL.md")
    names = re.findall(r"^\| `(\w+)\(", doc, re.M)
    assert len(names) >= 14
    for name in names:
        assert callable(getattr(native, name)), f"native {name}"
        assert callable(getattr(reference, name)), f"reference {name}"


def test_the_family_extension_point_is_documented():
    from shape.builtins.distributions import families

    page = _read("GENERATION_STRATEGIES.md")
    for name in ("Family", "FAMILIES", "from_spec", "draw", "words_per_row"):
        assert name in page
    assert callable(families.Family.draw) and callable(families.Family.fit)
    assert set(families.FAMILIES) >= {"uniform", "normal", "gamma", "mixture", "truncated"}


def test_calendar_docs_cover_every_spec_key():
    page = _read("GENERATION_CALENDARS.md")
    for key in (
        "ramp_up_days", "decay_days", "payday", "month_end", "quarter_end", "trend", "events",
        "calendars", "holiday_lift", "lifts", "annual_growth", "steps", "ramps", "anchor",
        "sample_timestamps", "temporal_profile", "holiday_lifts", "tail_index",
    ):  # fmt: skip
        assert key in page, key
