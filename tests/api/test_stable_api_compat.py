"""W7-07: the compatibility check for the stable Python modules.

``scripts/stable_api_compat.py --check`` compares the live modules with the committed baseline
(``stable_api_baseline.json``). The meta-tests apply each kind of break (and each additive kind)
to a synthetic copy of the two modules and run the real command line on it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "stable_api_compat", ROOT / "scripts" / "stable_api_compat.py"
)
assert _spec is not None and _spec.loader is not None
compat = importlib.util.module_from_spec(_spec)
sys.modules["stable_api_compat"] = compat
_spec.loader.exec_module(compat)

SRC = ROOT / "src" / "shape" / "generation"
BASELINE = compat.BASELINE


def test_the_committed_baseline_matches_the_live_modules() -> None:
    assert compat.main(["--check"]) == 0


def test_baseline_declares_format_and_integer_version() -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert data["format"] == "shape-stable-api-baseline"
    assert data["version"] == 1 and isinstance(data["version"], int)
    assert set(data["modules"]) == set(compat.MODULES)


def test_baseline_covers_every_exported_name_with_its_kind() -> None:
    from shape.generation import spec_edit, spec_schema

    names = json.loads(BASELINE.read_text(encoding="utf-8"))["modules"]
    for mod in (spec_edit, spec_schema):
        recorded = names[mod.__name__]["names"]
        assert sorted(recorded) == sorted(mod.__all__)
    edit = names["shape.generation.spec_edit"]["names"]
    assert edit["SpecProblem"]["kind"] == "dataclass" and edit["SpecProblem"]["frozen"] is True
    assert [f["name"] for f in edit["SpecProblem"]["fields"]] == [
        "level",
        "pointer",
        "message",
        "line",
        "column",
    ]
    assert edit["SpecError"]["kind"] == "exception" and edit["SpecError"]["bases"] == [
        "shape.errors.ShapeSchemaError"
    ]
    doc = edit["SpecDocument"]["members"]
    assert doc["loads"]["kind"] == "classmethod" and doc["table_names"]["kind"] == "property"
    assert doc["add_column"]["params"][-1]["kind"] == "VAR_KEYWORD"
    assert edit["validate_text"]["params"][0] == {
        "name": "text",
        "kind": "POSITIONAL_OR_KEYWORD",
        "annotation": "str",
        "default": None,
    }
    assert edit["Position"]["kind"] == "alias"


# ---- a newer baseline version is refused with a message ----


def _write(path: Path, data: dict[str, object]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_newer_baseline_version_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    data["version"] = 2
    assert compat.main(["--check", "--baseline", str(_write(tmp_path / "b.json", data))]) == 2
    err = capsys.readouterr().err
    assert "version 2" in err and "newer" in err


@pytest.mark.parametrize(
    ("patch", "fragment"),
    [
        ({"format": "something-else"}, "not a shape-stable-api-baseline"),
        ({"version": "1"}, "integer"),
        ({"version": True}, "integer"),
        ({"version": 0}, "not a valid"),
        ({"modules": []}, "not a valid"),
    ],
)
def test_a_malformed_baseline_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], patch: dict[str, object], fragment: str
) -> None:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    data.update(patch)
    assert compat.main(["--check", "--baseline", str(_write(tmp_path / "b.json", data))]) == 2
    assert fragment in capsys.readouterr().err


def test_a_missing_or_unreadable_baseline_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert compat.main(["--check", "--baseline", str(tmp_path / "none.json")]) == 2
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert compat.main(["--check", "--baseline", str(tmp_path / "bad.json")]) == 2
    assert "cannot read" in capsys.readouterr().err


# ---- synthetic copies: each break fails and names itself; each additive change passes ----


@pytest.fixture
def copy_dir(tmp_path: Path) -> Path:
    d = tmp_path / "copy"
    d.mkdir()
    for short in ("spec_edit", "spec_schema"):
        shutil.copy(SRC / f"{short}.py", d / f"{short}.py")
    return d


def edit(copy_dir: Path, module: str, old: str, new: str) -> None:
    path = copy_dir / f"{module}.py"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} must occur once in {module}.py"
    path.write_text(text.replace(old, new), encoding="utf-8")


def run(copy_dir: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = compat.main(["--check", "--source", str(copy_dir)])
    return code, capsys.readouterr().out


def test_an_unmodified_copy_passes(copy_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(copy_dir, capsys)[0] == 0


BREAKS = {
    "removed name (function)": (
        "spec_schema",
        "def render()",
        "def render_text()",
        "shape.generation.spec_schema.render: name was removed",
    ),
    "removed name (all)": (
        "spec_edit",
        '"validate_text"]',
        "]",
        "shape.generation.spec_edit.validate_text: name was removed",
    ),
    "renamed class": ("spec_edit", "class SpecDocument:", "class Document:", "SpecDocument"),
    "removed method": (
        "spec_edit",
        "    def remove_table(self, name: str)",
        "    def drop_table(self, name: str)",
        "SpecDocument.remove_table: member was removed",
    ),
    "renamed parameter": (
        "spec_edit",
        "def validate_text(text: str)",
        "def validate_text(source: str)",
        "validate_text: parameter 'text' changed",
    ),
    "removed parameter": (
        "spec_edit",
        "def get(self, pointer: str)",
        "def get(self)",
        "SpecDocument.get: parameter 'pointer' was removed",
    ),
    "reordered parameters": (
        "spec_edit",
        "def remove_column(self, table: str, name: str)",
        "def remove_column(self, name: str, table: str)",
        "SpecDocument.remove_column: parameter 'table' changed",
    ),
    "new required parameter": (
        "spec_edit",
        "def remove_table(self, name: str)",
        "def remove_table(self, name: str, cascade: bool)",
        "SpecDocument.remove_table: new parameter 'cascade' has no default",
    ),
    "new required keyword-only parameter": (
        "spec_edit",
        "def remove_table(self, name: str)",
        "def remove_table(self, name: str, *, cascade: bool)",
        "SpecDocument.remove_table: new parameter 'cascade' has no default",
    ),
    "changed default": (
        "spec_edit",
        "text: str | None = None,\n        positions",
        "text: str | None = '',\n        positions",
        "SpecDocument.__init__: parameter 'text' changed",
    ),
    "changed parameter kind": (
        "spec_edit",
        "def set(self, pointer: str, value: Any)",
        "def set(self, pointer: str, /, value: Any)",
        "SpecDocument.set: parameter 'pointer' changed",
    ),
    "changed return annotation": (
        "spec_edit",
        "def dumps(self) -> str:",
        "def dumps(self) -> bytes:",
        "SpecDocument.dumps: return type changed",
    ),
    "changed property type": (
        "spec_edit",
        "def table_names(self) -> list[str]:",
        "def table_names(self) -> tuple[str, ...]:",
        "SpecDocument.table_names: type changed",
    ),
    "classmethod becomes method": (
        "spec_edit",
        "    @classmethod\n    def loads(cls, text: str)",
        "    def loads(cls, text: str)",
        "SpecDocument.loads: kind changed classmethod -> method",
    ),
    "removed dataclass field": (
        "spec_edit",
        "    column: int | None = None\n",
        "",
        "SpecProblem: field 'column' was removed",
    ),
    "retyped dataclass field": (
        "spec_edit",
        "    line: int | None = None",
        "    line: str | None = None",
        "SpecProblem: field 'line' changed",
    ),
    "reordered dataclass fields": (
        "spec_edit",
        "    level: str\n    pointer: str\n",
        "    pointer: str\n    level: str\n",
        "SpecProblem: field 'level' changed",
    ),
    "dataclass no longer frozen": (
        "spec_edit",
        "@dataclass(frozen=True, slots=True)\nclass SpecProblem",
        "@dataclass(slots=True)\nclass SpecProblem",
        "SpecProblem: frozen changed True -> False",
    ),
    "dataclass field inserted before existing ones": (
        "spec_edit",
        "    line: int | None = None",
        "    hint: str = ''\n    line: int | None = None",
        "SpecProblem: field 'line' changed",
    ),
    "changed exception base": (
        "spec_edit",
        "class SpecError(ShapeSchemaError):",
        "class SpecError(ValueError):",
        "SpecError: base classes changed",
    ),
    "changed alias": (
        "spec_edit",
        "Position = tuple[int, int]",
        "Position = tuple[int, int, int]",
        "Position: changed",
    ),
    "name missing from the module": (
        "spec_schema",
        '"strategy_names"]',
        '"strategy_names", "gone"]',
        "gone: exported in __all__ but missing",
    ),
}


@pytest.mark.parametrize("case", sorted(BREAKS))
def test_each_break_fails_the_check_and_is_named(
    case: str, copy_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module, old, new, fragment = BREAKS[case]
    edit(copy_dir, module, old, new)
    code, out = run(copy_dir, capsys)
    assert code == 1, out
    assert "BREAKING:" in out and fragment in out, out
    mod = "shape.generation." + module
    assert mod in out


ADDITIONS = {
    "new exported name": (
        "spec_schema",
        '"strategy_names"]',
        '"strategy_names", "extra_helper"]\n\n\ndef extra_helper() -> int:\n    return 1\n',
        None,
    ),
    "new method": (
        "spec_edit",
        "    def table_names(self)",
        "    def extra(self) -> int:\n        return 1\n\n    @property\n    def table_names(self)",
        None,
    ),
    "new keyword parameter with default": (
        "spec_edit",
        "def remove_table(self, name: str)",
        "def remove_table(self, name: str, *, cascade: bool = False)",
        None,
    ),
    "new positional parameter with default at the end": (
        "spec_edit",
        "def remove_table(self, name: str)",
        "def remove_table(self, name: str, cascade: bool = False)",
        None,
    ),
    "new optional dataclass field at the end": (
        "spec_edit",
        "    column: int | None = None\n",
        "    column: int | None = None\n    hint: str = ''\n",
        None,
    ),
    "new property": (
        "spec_edit",
        "    def table_names(self)",
        "    def extra_prop(self) -> int:\n        return 1\n\n"
        "    @property\n    def table_names(self)",
        None,
    ),
}


@pytest.mark.parametrize("case", sorted(ADDITIONS))
def test_each_additive_change_passes(
    case: str, copy_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module, old, new, _ = ADDITIONS[case]
    edit(copy_dir, module, old, new)
    if case == "new method":  # a plain method in front of the property that follows it
        text = (copy_dir / "spec_edit.py").read_text(encoding="utf-8")
        assert "def extra(self)" in text
    code, out = run(copy_dir, capsys)
    assert code == 0, out
    assert "0 breaking" in out


def test_write_refuses_a_break_and_accepts_an_addition(
    copy_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    base.write_text(BASELINE.read_text(encoding="utf-8"), encoding="utf-8")
    edit(copy_dir, "spec_edit", "def validate_text(text: str)", "def validate_text(source: str)")
    before = base.read_text(encoding="utf-8")
    assert compat.main(["--write", "--baseline", str(base), "--source", str(copy_dir)]) == 1
    assert base.read_text(encoding="utf-8") == before
    assert "refusing to write" in capsys.readouterr().err
    shutil.copy(SRC / "spec_edit.py", copy_dir / "spec_edit.py")
    edit(
        copy_dir,
        "spec_edit",
        "def remove_table(self, name: str)",
        "def remove_table(self, name: str, *, cascade: bool = False)",
    )
    assert compat.main(["--write", "--baseline", str(base), "--source", str(copy_dir)]) == 0
    refreshed = json.loads(base.read_text(encoding="utf-8"))
    params = refreshed["modules"]["shape.generation.spec_edit"]["names"]["SpecDocument"]["members"][
        "remove_table"
    ]["params"]
    assert [p["name"] for p in params] == ["name", "cascade"]
    assert compat.main(["--check", "--baseline", str(base), "--source", str(copy_dir)]) == 0


def test_the_comparison_does_not_depend_on_dict_order() -> None:
    live = compat.snapshot()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    shuffled = copy.deepcopy(live)
    shuffled["modules"] = dict(reversed(list(shuffled["modules"].items())))
    assert compat.compare(baseline, shuffled) == []


def test_removing_a_module_is_a_break() -> None:
    live = compat.snapshot()
    del live["modules"]["shape.generation.spec_schema"]
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert any("spec_schema: module was removed" in p for p in compat.compare(baseline, live))


def test_the_plugin_compat_script_still_passes(capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location(
        "plugin_api_compat_chk", ROOT / "scripts" / "plugin_api_compat.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0
    assert re.fullmatch(r"plugin API 1\.0: 0 breaking change\(s\)\n", capsys.readouterr().out)
