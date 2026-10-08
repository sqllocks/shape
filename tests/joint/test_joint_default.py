"""When the joint analysis runs: on for a single table, off for a dataset, ``joint=`` /
``--joint`` / ``--no-joint`` to choose, ``SHAPE_PROFILE_JOINT`` when the call does not (#47)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main

pytestmark = pytest.mark.usefixtures("_no_env")


@pytest.fixture
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHAPE_PROFILE_JOINT", raising=False)


def _rows(n: int = 400) -> dict[str, list]:
    return {
        "id": list(range(n)),
        "city": [f"c{i % 8}" for i in range(n)],
        "state": [f"s{(i % 8) // 2}" for i in range(n)],  # city -> state
    }


def _has_joint(doc: dict) -> bool:
    if "tables" in doc:
        return {n: "joint" in t for n, t in doc["tables"].items()} == {
            n: True for n in doc["tables"]
        }
    return "joint" in doc


def _none_have_joint(doc: dict) -> bool:
    return all("joint" not in t for t in doc["tables"].values())


def _single(**kw: object) -> dict:
    return shape.profile(pa.table(_rows()), **kw).to_dict()  # type: ignore[arg-type]


def _dataset(**kw: object) -> dict:
    return shape.profile({"a": pa.table(_rows()), "b": pa.table(_rows(300))}, **kw).to_dict()  # type: ignore[arg-type]


def test_single_table_default_is_on() -> None:
    assert "joint" in _single()


def test_single_table_joint_false_is_off() -> None:
    assert "joint" not in _single(joint=False)


def test_single_table_joint_true_is_on() -> None:
    assert "joint" in _single(joint=True)


def test_dataset_default_is_off() -> None:
    assert _none_have_joint(_dataset())


def test_dataset_joint_true_is_on() -> None:
    assert _has_joint(_dataset(joint=True))


def test_dataset_joint_false_is_off() -> None:
    assert _none_have_joint(_dataset(joint=False))


def test_a_one_table_dict_is_a_dataset() -> None:
    doc = shape.profile({"a": pa.table(_rows())}).to_dict()
    assert _none_have_joint(doc)


@pytest.mark.parametrize("value", ["0", "false", "no"])
def test_env_off_switches_a_single_table_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", value)
    assert "joint" not in _single()


def test_env_on_switches_a_dataset_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "1")
    assert _has_joint(_dataset())


def test_the_argument_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "0")
    assert "joint" in _single(joint=True)
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "1")
    assert "joint" not in _single(joint=False)
    assert _none_have_joint(_dataset(joint=False))


def test_an_empty_environment_value_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "")
    assert "joint" in _single()
    assert _none_have_joint(_dataset())


def test_a_dataset_without_the_joint_entry_differs_only_by_it() -> None:
    off, on = _dataset(), _dataset(joint=True)
    for name, table in on["tables"].items():
        assert {k: v for k, v in table.items() if k != "joint"} == off["tables"][name]


# --- the CLI ------------------------------------------------------------------------------


def _csv(path: Path, n: int = 400) -> Path:
    rows = _rows(n)
    path.write_text(
        "id,city,state\n"
        + "\n".join(f"{i},{rows['city'][i]},{rows['state'][i]}" for i in range(n))
        + "\n"
    )
    return path


def _cli(tmp_path: Path, *args: str) -> dict:
    out = tmp_path / "out.shape"
    assert main(["profile", *args, "-o", str(out)]) == 0
    return shape.load(str(out)).to_dict()


def test_cli_single_table(tmp_path: Path) -> None:
    src = str(_csv(tmp_path / "a.csv"))
    assert "joint" in _cli(tmp_path, src)
    assert "joint" in _cli(tmp_path, src, "--joint")
    assert "joint" not in _cli(tmp_path, src, "--no-joint")


def test_cli_dataset(tmp_path: Path) -> None:
    folder = tmp_path / "ds"
    folder.mkdir()
    _csv(folder / "a.csv")
    _csv(folder / "b.csv", 300)
    assert _none_have_joint(_cli(tmp_path, str(folder), "--dataset"))
    assert _has_joint(_cli(tmp_path, str(folder), "--dataset", "--joint"))
    assert _none_have_joint(_cli(tmp_path, str(folder), "--dataset", "--no-joint"))


def test_cli_dataset_env_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "ds"
    folder.mkdir()
    _csv(folder / "a.csv")
    _csv(folder / "b.csv", 300)
    monkeypatch.setenv("SHAPE_PROFILE_JOINT", "1")
    assert _has_joint(_cli(tmp_path, str(folder), "--dataset"))
    assert _none_have_joint(_cli(tmp_path, str(folder), "--dataset", "--no-joint"))
