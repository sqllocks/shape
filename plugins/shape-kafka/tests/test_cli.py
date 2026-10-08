"""``shape stream-profile`` through the kafka:// plugin and the in-memory broker (contract
tests, every PR). Gate G3's equivalence, on the real path: broker, decoder, consumer, bounded
profiler, written document."""

import importlib.util
import json
import sys
from pathlib import Path

import pyarrow as pa
import pytest
from shape_kafka.testing import FakeBroker

from shape.cli.main import main
from shape.plugins.host import PluginHost
from shape.profile.engine import EngineOptions, profile_table
from shape.streaming.messages import decode_messages

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
URI = "kafka://broker:9092/d2"


def d2(n):
    (path,) = (ROOT / "benchmarks").glob("*/profile_1to1/datasets.py")  # the D2 generator
    spec = importlib.util.spec_from_file_location("d2_datasets", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["d2_datasets"] = mod
    spec.loader.exec_module(mod)
    return mod._d2_table(n)


def messages(table, partitions):
    out = {p: [] for p in range(partitions)}
    for i, row in enumerate(table.to_pylist()):
        out[i % partitions].append((json.dumps(row, default=str).encode(), 1_700_000_000_000 + i))
    return out


@pytest.fixture
def cli(monkeypatch, capsys):
    def go(broker, *argv):
        host = PluginHost(entry_points=lambda: [])
        host.register("shape.stream_sources", "kafka", broker.source())
        monkeypatch.setattr("shape.plugins.host.default_host", lambda: host)
        code = main(["stream-profile", URI, *map(str, argv)])
        out, err = capsys.readouterr()
        return code, out, err

    return go


@pytest.mark.parametrize("partitions", [1, 3])
def test_a_stream_replay_of_d2_equals_batch_bounded_profiling(cli, tmp_path, partitions):
    table = d2(20_000)
    broker = FakeBroker({"d2": messages(table, partitions)}, chunk=4096)
    out = tmp_path / "p.json"
    code, stdout, _ = cli(broker, "-o", out, "--batch-size", 4096, "--name", "d2")
    assert code == 0
    assert json.loads(stdout.strip().splitlines()[-1])["events"] == 20_000
    got = json.loads(out.read_text())["tables"]["d2"]

    # The same messages decoded in the same order (partitions one after the other) and profiled
    # as one table in bounded mode.
    from shape.streaming.messages import StreamMessage

    flat = []
    for p in range(partitions):
        flat += [
            StreamMessage(str(p), i, body, ts * 1000)
            for i, (body, ts) in enumerate(broker.topics["d2"][p])
        ]
    decoded = decode_messages(flat)
    want = profile_table(pa.Table.from_batches([decoded]), "d2", EngineOptions(mode="bounded"))
    assert got["rows"] == want["rows"] == 20_000
    assert [c["name"] for c in got["columns"]] == [c["name"] for c in want["columns"]]
    for g, w in zip(got["columns"], want["columns"], strict=True):
        assert g["error_models"] == w["error_models"], w["name"]
        assert g["count"] == w["count"] and g["null_count"] == w["null_count"], w["name"]
        if "top" in w:
            assert g["top"] == w["top"], w["name"]
        if "distinct" in w:
            assert g["distinct"] == pytest.approx(w["distinct"], rel=1e-12), w["name"]


def test_100_forced_reconnects_and_a_kill_leave_the_profile_equal_to_an_uninterrupted_run(
    cli, tmp_path
):
    table = d2(8_000)
    clean_out = tmp_path / "clean.json"
    code, _, _ = cli(FakeBroker({"d2": messages(table, 1)}, chunk=64), "-o", clean_out,
                     "--batch-size", 64)  # fmt: skip
    assert code == 0
    flaky_out = tmp_path / "flaky.json"
    flaky = FakeBroker({"d2": messages(table, 1)}, chunk=64, fail_at=64, fail_every=64)
    code, stdout, _ = cli(flaky, "-o", flaky_out, "--batch-size", 64, "--checkpoint",
                          tmp_path / "ck.json", "--checkpoint-every", 3)  # fmt: skip
    assert code == 0
    assert json.loads(stdout.strip().splitlines()[-1])["reconnects"] >= 100
    assert json.loads(flaky_out.read_text()) == json.loads(clean_out.read_text())


def test_a_missing_topic_is_an_input_error(cli, tmp_path):
    code, _, err = cli(FakeBroker({"other": {0: []}}), "-o", tmp_path / "p.json")
    assert code == 2 and "does not exist" in err
