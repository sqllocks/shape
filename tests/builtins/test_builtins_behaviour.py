"""P2-04: the built-ins do their job when reached through the plugin host."""

from datetime import date

import numpy as np
import pyarrow as pa
import pytest

from shape.builtins.strategies.address import AddressReference
from shape.plugins import registry
from shape.plugins.api.v1 import GenerationContext
from shape.plugins.host import PluginHost


@pytest.fixture(scope="module")
def host():
    h = PluginHost(entry_points=lambda: [])
    registry.register_builtins(h)
    return h


def ctx(n=1000, chunk=0, row_start=0, column="c", seed=7):
    return GenerationContext(seed, "t", column, chunk, row_start, n)


TABLE = pa.table({"id": [1, 2, 3], "name": ["a", "b", None], "when": pa.array([1.5, 2.5, 3.5])})


@pytest.mark.parametrize(
    "kind,ext", [("csv", "csv"), ("parquet", "parquet"), ("jsonl", "jsonl"), ("ipc", "arrow")]
)
def test_sink_then_source_round_trip(host, tmp_path, kind, ext):
    sink = host.get("shape.sinks", kind)
    source = host.get("shape.sources", kind)
    target = tmp_path / f"out.{ext}"
    batches = TABLE.to_batches(max_chunksize=2)
    assert sink.write(str(target), "t", iter(batches)) == 3
    assert source.can_open(str(target))
    assert source.can_open(target.as_uri())
    got = pa.Table.from_batches(list(source.read(str(target))))
    assert got.column("id").to_pylist() == [1, 2, 3]
    assert got.column("name").to_pylist() == ["a", "b", None]
    assert source.schema(str(target)).names == ["id", "name", "when"]


def test_source_rejects_other_file_types_and_schemes(host, tmp_path):
    csv = host.get("shape.sources", "csv")
    assert not csv.can_open(str(tmp_path / "x.parquet"))
    assert not csv.can_open("s3://bucket/x.csv")
    assert not csv.can_open(str(tmp_path / "x.txt"))
    with pytest.raises(ValueError, match="not a csv file"):
        csv.schema(str(tmp_path / "x.parquet"))
    assert host.get("shape.sources", "jsonl").can_open(str(tmp_path / "x.ndjson.gz"))


def test_sink_into_a_directory_names_the_file_after_the_table(host, tmp_path):
    n = host.get("shape.sinks", "parquet").write(str(tmp_path) + "/", "orders", TABLE.to_batches())
    assert n == 3 and (tmp_path / "orders.parquet").exists()
    assert host.get("shape.sinks", "csv").write(str(tmp_path / "e.csv"), "e", iter([])) == 0


def test_csv_source_takes_options(host, tmp_path):
    p = tmp_path / "z.csv"
    p.write_text("zip;n\n01234;1\n")
    got = pa.Table.from_batches(
        list(
            host.get("shape.sources", "csv").read(
                str(p), csv={"delimiter": ";", "column_types": {"zip": "string"}}
            )
        )
    )
    assert got.column("zip").to_pylist() == ["01234"]


def test_detectors_label_columns(host):
    emails = pa.array(["a@x.io", "b@y.org", "nope", None])
    assert host.get("shape.detectors", "email").detect(emails, "e").label == "email"
    assert host.get("shape.detectors", "us_ssn").detect(emails, "e") is None
    ssn = pa.array(["123-45-6789", "987-65-4321"])
    hit = host.get("shape.detectors", "us_ssn").detect(ssn, "s")
    assert hit is not None and hit.confidence == 1.0  # the share of sampled values that match
    assert host.get("shape.detectors", "ipv4").detect(pa.array(["10.0.0.1", "300.1.1.1"]), "i")
    assert host.get("shape.detectors", "phone").detect(pa.array(["+1 614 555 0100"] * 3), "p")
    assert host.get("shape.detectors", "email").detect(pa.array([], pa.string()), "e") is None


