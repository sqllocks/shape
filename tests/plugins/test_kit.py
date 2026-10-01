"""P2-06: the plugin conformance kit (``shape.plugins.kit``).

Three things are tested: core's own built-ins pass the kit (it is not stricter than what ships),
a deliberately wrong plugin per Protocol fails it with a message that names the rule, and the
kit works on an installed distribution (see ``test_plugin_kit_install.py``).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import date

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.plugins import kit
from shape.plugins.api import v1
from shape.plugins.host import default_host

TABLE = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})


# -- the built-ins pass ---------------------------------------------------------------------


@pytest.fixture
def files(tmp_path):
    out = {
        "csv": tmp_path / "t.csv",
        "parquet": tmp_path / "t.parquet",
        "jsonl": tmp_path / "t.jsonl",
        "ipc": tmp_path / "t.arrow",
    }
    pacsv.write_csv(TABLE, out["csv"])
    pq.write_table(TABLE, out["parquet"])
    out["jsonl"].write_text('{"id": 1, "name": "a"}\n{"id": 2, "name": "b"}\n', encoding="utf-8")
    with pa.OSFile(str(out["ipc"]), "wb") as sink, pa.ipc.new_file(sink, TABLE.schema) as w:
        w.write_table(TABLE)
    return out


@pytest.mark.parametrize("name", ["csv", "parquet", "jsonl", "ipc"])
def test_builtin_sources_conform(name, files):
    kit.check_source(default_host().get("shape.sources", name), str(files[name]))


@pytest.mark.parametrize("name", ["csv", "parquet", "jsonl", "ipc"])
def test_builtin_sinks_conform(name, tmp_path):
    sink = default_host().get("shape.sinks", name)
    batches = TABLE.to_batches()
    kit.check_sink(sink, str(tmp_path / f"out.{name}"), batches)


DETECTOR_SAMPLES = {
    "email": (["ann@example.com", "bo@example.org"], ["hello", "world"]),
    "us_ssn": (["123-45-6789", "234-56-7890"], ["hello", "world"]),
    "phone": (["+1 415 555 0100", "+44 20 7946 0958"], ["hello", "world"]),
    "ipv4": (["10.0.0.1", "192.168.1.20"], ["hello", "world"]),
}


@pytest.mark.parametrize("name", sorted(DETECTOR_SAMPLES))
def test_builtin_detectors_conform(name):
    pos, neg = DETECTOR_SAMPLES[name]
    kit.check_detector(
        default_host().get("shape.detectors", name), positives=[pos], negatives=[neg]
    )


def test_builtin_fitter_conforms():
    sample = [float(i % 17) + 0.5 for i in range(500)]
    kit.check_fitter(default_host().get("shape.fitters", "auto"), sample)


STRATEGY_SPECS = {
    "constant": {"value": 5},
    "sequence": {"start": 10, "step": 2},
    "choice": {"values": ["x", "y", "z"], "weights": [1, 2, 3]},
    "uniform": {"low": 0, "high": 10},
    "normal": {"mean": 0, "stddev": 1},
}


@pytest.mark.parametrize("name", sorted(STRATEGY_SPECS))
def test_builtin_strategies_conform(name):
    kit.check_strategy(default_host().get("shape.strategies", name), STRATEGY_SPECS[name])


def test_builtin_sequence_is_layout_independent():
    kit.check_strategy(
        default_host().get("shape.strategies", "sequence"),
        STRATEGY_SPECS["sequence"],
        layout_independent=True,
    )


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("normal", {"loc": 0.0, "scale": 2.0}),
        ("uniform", {"loc": 1.0, "scale": 3.0}),
        ("exponential", {"loc": 0.0, "scale": 1.5}),
        ("lognormal", {"loc": 0.0, "scale": 1.0, "s": 0.5}),
    ],
)
def test_builtin_distributions_conform(name, params):
    kit.check_distribution(default_host().get("shape.distributions", name), params)


@pytest.mark.parametrize("name", ["us_federal", "us_retail"])
def test_builtin_calendars_conform(name):
    kit.check_calendar(default_host().get("shape.calendars", name))


def test_check_plugin_dispatches_by_group(files):
    host = default_host()
    kit.check_plugin("shape.sources", host.get("shape.sources", "csv"), uri=str(files["csv"]))
    kit.check_plugin("shape.calendars", host.get("shape.calendars", "us_federal"))
    kit.check_plugin("shape.detectors", host.get("shape.detectors", "email"))  # shared rules only
    with pytest.raises(kit.ConformanceError, match="unknown plugin group"):
        kit.check_plugin("shape.nope", object())


# -- a wrong plugin per Protocol fails, naming the rule -------------------------------------


class _Named:
    name = "good"


def _fail(check, obj, match, **sample):
    with pytest.raises(kit.ConformanceError, match=match):
        check(obj, **sample)


def test_common_rules():
    _fail(kit.check_detector, object(), "does not implement SemanticDetector", positives=[["a"]])

    class Bad:
        name = "Bad Name"

        def detect(self, values, column):
            return None

    _fail(kit.check_detector, Bad(), "`name`", positives=[["a"]])

    class NoName:
        def detect(self, values, column):
            return None

    _fail(kit.check_detector, NoName(), "does not implement", positives=[["a"]])


class _Src:
    name = "src"
    schemes = ("src",)
    uri_ok = "src://x"

    def can_open(self, uri):
        return True  # claims everything

    def schema(self, uri, **o):
        return pa.schema([("a", pa.int64())])

    def read(self, uri, **o) -> Iterator[pa.RecordBatch]:
        yield pa.RecordBatch.from_pydict({"a": [1]}, schema=self.schema(uri))


def test_source_rules():
    kit.check_source(
        type("Ok", (_Src,), {"can_open": lambda s, u: u.startswith("src://")})(), "src://x"
    )
    _fail(kit.check_source, _Src(), "must be False for a URI", uri="src://x")

    class NoScheme(_Src):
        schemes = ()

    _fail(kit.check_source, NoScheme(), "`schemes`", uri="src://x")

    class WrongSchema(_Src):
        def can_open(self, uri):
            return uri.startswith("src://")

        def read(self, uri, **o):
            yield pa.RecordBatch.from_pydict({"b": ["x"]})

    _fail(kit.check_source, WrongSchema(), "differs from schema", uri="src://x")

    class Changing(_Src):
        n = 0

        def can_open(self, uri):
            return uri.startswith("src://")

        def read(self, uri, **o):
            type(self).n += 1
            yield pa.RecordBatch.from_pydict({"a": [self.n]}, schema=self.schema(uri))

    _fail(kit.check_source, Changing(), "different data", uri="src://x")


def test_sink_rules():
    class Sink:
        name = "snk"
        schemes = ("snk",)
        extra = 0

        def write(self, uri, table, batches, **o):
            return sum(b.num_rows for b in batches) + self.extra

    kit.check_sink(Sink(), "snk://x", TABLE.to_batches())
    bad = Sink()
    bad.extra = 1
    _fail(kit.check_sink, bad, "returned 4 but 3 rows", uri="snk://x", batches=TABLE.to_batches())
    _fail(kit.check_sink, Sink(), "must not be empty", uri="snk://x", batches=[])


def test_detector_rules():
    class Det:
        name = "det"

        def __init__(self, result):
            self.result = result

        def detect(self, values, column):
            return self.result if len(values) and values.null_count < len(values) else None

    kit.check_detector(Det(v1.Detection("x", 0.9)), positives=[["a"]])
    _fail(kit.check_detector, Det(None), "returned None for a positive", positives=[["a"]])
    _fail(kit.check_detector, Det(("x", 0.9)), "Detection or None", positives=[["a"]])
    _fail(kit.check_detector, Det(v1.Detection("x", 1.5)), r"in \[0, 1\]", positives=[["a"]])
    _fail(kit.check_detector, Det(v1.Detection("", 0.5)), "non-empty", positives=[["a"]])
    _fail(
        kit.check_detector,
        Det(v1.Detection("x", 0.9)),
        "did not return None for a negative",
        positives=[["a"]],
        negatives=[["b"]],
    )

    class Always:
        name = "always"

        def detect(self, values, column):
            return v1.Detection("x", 1.0)

    _fail(kit.check_detector, Always(), "empty input", positives=[["a"]])

    class Flaky:
        name = "flaky"
        n = 0

        def detect(self, values, column):
            if not len(values) or values.null_count == len(values):
                return None
            type(self).n += 1
            return v1.Detection("x", 0.5 if self.n % 2 else 0.6)

    _fail(kit.check_detector, Flaky(), "different answers", positives=[["a"]])


def test_fitter_rules():
    class Fit:
        name = "fit"
        families = ("norm",)

        def __init__(self, result):
            self.result = result

        def fit(self, sample):
            return self.result

    ok = v1.FitResult("norm", {"loc": 0.0}, 0.1)
    kit.check_fitter(Fit(ok), [1.0, 2.0])
    _fail(kit.check_fitter, Fit(None), "returned None", sample=[1.0])
    _fail(kit.check_fitter, Fit(v1.FitResult("gamma", {}, 0.1)), "not in families", sample=[1.0])
    _fail(kit.check_fitter, Fit(v1.FitResult("norm", {}, 1.5)), "ks", sample=[1.0])
    _fail(
        kit.check_fitter,
        Fit(v1.FitResult("norm", {"loc": float("nan")}, 0.1)),
        "finite",
        sample=[1.0],
    )


def test_strategy_and_distribution_rules():
    class Strat:
        name = "strat"

        def generate(self, spec, ctx):
            return pa.array([1] * ctx.n_rows)

    kit.check_strategy(Strat(), {})

    class Short(Strat):
        def generate(self, spec, ctx):
            return pa.array([1] * (ctx.n_rows - 1)) if ctx.n_rows else pa.array([], pa.int64())

    _fail(kit.check_strategy, Short(), "returned 63 values", spec={})

    class Random(Strat):
        def generate(self, spec, ctx):
            import random

            return pa.array([random.random() for _ in range(ctx.n_rows)])

    _fail(kit.check_strategy, Random(), "not deterministic", spec={})

    class Mutating(Strat):
        def generate(self, spec, ctx):
            spec["touched"] = True  # type: ignore[index]
            return super().generate(spec, ctx)

    _fail(kit.check_strategy, Mutating(), "modified its `spec`", spec={})

    class ByChunk(Strat):
        def generate(self, spec, ctx):
            return pa.array([ctx.chunk] * ctx.n_rows)

    kit.check_strategy(ByChunk(), {})
    _fail(kit.check_strategy, ByChunk(), "chunk layout", spec={}, layout_independent=True)

    class Dist:
        name = "dist"

        def __init__(self, fn):
            self.fn = fn

        def sample(self, params, ctx):
            return self.fn(ctx)

    kit.check_distribution(Dist(lambda c: pa.array([0.5] * c.n_rows)), {})
    _fail(kit.check_distribution, Dist(lambda c: pa.array(["a"] * c.n_rows)), "numbers", params={})
    _fail(
        kit.check_distribution,
        Dist(lambda c: pa.array([float("inf")] * c.n_rows)),
        "non-finite",
        params={},
    )


def test_calendar_rules():
    class Cal:
        name = "cal"
        fill = 1.0

        def lift(self, start, end):
            return pa.array([self.fill] * ((end - start).days + 1), type=pa.float64())

    kit.check_calendar(Cal())
    neg = Cal()
    neg.fill = -1.0
    _fail(kit.check_calendar, neg, "non-negative")

    class Short(Cal):
        def lift(self, start, end):
            return pa.array([1.0], type=pa.float64())

    _fail(kit.check_calendar, Short(), "returned 1 values")

    class Ints(Cal):
        def lift(self, start, end):
            return pa.array([1] * ((end - start).days + 1))

    _fail(kit.check_calendar, Ints(), "float64")

    class Drifting(Cal):
        def lift(self, start, end):  # factor depends on where the range starts
            return pa.array([float(start.day)] * ((end - start).days + 1), type=pa.float64())

    _fail(kit.check_calendar, Drifting(), "sub-range", start=date(2024, 1, 1), end=date(2024, 3, 1))


def test_domain_rules():
    class Dom:
        name = "dom"

        def __init__(self, definition):
            self._d = definition

        def definition(self):
            return self._d

    good = v1.DomainDefinition(schema={"tables": {"t": {}}}, reference_data={"r": TABLE})
    kit.check_domain(Dom(good))
    _fail(kit.check_domain, Dom(v1.DomainDefinition(schema={})), "non-empty")
    _fail(kit.check_domain, Dom({"schema": 1}), "DomainDefinition")
    _fail(
        kit.check_domain,
        Dom(v1.DomainDefinition(schema={"t": 1}, reference_data={"r": [1]})),  # type: ignore[dict-item]
        "pyarrow Tables",
    )
    _fail(
        kit.check_domain,
        Dom(v1.DomainDefinition(schema={"t": 1}, scale_presets={"s": {"t": -1}})),
        "non-negative",
    )


def test_chaos_rules():
    batch = TABLE.to_batches()[0]

    class Chaos:
        name = "chaos"

        def mutate(self, batch, seed):
            return batch, v1.ChaosReport("chaos", 0)

    kit.check_chaos(Chaos(), batch)

    class Reshape(Chaos):
        def mutate(self, batch, seed):
            return batch.select(["id"]), v1.ChaosReport("chaos", 0)

    _fail(kit.check_chaos, Reshape(), "keep the batch schema", batch=batch)

    class NoReport(Chaos):
        def mutate(self, batch, seed):
            return batch, None

    _fail(kit.check_chaos, NoReport(), "ChaosReport", batch=batch)

    class Unseeded(Chaos):
        n = 0

        def mutate(self, batch, seed):
            type(self).n += 1
            return batch, v1.ChaosReport("chaos", self.n)

    _fail(kit.check_chaos, Unseeded(), "not deterministic", batch=batch)


def test_emitter_and_stream_source_rules():
    class Emit:
        name = "emit"
        schemes = ("emit",)
        extra = 0

        def emit(self, uri, batches, **o):
            return sum(b.num_rows for b in batches) + self.extra

    kit.check_emitter(Emit(), "emit://x", TABLE.to_batches())
    bad = Emit()
    bad.extra = 2
    _fail(kit.check_emitter, bad, "returned 5", uri="emit://x", batches=TABLE.to_batches())

    class Stream:
        name = "stream"
        schemes = ("stream",)
        resume_ok = True

        def read(self, uri, start=None, **o):
            n = 0 if start is None else int(start.value["n"])
            for i in range(n, 3):
                yield v1.StreamOffset({"n": i + 1}), pa.RecordBatch.from_pydict({"a": [i]})
                if not self.resume_ok and start is not None:
                    return

    kit.check_stream_source(Stream(), "stream://x")
    broken = Stream()
    broken.resume_ok = False

    class Restarts(Stream):
        def read(self, uri, start=None, **o):
            return super().read(uri, None, **o)  # ignores the offset

    _fail(kit.check_stream_source, Restarts(), "resume", uri="stream://x")

    class BadOffset(Stream):
        def read(self, uri, start=None, **o):
            yield v1.StreamOffset({"n": object()}), pa.RecordBatch.from_pydict({"a": [0]})

    _fail(kit.check_stream_source, BadOffset(), "JSON", uri="stream://x")


def test_transform_command_and_report_rules():
    class Tr:
        name = "tr"

        def apply(self, tables, **o):
            return {k: v for k, v in tables.items()}

    kit.check_transform(Tr(), {"t": TABLE})

    class Clear(Tr):
        def apply(self, tables, **o):
            tables.clear()
            return {}

    _fail(kit.check_transform, Clear(), "modified the tables", tables={"t": TABLE})

    class NotDict(Tr):
        def apply(self, tables, **o):
            return list(tables.values())

    _fail(kit.check_transform, NotDict(), "dict of tables", tables={"t": TABLE})

    class Cmd:
        name = "kitcmd"
        help = "a command"
        code = 0

        def configure(self, parser: argparse.ArgumentParser) -> None:
            parser.add_argument("--n", type=int, default=1)

        def run(self, args):
            return self.code

    kit.check_command(Cmd(), ["--n", "2"])
    failing = Cmd()
    failing.code = 3
    _fail(kit.check_command, failing, "expected 0", argv=[])
    kit.check_command(failing, [], expect_exit=3)
    _fail(kit.check_command, Cmd(), "does not accept argv", argv=["--bogus"])

    class BadName(Cmd):
        name = "Kit_Cmd"

    _fail(kit.check_command, BadName(), "name")

    class NoInt(Cmd):
        def run(self, args):
            return None

    _fail(kit.check_command, NoInt(), "exit code")

    class Rep:
        name = "rep"
        extension = ".txt"

        def render(self, report):
            return b"x"

    kit.check_report_format(Rep(), {"a": 1})

    class NoDot(Rep):
        extension = "txt"

    _fail(kit.check_report_format, NoDot(), "extension", report={})

    class Str(Rep):
        def render(self, report):
            return "text"

    _fail(kit.check_report_format, Str(), "bytes", report={})


def test_module_api_rules():
    import types

    ok = types.ModuleType("good_mod")
    ok.SHAPE_API = "1.3"  # type: ignore[attr-defined]
    assert kit.check_module_api(ok) == "1.3"
    for value, match in ((None, "must declare"), ("2.0", "majors must match"), ("x", "malformed")):
        mod = types.ModuleType("bad_mod")
        if value is not None:
            mod.SHAPE_API = value  # type: ignore[attr-defined]
        with pytest.raises(kit.ConformanceError, match=match):
            kit.check_module_api(mod)


def test_every_group_has_a_check():
    assert set(kit.CHECKS) == set(v1.GROUPS)


def test_author_guide_covers_every_group_and_check():
    from pathlib import Path

    guide = (Path(__file__).resolve().parents[2] / "docs/plugins/authoring.md").read_text(
        encoding="utf-8"
    )
    for group, check in ((g, c.__name__) for g, c in kit.CHECKS.items()):
        assert f"`{group}`" in guide and f"`{check}`" in guide, (group, check)
    for name in ("check_module_api", "check_installed", "check_plugin", "ConformanceError"):
        assert name in guide
