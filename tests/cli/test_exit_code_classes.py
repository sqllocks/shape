"""W1-10: one representative command per exit-code class of ``docs/CLI.md``.

0 ok; 1 a check failed; 2 bad input; 3 and above a command's own verdict (here ``shape
compatibility``: 5 for an incompatible change). The classes are what ``docs/CLI_STABILITY.md``
promises, so a change to one fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def profile(tmp_path: Path) -> Path:
    src = tmp_path / "orders.csv"
    src.write_text("id,status\n" + "\n".join(f"{i},{'ab'[i % 2]}" for i in range(50)) + "\n")
    out = tmp_path / "orders.shape"
    assert main(["profile", str(src), "-o", str(out)]) == 0
    return out


def _contract(tmp_path: Path, min_rows: int) -> Path:
    path = tmp_path / f"contract-{min_rows}.json"
    path.write_text(json.dumps({"row_count": {"min": min_rows}}))
    return path


def test_class_0_ok(profile: Path, tmp_path: Path) -> None:
    assert main(["check", str(profile), str(_contract(tmp_path, 10))]) == 0


def test_class_1_a_check_failed(profile: Path, tmp_path: Path) -> None:
    assert main(["check", str(profile), str(_contract(tmp_path, 10**6))]) == 1


def test_class_2_bad_input(
    profile: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(tmp_path / "missing.shape"), str(_contract(tmp_path, 1))]) == 2
    assert "shape: error:" in capsys.readouterr().err


def test_class_3_and_above_is_the_commands_own_verdict(tmp_path: Path) -> None:
    models = []
    for name, table in (
        ("base", pa.table({"id": [1, 2, 3], "x": ["a", "b", "c"]})),
        ("today", pa.table({"id": [1, 2, 3]})),  # a dropped column
    ):
        src = tmp_path / f"{name}.parquet"
        pq.write_table(table, src)
        out = tmp_path / f"{name}.shape"
        assert main(["capture", str(src), "-o", str(out)]) == 0
        models.append(str(out))
    assert main(["compatibility", *models]) == 5
    assert main(["compatibility", models[0], models[0]]) == 0


def test_a_class_is_never_reused_for_another_meaning(profile: Path, tmp_path: Path) -> None:
    """A failed check (1) and an unreadable input (2) stay apart for the same command."""
    codes = {
        main(["check", str(profile), str(_contract(tmp_path, 10**6))]),
        main(["check", str(tmp_path / "nope.shape"), str(_contract(tmp_path, 1))]),
    }
    assert codes == {1, 2}


def test_one_zero_contract_lists_the_same_classes_as_the_cli_docs() -> None:
    contract = (ROOT / "docs" / "specs" / "ONE_ZERO_CONTRACT.md").read_text("utf-8")
    cli = (ROOT / "docs" / "CLI.md").read_text("utf-8")
    assert "64 for" not in contract.split("Exit codes", 1)[1].split("An earlier draft")[0]
    for needle in ("0 ok", "1 a check failed", "2 bad input", "3 and above"):
        assert needle in contract.replace("`", "") or needle in cli.replace("`", "")
    assert "docs/CLI.md" in contract