def test_fitter_agrees_with_the_profiler_function(host):
    from shape.profile.fitting import detect_distribution

    values = np.random.default_rng(3).normal(50, 4, 4000)
    expected = detect_distribution(values)
    got = host.get("shape.fitters", "auto").fit(pa.array(values))
    assert got is not None
    assert got.family == expected["distribution"] == "normal"
    assert dict(got.params) == expected["distribution_params"]
    assert got.ks == pytest.approx(1 - expected["fit_score"], abs=1e-4)
    assert host.get("shape.fitters", "auto").fit(pa.array([1.0, 2.0])) is None
    assert host.get("shape.fitters", "auto").fit(pa.array([None] * 30, pa.float64())) is None


@pytest.mark.parametrize(
    "name,params,family",
    [
        ("normal", {"loc": 10.0, "scale": 2.0}, "normal"),
        ("uniform", {"loc": 5.0, "scale": 20.0}, "uniform"),
        ("exponential", {"loc": 0.0, "scale": 3.0}, "exponential"),
        ("lognormal", {"s": 0.5, "loc": 0.0, "scale": 20.0}, "lognormal"),
    ],
)
def test_distribution_draws_are_deterministic_and_refit(host, name, params, family):
    dist = host.get("shape.distributions", name)
    a, b = dist.sample(params, ctx(5000)), dist.sample(params, ctx(5000))
    assert a == b and len(a) == 5000
    # draws are addressed by row (T-16): the chunk number does not matter, the rows do
    assert dist.sample(params, ctx(5000, chunk=1)) == a
    assert dist.sample(params, ctx(5000, row_start=5000)) != a
    assert dist.sample(params, ctx(2500, row_start=2500)) == a.slice(2500)
    assert dist.sample(params, ctx(5000, column="d")) != a
    fit = host.get("shape.fitters", "auto").fit(a)
    assert fit is not None
    if family == "normal":
        # a lognormal with a tiny shape is indistinguishable from a normal at this size, and the
        # profiler may pick it; the draws themselves must have the configured moments
        x = np.asarray(a)
        assert abs(x.mean() - 10) < 0.2 and abs(x.std() - 2) < 0.2
    else:
        assert fit.family == family


def test_distribution_parameter_errors(host):
    with pytest.raises(ValueError, match="scale"):
        host.get("shape.distributions", "normal").sample({"scale": 0}, ctx())
    with pytest.raises(ValueError, match="'s'"):
        host.get("shape.distributions", "lognormal").sample({}, ctx())


def test_sequence_strategy_is_independent_of_chunking(host):
    seq = host.get("shape.strategies", "sequence")
    spec = {"start": 10, "step": 5}
    whole = seq.generate(spec, ctx(6)).to_pylist()
    halves = (
        seq.generate(spec, ctx(3, 0, 0)).to_pylist() + seq.generate(spec, ctx(3, 1, 3)).to_pylist()
    )
    assert whole == halves == [10, 15, 20, 25, 30, 35]


def test_constant_choice_uniform_normal_strategies(host):
    get = lambda n: host.get("shape.strategies", n)  # noqa: E731
    assert get("constant").generate({"value": "x"}, ctx(3)).to_pylist() == ["x"] * 3
    pick = get("choice").generate({"values": ["a", "b"], "weights": [0.0, 1.0]}, ctx(50))
    assert set(pick.to_pylist()) == {"b"}
    assert get("choice").generate({"values": ["a", "b", "c"]}, ctx(200)) == get("choice").generate(
        {"values": ["a", "b", "c"]}, ctx(200)
    )
    with pytest.raises(ValueError):
        get("choice").generate({"values": []}, ctx())
    with pytest.raises(ValueError):
        get("choice").generate({"values": [1, 2], "weights": [1]}, ctx())
    u = np.asarray(get("uniform").generate({"low": 2, "high": 4}, ctx(2000)))
    assert u.min() >= 2 and u.max() < 4
    n = np.asarray(get("normal").generate({"mean": 100, "stddev": 5}, ctx(20000)))
    assert abs(n.mean() - 100) < 0.5 and abs(n.std() - 5) < 0.3


