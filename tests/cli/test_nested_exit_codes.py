"""Commands that read a profile with a nested column work or refuse cleanly (GitHub issue #1).

Each command below is run on the real program (exit code, standard error): it exits 0 (or 1 for a
failed check or drift), or it exits 2 with one ``shape: error:`` line. Never a traceback, never an
abort (134, 139) and never a hang.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import nested_inputs as ni
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

NAMES = tuple(ni.NESTED_TYPES)


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def clean(done: subprocess.CompletedProcess[str], ok: tuple[int, ...] = (0,)) -> None:
    """The command ended by itself with an expected code and no traceback."""
    assert done.returncode in ok, f"exit {done.returncode}: {done.stderr[-400:]}"
    assert "Traceback" not in done.stderr and "terminate called" not in done.stderr
    if done.returncode == 2:
        errors = [x for x in done.stderr.splitlines() if x.startswith("shape: error:")]
        assert len(errors) == 1, done.stderr


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("nested_cli")
    table = ni.table_with(*NAMES)
    ni.write_parquet(d / "t.parquet", table)
    # the same table with more nulls in a nested column, a longer list and a changed flat column
    more = ni.nested_array("li").to_pylist()
    changed = table.set_column(
        table.schema.get_field_index("li"),
        "li",
        pa.array([None if i % 2 == 0 else v for i, v in enumerate(more)], pa.list_(pa.int64())),
    )
    pq.write_table(changed, str(d / "u.parquet"))
    for stem in ("t", "u"):
        clean(run(d, "profile", f"{stem}.parquet", "-o", f"{stem}.shape"))
    contract = {
        "columns": {
            "li": {"dtype": "nested", "max_null_rate": 0.5},
            "st": {"nullable": True},
            "id": {"dtype": "integer"},
        }
    }
    (d / "ok.json").write_text(json.dumps(contract))
    strict = {"columns": {"li": {"max_null_rate": 0.01}}}
    (d / "strict.json").write_text(json.dumps(strict))
    return d


def test_inspect_and_show_print_the_nested_columns(work: Path) -> None:
    for command in ("inspect", "show"):
        done = run(work, command, "t.shape")
        clean(done)
        profile = json.loads(done.stdout)
        text = json.dumps(profile)
        assert str(pq.read_schema(str(work / "t.parquet")).field("li").type) in text
        assert "map<string, int64" in text


def test_inspect_pretty(work: Path) -> None:
    clean(run(work, "inspect", "--pretty", "t.shape"))


def test_check_passes_and_fails_on_a_nested_column(work: Path) -> None:
    clean(run(work, "check", "t.shape", "ok.json"))
    done = run(work, "check", "t.shape", "strict.json")
    clean(done, ok=(1,))  # li has 20% nulls: the contract allows 1%


def test_check_with_data_beside_a_nested_column(work: Path) -> None:
    clean(run(work, "check", "t.shape", "ok.json", "--data", "t.parquet"), ok=(0, 1, 2))


def test_diff_of_a_profile_with_itself_and_with_a_changed_one(work: Path) -> None:
    clean(run(work, "diff", "t.shape", "t.shape", "--fail-on-drift"))
    done = run(work, "diff", "t.shape", "u.shape", "--fail-on-drift", "--json", "d.json")
    clean(done, ok=(1,))
    changes = json.loads((work / "d.json").read_text())["changes"]
    assert any(c["column"] == "li" and c["kind"] == "null_rate_change" for c in changes)


def test_verify_a_data_file_with_nested_columns(work: Path) -> None:
    clean(run(work, "verify", "t.parquet"), ok=(0, 1))


def test_verify_a_profile_artifact(work: Path) -> None:
    clean(run(work, "verify", "t.shape"), ok=(2,))  # needs --key: a clean refusal
    clean(run(work, "keygen", "k", "--no-passphrase"))
    clean(run(work, "sign", "t.shape", "--key", "k.key", "-o", "s.shape"))
    clean(run(work, "verify", "s.shape", "--key", "k.pub"))
    clean(run(work, "inspect", "s.shape", "--verify", "k.pub"))


def test_generate_from_a_nested_profile(work: Path) -> None:
    done = run(
        work, "generate", "--from", "t.shape", "--rows", "40", "-o", "gen", "--format", "parquet"
    )
    clean(done)
    out = pq.read_table(str(work / "gen" / "t.parquet"))
    assert out.num_rows == 40
    for name in NAMES:
        assert out.schema.field(name).type.equals(
            pq.read_schema(str(work / "t.parquet")).field(name).type
        )


@pytest.mark.parametrize(
    "args",
    [
        ("plan", "t.shape"),
        ("cat", "t.shape"),
        ("types", "t.shape"),
        ("skew", "t.shape", "t.shape"),
        ("learn", "t.parquet"),
        ("mask", "t.parquet", "-o", "masked"),
        ("capture", "t.parquet"),
        ("registry", "reg", "commit", "t", "t.shape", "--safe"),
        ("profile", "t.parquet", "-o", "again.shape"),
    ],
    ids=lambda a: a[0],
)
def test_other_commands_that_read_the_profile_or_the_data(
    work: Path, args: tuple[str, ...]
) -> None:
    clean(run(work, *args), ok=(0, 1, 2))


def test_profile_of_the_issue_repro_and_jsonl_and_delta(tmp_path: Path) -> None:
    ni.write_parquet(tmp_path / "a.parquet", pa.table({"id": [1, 2], "li": [[1], [2]]}))
    ni.write_jsonl(tmp_path / "n.jsonl", ni.table_with(*ni.JSONL_NESTED))
    ni.write_delta(tmp_path / "d", ni.table_with("st"))
    for src in ("a.parquet", "n.jsonl", "d"):
        clean(run(tmp_path, "profile", src, "-o", f"{src}.shape"))
        assert (tmp_path / f"{src}.shape").exists()
