"""``--event-format`` (W2-09 item 1): what the core command line does without a plugin.

The registry formats themselves are tested in plugins/shape-kafka/tests/test_registry_formats.py.
"""

from __future__ import annotations

import json

import pytest

from shape.cli.main import main

BASE = ["emit", "retail", "--table", "customer", "--max-events", "5"]


def test_json_is_the_default_and_the_bytes_do_not_change(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(BASE) == 0
    default = capsys.readouterr().out
    assert main([*BASE, "--event-format", "json"]) == 0
    assert capsys.readouterr().out == default
    assert json.loads(default.splitlines()[0])["_shape_table"] == "customer"


@pytest.mark.parametrize("fmt", ["avro", "protobuf", "json-schema"])
@pytest.mark.parametrize(
    "sink",
    [["--sink", "console"], ["--sink", "file", "-o", "x.jsonl"], ["--to", "file:///x.jsonl"]],
)
def test_a_registry_format_is_refused_for_targets_that_do_not_take_it(
    fmt: str, sink: list[str], tmp_path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main([*BASE, "--event-format", fmt, *sink]) == 2
    err = capsys.readouterr().err
    assert f"--event-format {fmt} applies to kafka:// targets only" in err
    assert not (tmp_path / "x.jsonl").exists()


def test_cloudevents_with_a_registry_format_is_refused_with_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [*BASE, "--event-format", "avro", "--envelope", "cloudevents", "--sink", "kafka://b:9092/t"]
    )
    assert code == 2
    assert "--envelope cloudevents is JSON only" in capsys.readouterr().err
    # cloudevents with json stays fine
    assert main([*BASE, "--envelope", "cloudevents"]) == 0


def test_an_unknown_format_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main([*BASE, "--event-format", "xml"])
    assert exc.value.code == 2


def test_a_table_sink_refuses_a_registry_format(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")  # W1-17: the target is not on this machine
    uri = "abfss://landing@acct.dfs.core.windows.net/stream"
    code = main([*BASE, "--event-format", "avro", "--to", uri])
    assert code == 2
    assert "applies to kafka:// targets only" in capsys.readouterr().err
