"""W1-17: writing to a non-local target needs ``--yes`` or ``SHAPE_CONFIRM_REMOTE=1``."""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fsspec
import pytest

from shape.cli.main import main
from shape.errors import ShapeError
from shape.io.targets import (
    confirm_remote_targets,
    is_local_destination,
    nonlocal_destinations,
    scale_sink_destinations,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
from scale_schemas import plain_doc  # noqa: E402

PG = "postgresql://db.example/shape"
ABFSS = "abfss://landing@acct.dfs.core.windows.net/raw"
REFUSAL = (
    "shape: error: refusing to write to non-local target postgresql://db.example/shape "
    "without confirmation; pass --yes or set SHAPE_CONFIRM_REMOTE=1"
)


def run(capsys: Any, *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHAPE_CONFIRM_REMOTE", raising=False)


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc()))
    return path


@pytest.fixture
def sockets(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every attempt to resolve or connect is recorded (and refused)."""
    calls: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError(f"network use before confirmation: {args}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return calls


class FakeTerminal(io.StringIO):
    def __init__(self, text: str = "", tty: bool = True) -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# ---- what is local --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "out",
        "/tmp/out/data",
        "./rel/dir",
        "C:\\data\\out",
        "file:///tmp/out",
        "jsonl:///tmp/out",
        "console",
        "file",
        "http://localhost:10000/devstore",
        "abfss://c@127.0.0.1:10000/raw",
        "postgresql://u:pw@[::1]:5432/db",
        "kafka://LOCALHOST:9092/topic",
        "mssql://127.0.0.1/db",
    ],
)
def test_local_destinations(target: str) -> None:
    assert is_local_destination(target)
    assert nonlocal_destinations([target]) == []


@pytest.mark.parametrize(
    "target",
    [
        PG,
        ABFSS,
        "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables",
        "eventhubs://ns/hub",
        "kafka://broker.example:9092/topic",
        "mssql://sql.example/db",
        "postgresql://localhost.evil.example/db",
        "postgresql://127.0.0.1.evil.example/db",
        "postgresql://u:pw@db.example/shape",
    ],
)
def test_nonlocal_destinations(target: str) -> None:
    assert not is_local_destination(target)
    assert nonlocal_destinations([target]) == [target]


def test_nonlocal_destinations_keep_order_and_drop_repeats_and_locals() -> None:
    assert nonlocal_destinations([PG, "out", ABFSS, PG, "console"]) == [PG, ABFSS]


def test_scale_sinks() -> None:
    assert scale_sink_destinations(["memory", "parquet"]) == []
    assert scale_sink_destinations(["warehouse", "sql_database", "kql"]) == [
        "warehouse://",
        "sql-database://",
        "kql://",
    ]
    assert scale_sink_destinations(["lakehouse"], {"lakehouse": {"base_path": "/tmp/lh"}}) == []
    assert scale_sink_destinations(["lakehouse"], {"lakehouse": {"base_path": ABFSS}}) == [ABFSS]
    assert scale_sink_destinations(["lakehouse"]) == ["lakehouse://"]


# ---- the confirmation paths ---------------------------------------------------------------


def test_no_nonlocal_target_needs_no_confirmation() -> None:
    assert confirm_remote_targets(["out", "console"], stdin=FakeTerminal(tty=False)) is True


def test_confirm_argument_confirms() -> None:
    assert confirm_remote_targets([PG], confirm=True) is True


def test_env_1_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")
    assert confirm_remote_targets([PG], stdin=FakeTerminal(tty=False)) is True


@pytest.mark.parametrize("value", ["0", "", "true", "yes", "2", " 1", "1 "])
def test_any_other_env_value_does_not_confirm(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", value)
    with pytest.raises(ShapeError, match="refusing to write to non-local target"):
        confirm_remote_targets([PG], stdin=FakeTerminal(tty=False), stderr=FakeTerminal())


def test_dry_run_needs_no_confirmation() -> None:
    assert confirm_remote_targets([PG], dry_run=True, stdin=FakeTerminal(tty=False)) is True


def test_no_terminal_refuses_with_the_documented_message() -> None:
    with pytest.raises(ShapeError) as caught:
        confirm_remote_targets([PG], stdin=FakeTerminal(tty=False), stderr=FakeTerminal(tty=False))
    assert str(caught.value) == REFUSAL.removeprefix("shape: error: ")


def test_a_terminal_on_one_side_only_is_no_terminal() -> None:
    err = FakeTerminal(tty=False)
    with pytest.raises(ShapeError):
        confirm_remote_targets([PG], stdin=FakeTerminal("y\n"), stderr=err)
    assert err.getvalue() == ""  # no prompt was written
    with pytest.raises(ShapeError):
        confirm_remote_targets([PG], stdin=FakeTerminal("y\n", tty=False), stderr=FakeTerminal())


def test_interactive_y_confirms_and_the_prompt_is_redacted() -> None:
    err = FakeTerminal()
    ok = confirm_remote_targets(
        ["postgresql://u:secret@db.example/shape", ABFSS], stdin=FakeTerminal("y\n"), stderr=err
    )
    assert ok is True
    assert err.getvalue() == (
        f"Write to 2 non-local targets: postgresql://u:***@db.example/shape, {ABFSS}? [y/N] "
    )
    assert "secret" not in err.getvalue()


@pytest.mark.parametrize("answer", ["n\n", "\n", "", "no\n", "maybe\n"])
def test_interactive_anything_but_y_refuses(answer: str) -> None:
    with pytest.raises(ShapeError, match="without confirmation"):
        confirm_remote_targets([PG], stdin=FakeTerminal(answer), stderr=FakeTerminal())


def test_the_refusal_redacts_a_password_and_names_every_target() -> None:
    with pytest.raises(ShapeError) as caught:
        confirm_remote_targets(
            ["postgresql://u:secret@db.example/shape", ABFSS],
            stdin=FakeTerminal(tty=False),
            stderr=FakeTerminal(tty=False),
        )
    text = str(caught.value)
    assert "secret" not in text and "non-local targets" in text and ABFSS in text


# ---- the commands -------------------------------------------------------------------------


@pytest.fixture
def memfs(monkeypatch: pytest.MonkeyPatch) -> Any:
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    from shape.builtins.sources import azure

    monkeypatch.setattr(azure, "_filesystem", lambda loc, options: fs)
    return fs


def test_generate_to_refuses_without_confirmation_and_opens_no_socket(
    capsys: Any, schema_file: Path, sockets: list[Any]
) -> None:
    code, out, err = run(capsys, "generate", schema_file, "--to", PG)
    assert code == 2 and REFUSAL in err and out == ""
    assert sockets == []


def test_generate_to_refuses_before_signing_in(
    capsys: Any, schema_file: Path, sockets: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from shape.cli import auth

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("signed in before confirmation")

    monkeypatch.setattr(auth, "make_credential", boom)
    code, _, err = run(capsys, "generate", schema_file, "--to", ABFSS, "--auth", "cli")
    assert code == 2 and "refusing to write to non-local target abfss://" in err


def test_generate_to_with_yes_goes_ahead(capsys: Any, memfs: Any, schema_file: Path) -> None:
    code, out, err = run(capsys, "generate", schema_file, "--to", ABFSS, "--yes")
    assert code == 0, err
    assert memfs.find("landing/raw")


def test_generate_to_with_env_1_goes_ahead(
    capsys: Any, memfs: Any, schema_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")
    code, _, err = run(capsys, "generate", schema_file, "--to", ABFSS)
    assert code == 0, err and memfs.find("landing/raw")


def test_generate_to_with_env_0_refuses(
    capsys: Any, memfs: Any, schema_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "0")
    code, _, err = run(capsys, "generate", schema_file, "--to", ABFSS)
    assert code == 2 and "without confirmation" in err
    assert memfs.find("landing/raw") == []


def test_generate_to_one_nonlocal_among_several_refuses_them_all(
    capsys: Any, memfs: Any, schema_file: Path, tmp_path: Path
) -> None:
    code, _, err = run(capsys, "generate", schema_file, "--to", ABFSS, "--to", PG)
    assert code == 2 and "non-local targets" in err and ABFSS in err and PG in err
    assert memfs.find("landing/raw") == []


def test_generate_to_dry_run_needs_no_confirmation(
    capsys: Any, schema_file: Path, sockets: list[Any]
) -> None:
    code, _, err = run(capsys, "generate", schema_file, "--to", PG, "--dry-run")
    assert code == 0, err
    assert sockets == []


def test_generate_to_a_loopback_target_needs_no_confirmation(
    capsys: Any, schema_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[list[str]] = []

    def fake(engine: Any, targets: list[str], options: Any, chunk_rows: Any = None) -> dict:
        called.append(targets)
        return {t: {"customer": 1} for t in targets}

    monkeypatch.setattr("shape.generation.output.write_targets", fake)
    code, _, err = run(capsys, "generate", schema_file, "--to", "postgresql://127.0.0.1/db")
    assert code == 0, err
    assert called == [["postgresql://127.0.0.1/db"]]


def test_a_plain_output_directory_never_asks(
    capsys: Any, schema_file: Path, tmp_path: Path, sockets: list[Any]
) -> None:
    code, _, err = run(capsys, "generate", schema_file, "-f", "csv", "-o", tmp_path / "o")
    assert code == 0, err


def test_run_to_python_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    from shape.cli.to import run_to

    seen: list[Any] = []
    monkeypatch.setattr("shape.cli.to.target_options", lambda *a, **k: object())
    monkeypatch.setattr(
        "shape.generation.output.write_targets",
        lambda engine, targets, options, chunk_rows=None: (
            seen.append(targets) or {t: {"t": 1} for t in targets}
        ),
    )
    a = argparse.Namespace(output=None, to=[PG], format="parquet", chunk_rows=None, json=True)
    engine = SimpleNamespace(seed=1)
    with pytest.raises(ShapeError, match="refusing to write to non-local target"):
        run_to(a, engine, 0.0)
    assert seen == []
    assert run_to(a, engine, 0.0, confirm_remote=True) == 0
    assert seen == [[PG]]
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")
    assert run_to(a, engine, 0.0) == 0


def test_emit_target_setup_python_entry_point(sockets: list[Any]) -> None:
    from shape.cli.emit import _sink

    a = argparse.Namespace(sink="kafka://broker.example:9092/t", to=[], yes=False, dry_run=False)
    with pytest.raises(ShapeError, match="refusing to write to non-local target kafka://"):
        _sink(a, "plain", resuming=False)
    assert sockets == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--sink", "kafka://broker.example:9092/orders"],
        ["--sink", "eventhubs://ns/hub"],
        ["--to", PG],
    ],
)
def test_emit_refuses_a_nonlocal_sink(
    capsys: Any, schema_file: Path, sockets: list[Any], extra: list[str]
) -> None:
    code, out, err = run(capsys, "emit", schema_file, "--max-events", "5", *extra)
    assert code == 2 and "refusing to write to non-local target" in err
    assert "pass --yes or set SHAPE_CONFIRM_REMOTE=1" in err and out == ""
    assert sockets == []


def test_stream_refuses_a_nonlocal_sink(capsys: Any, schema_file: Path, sockets: list[Any]) -> None:
    code, _, err = run(
        capsys, "stream", schema_file, "-t", "order", "--max-events", "5",
        "--sink", "eventhubs://ns/hub",
    )  # fmt: skip
    assert code == 2 and "refusing to write to non-local target eventhubs://ns/hub" in err
    assert sockets == []


def test_emit_to_console_and_file_never_ask(
    capsys: Any, schema_file: Path, tmp_path: Path, sockets: list[Any]
) -> None:
    code, out, err = run(capsys, "emit", schema_file, "--max-events", "3")
    assert code == 0, err
    code, _, err = run(
        capsys, "emit", schema_file, "--max-events", "3", "--sink", "file", "-o", tmp_path / "e.jsonl"
    )  # fmt: skip
    assert code == 0, err


def test_emit_with_yes_gets_past_the_confirmation(capsys: Any, schema_file: Path) -> None:
    code, _, err = run(
        capsys, "emit", schema_file, "--max-events", "5", "--sink", "kafka://broker.example:9092/o",
        "--yes",
    )  # fmt: skip
    # No Kafka plugin or broker here: the command gets past the confirmation and fails later.
    assert "refusing to write to non-local" not in err


@pytest.mark.parametrize(
    "extra",
    [["--sink", "warehouse"], ["--sink", "sql_database"], ["--sink", "kql"]],
)
def test_scale_refuses_a_nonlocal_sink(
    capsys: Any, schema_file: Path, sockets: list[Any], extra: list[str]
) -> None:
    code, _, err = run(capsys, "generate", schema_file, "--scale-mode", "local_single", *extra)
    assert code == 2 and "refusing to write to non-local target" in err
    assert "pass --yes or set SHAPE_CONFIRM_REMOTE=1" in err
    assert sockets == []


def test_scale_refuses_a_remote_lakehouse_base_path(
    capsys: Any, schema_file: Path, sockets: list[Any]
) -> None:
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_single", "--sink", "lakehouse",
        "--sink-config", f"lakehouse.base_path={ABFSS}",
    )  # fmt: skip
    assert code == 2 and f"non-local target {ABFSS}" in err


def test_scale_local_sinks_never_ask(capsys: Any, schema_file: Path, tmp_path: Path) -> None:
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_single", "--sink", "memory"
    )
    assert code == 0, err
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_single", "--sink", "lakehouse",
        "--sink-config", f"lakehouse.base_path={tmp_path / 'lh'}",
    )  # fmt: skip
    assert "refusing to write" not in err  # (the lakehouse sink itself needs the fabric plugin)


def test_scale_dry_run_needs_no_confirmation(capsys: Any, schema_file: Path) -> None:
    code, _, err = run(
        capsys, "generate", schema_file, "--scale-mode", "local_single", "--sink", "warehouse",
        "--dry-run",
    )  # fmt: skip
    assert "refusing to write" not in err