REF = [
    AddressReference("100 N High St", "Columbus", "Franklin", "OH", "43215", "US", 39.96, -83.0),
    AddressReference("5 Main St", "Austin", "Travis", "TX", "73301", "US", 30.27, -97.74),
]


def test_address_strategy(host):
    strat = host.get("shape.strategies", "address")
    out = strat.generate({"reference": REF}, ctx(40))
    assert pa.types.is_struct(out.type) and len(out) == 40
    assert set(out.field("state").to_pylist()) <= {"OH", "TX"}
    assert out == strat.generate({"reference": REF}, ctx(40))
    assert out != strat.generate({"reference": REF}, ctx(40, seed=8))
    oh = strat.generate({"reference": REF, "scope": [{"state": "OH"}], "field": "city"}, ctx(10))
    assert set(oh.to_pylist()) == {"Columbus"}
    as_dict = [r.__dict__ if hasattr(r, "__dict__") else {} for r in REF]
    del as_dict
    with pytest.raises(ValueError, match="reference"):
        strat.generate({}, ctx(1))
    with pytest.raises(ValueError, match="unknown address field"):
        strat.generate({"reference": REF, "field": "nope"}, ctx(1))


def lift(host, name, start, end):
    return host.get("shape.calendars", name).lift(start, end)


def test_us_federal_dates_are_rule_based_and_observed(host):
    cal = host.get("shape.calendars", "us_federal")
    h2026 = cal.holidays(date(2026, 1, 1), date(2026, 12, 31))
    assert h2026[date(2026, 5, 25)] == "memorial_day"
    assert h2026[date(2026, 11, 26)] == "thanksgiving"
    assert h2026[date(2026, 7, 3)] == "independence_day"  # July 4 is a Saturday: Friday off
    assert h2026[date(2026, 1, 19)] == "martin_luther_king_day"
    assert len(h2026) == 11
    # Jan 1 2022 is a Saturday: observed on Dec 31 2021, which belongs to 2021's range
    assert cal.holidays(date(2021, 12, 31), date(2021, 12, 31)) == {
        date(2021, 12, 31): "new_years_day"
    }
    assert "juneteenth" not in cal.holidays(date(2020, 1, 1), date(2020, 12, 31)).values()
    # 2021: eleven holidays (Juneteenth included) plus 2022's New Year observed on Dec 31
    assert len(cal.holidays(date(2021, 1, 1), date(2021, 12, 31))) == 12


def test_us_retail_dates(host):
    cal = host.get("shape.calendars", "us_retail")
    r = cal.holidays(date(2026, 1, 1), date(2026, 12, 31))
    assert {v: k for k, v in r.items()}["black_friday"] == date(2026, 11, 27)
    assert {v: k for k, v in r.items()}["cyber_monday"] == date(2026, 11, 30)
    assert {v: k for k, v in r.items()}["easter"] == date(2026, 4, 5)
    assert {v: k for k, v in r.items()}["mothers_day"] == date(2026, 5, 10)
    assert cal.holidays(date(2025, 4, 1), date(2025, 4, 30)) == {date(2025, 4, 20): "easter"}


def test_calendar_lift_array(host):
    out = lift(host, "us_retail", date(2026, 11, 25), date(2026, 12, 1))
    assert out.type == pa.float64() and len(out) == 7
    assert out.to_pylist() == [1.0] * 7  # neutral by default
    from shape.builtins.calendars import UsRetailCalendar

    shaped = UsRetailCalendar(holiday_lift=3.0).lift(date(2026, 11, 25), date(2026, 12, 1))
    assert shaped.to_pylist() == [
        1.0,
        3.0,
        3.0,
        1.0,
        1.0,
        3.0,
        1.0,
    ]  # Thanksgiving, Black Fri, Cyber Mon
    with pytest.raises(ValueError):
        lift(host, "us_retail", date(2026, 2, 1), date(2026, 1, 1))
