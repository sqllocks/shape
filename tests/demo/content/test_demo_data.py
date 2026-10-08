"""DM-04: demo data, day-2 drift and contracts, verified with the real shape API."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
import subprocess
import sys
import types
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

import shape

REPO = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(REPO / "scripts"))
from refengine_name import names_refengine  # noqa: E402

DEMO = REPO / "demo"
CONTRACTS = DEMO / "contracts"

# Mean shift is +40% = 0.39 baseline std, below the default 0.5 std threshold (section 12.3),
# so the demo diff lowers it explicitly.
DEMO_THRESHOLDS = {"mean_shift_std": 0.25}

# (column, rule) pairs day 2 must violate, per table: exactly the rules in DRIFT.md.
EXPECTED_VIOLATIONS = {
    "customers": {("email", "max_null_rate")},
    "orders": {("status", "allowed_values"), ("order_total", "max")},
    "products": {("sku", "unique")},
}
# (column, kind) changes diff must report.
EXPECTED_CHANGES = {
    "customers": {("email", "null_rate_change")},
    "orders": {("status", "new_categorical_values"), ("order_total", "mean_shift")},
    "products": set(),  # loss of uniqueness is a contract rule, not a diff kind (section 12.3)
}
# Side effects of the drift that the profiler reports; documented in DRIFT.md. Anything
# outside EXPECTED_CHANGES | KNOWN_SIDE_EFFECTS fails the test.
KNOWN_SIDE_EFFECTS = {
    "orders": {
        ("order_total", "new_categorical_values"),
        # the +40% price step also moves the value mix and stretches the range
        ("order_total", "category_shift"),
        ("order_total", "range_change"),
    },
}


def _load_make_data():
    spec = importlib.util.spec_from_file_location("demo_make_data", DEMO / "make_data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_make_data"] = mod
    spec.loader.exec_module(mod)
    return mod


make_data = _load_make_data()


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    # D2 is shortened to keep the suite fast; its contract needs at least 100,000 rows.
    day1 = make_data.build_day1("medium", 42, d2_rows=100_000)
    day2 = make_data.apply_day2(day1, 42)
    make_data.write_all(day1, day2, out)
    return out, day1, day2


@pytest.fixture(scope="module")
def profiles(data):
    out, _, _ = data
    res = {}
    for table in ("customers", "orders", "products"):
        for day in ("day1", "day2"):
            res[table, day] = shape.profile(out / day / f"{table}.parquet", name=table)
    res["d2", "day1"] = shape.profile(out / "day1" / "d2.parquet", name="d2")
    return res


def test_files_written(data):
    out, day1, day2 = data
    for name in day1:
        assert (out / "day1" / f"{name}.parquet").stat().st_size > 0
    assert sorted(p.stem for p in (out / "day2").glob("*.parquet")) == sorted(make_data.DRIFTED)
    # "order" and "return" are SQL keywords: files use the plural names
    assert "orders" in day1 and "order" not in day1
    assert "sku" in day1["products"].column_names
    assert sorted(day1) == ["customers", "d2", "orders", "products", "returns"]


def test_medium_row_counts(data):
    _, day1, day2 = data
    assert {k: t.num_rows for k, t in day1.items()} == {
        "customers": 50_000,
        "orders": 500_000,
        "products": 5_000,
        "returns": 85_000,
        "d2": 100_000,  # shortened by the fixture; the CLI default is 1,000,000
    }
    assert {k: t.num_rows for k, t in day2.items()} == {
        "customers": 50_000,
        "orders": 500_000,
        "products": 5_050,
    }


def test_consumer_columns_and_dtypes(data):
    """The columns and types that contracts, DRIFT.md and the talk rely on."""
    _, day1, _ = data
    want = {
        "customers": {"customer_id": pa.int64(), "email": pa.string(), "signup_date": pa.date32()},
        "orders": {
            "order_id": pa.int64(),
            "customer_id": pa.int64(),
            "order_date": pa.date32(),
            "status": pa.string(),
            "order_total": pa.float64(),
        },
        "products": {"product_id": pa.int64(), "sku": pa.string(), "unit_price": pa.float64()},
        "returns": {"return_id": pa.int64(), "order_id": pa.int64(), "product_id": pa.int64()},
        "d2": {"customer_id": pa.int64(), "email": pa.string(), "status": pa.string()},
    }
    for table, cols in want.items():
        for col, typ in cols.items():
            assert day1[table].schema.field(col).type == typ, (table, col)
    assert day1["d2"].num_columns == 20


def test_foreign_keys_resolve(data):
    _, day1, _ = data
    cust = set(day1["customers"]["customer_id"].to_pylist())
    assert set(day1["orders"]["customer_id"].to_pylist()) <= cust
    orders = set(day1["orders"]["order_id"].to_pylist())
    assert set(day1["returns"]["order_id"].to_pylist()) <= orders
    prods = set(day1["products"]["product_id"].to_pylist())
    assert set(day1["returns"]["product_id"].to_pylist()) <= prods


def test_generation_is_deterministic(data):
    _, day1, day2 = data
    again = make_data.apply_day2(day1, 42)
    for name in day2:
        assert again[name].equals(day2[name])


def test_build_day1_is_deterministic_per_seed():
    a = make_data.build_day1("small", 7, d2_rows=1_000)
    b = make_data.build_day1("small", 7, d2_rows=1_000)
    c = make_data.build_day1("small", 8, d2_rows=1_000)
    for name in a:
        assert a[name].equals(b[name]), name
    assert not a["orders"].equals(c["orders"])


def test_documented_drift_in_the_data(data):
    _, day1, day2 = data
    c1, c2 = day1["customers"], day2["customers"]
    assert c1["email"].null_count == 2485  # exactly 4.97%, the figure in TALK.md
    assert c1["email"].null_count / c1.num_rows == pytest.approx(0.05, abs=0.005)
    assert c2["email"].null_count / c2.num_rows == pytest.approx(0.20, abs=1e-9)
    assert c1.num_rows == c2.num_rows

    o1, o2 = day1["orders"], day2["orders"]
    assert "lost" not in set(o1["status"].to_pylist())
    assert "lost" in set(o2["status"].to_pylist())
    assert o2["status"].to_pylist().count("lost") == round(0.02 * o2.num_rows)
    assert pc.max(o1["order_total"]).as_py() == 5135.63  # the figure in TALK.md
    ratio = pc.mean(o2["order_total"]).as_py() / pc.mean(o1["order_total"]).as_py()
    assert ratio == pytest.approx(1.40, rel=1e-9)

    p1, p2 = day1["products"], day2["products"]
    assert len(set(p1["sku"].to_pylist())) == p1.num_rows
    assert p2.num_rows == p1.num_rows + 50
    assert p2.num_rows - len(set(p2["sku"].to_pylist())) == 50
    assert len(set(p2["product_id"].to_pylist())) == p2.num_rows  # only sku loses uniqueness


def test_parquet_round_trip(data):
    out, day1, _ = data
    assert pq.read_table(out / "day1" / "orders.parquet").equals(day1["orders"])


@pytest.mark.parametrize("table", ["customers", "orders", "products", "d2"])
def test_day1_contract_passes(profiles, table):
    r = shape.check(profiles[table, "day1"], CONTRACTS / f"{table}.json")
    assert r.passed, r.violations


@pytest.mark.parametrize("table", sorted(EXPECTED_VIOLATIONS))
def test_day2_contract_fails_on_exactly_documented_rules(profiles, table):
    r = shape.check(profiles[table, "day2"], CONTRACTS / f"{table}.json")
    assert not r.passed
    assert {(v["column"], v["rule"]) for v in r.violations} == EXPECTED_VIOLATIONS[table]


class _FileClient:
    """A file of the lakehouse Files area of a Fabric connection, served from a local folder."""

    def __init__(self, path: Path):
        self.path = path

    def get_file_properties(self):
        return types.SimpleNamespace(size=self.path.stat().st_size)

    def download_file(self):
        return types.SimpleNamespace(readall=self.path.read_bytes)

    def upload_data(self, data, overwrite=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)


@pytest.mark.parametrize("table", sorted(EXPECTED_VIOLATIONS))
def test_udf_gate_on_day2_fails_for_the_documented_reason(data, table):
    """Beat 7 (pipeline ``shape_gate_udf``): profileLakehouseFile, then checkProfile with
    failOnViolation. Withholding classified values by default (#721) leaves the reason intact:
    the same rules fail, and the gated ``orders`` table shows the same violations as before."""
    from shape.integrations.fabric import udf

    out, _, _ = data
    files = types.SimpleNamespace(get_file_client=lambda path: _FileClient(out / path))
    lakehouse = types.SimpleNamespace(connectToFiles=lambda: files)
    contract = json.loads((CONTRACTS / f"{table}.json").read_text())
    for day in ("day1", "day2"):
        udf.profile_lakehouse_file(lakehouse, f"{day}/{table}.parquet", f"s/{table}_{day}.shape")
    assert udf.check_profile(lakehouse, f"s/{table}_day1.shape", contract, True)["passed"]
    with pytest.raises(udf.UserThrownError) as e:
        udf.check_profile(lakehouse, f"s/{table}_day2.shape", contract, fail_on_violation=True)
    violations = e.value.properties["violations"]
    assert {(v["column"], v["rule"]) for v in violations} == EXPECTED_VIOLATIONS[table]
    for column, rule in EXPECTED_VIOLATIONS[table]:
        assert f"{column}: {rule}" in e.value.message
    full = udf.check_profile(lakehouse, f"s/{table}_day2.shape", contract, include_raw_values=True)
    if table == "orders":
        assert violations == full["violations"]
        assert {v["column"]: v["observed"] for v in violations}["order_total"] == pytest.approx(
            7189.882
        )


@pytest.mark.parametrize("table", sorted(EXPECTED_CHANGES))
def test_diff_reports_each_documented_change(profiles, table):
    d = shape.diff(profiles[table, "day1"], profiles[table, "day2"], thresholds=DEMO_THRESHOLDS)
    got = {(c["column"], c["kind"]) for c in d.changes}
    assert EXPECTED_CHANGES[table] <= got
    assert got <= EXPECTED_CHANGES[table] | KNOWN_SIDE_EFFECTS.get(table, set())
    assert d.drifted == bool(got)


def test_default_thresholds_miss_the_mean_shift(profiles):
    """Documented: +40% is 0.39 std, so the demo must lower mean_shift_std."""
    d = shape.diff(profiles["orders", "day1"], profiles["orders", "day2"])
    assert ("order_total", "mean_shift") not in {(c["column"], c["kind"]) for c in d.changes}


def test_the_notebook_baseline_diff_reports_only_the_new_status(profiles, tmp_path):
    """DEMO-LIVE F-5: ``shape_profile`` saves its artifact with ``shape.save``'s default safe
    capture, and a day-2 run with that artifact as ``baselinePath`` diffs against it. The safe
    capture leaves out the values ``order_total``'s comparisons need, so the notebook's
    ``changes`` hold only ``status``'s new value; ``order_total``'s comparisons are not evaluable
    (never drift). RUNBOOK section 4 step 6 and DRIFT.md say so; the contract check, run on the
    day-2 profile in memory, still names ``order_total``'s ``max``."""
    path = tmp_path / "orders_day1.shape"
    shape.save(profiles["orders", "day1"], str(path))  # the notebook's call: default capture
    with pytest.warns(Warning, match="not signed"):
        baseline = shape.load(str(path))
    d = shape.diff(baseline, profiles["orders", "day2"])
    assert d.drifted
    assert [(c["column"], c["kind"]) for c in d.changes] == [("status", "new_categorical_values")]
    skipped = {(g["column"], g["kind"]) for g in d.not_evaluable}
    assert {("order_total", "range_change"), ("order_total", "category_shift")} <= skipped
    checked = shape.check(profiles["orders", "day2"], CONTRACTS / "orders.json")
    assert {(v["column"], v["rule"]) for v in checked.violations} == EXPECTED_VIOLATIONS["orders"]


def test_diff_of_day1_with_itself_is_clean(profiles):
    d = shape.diff(profiles["orders", "day1"], profiles["orders", "day1"])
    assert not d.drifted


def test_talk_stage_diff_command_fits_a_screen_and_shows_the_mean_shift(profiles, tmp_path, capsys):
    """DEMO-REHEARSAL: a plain ``shape diff`` of day 1 and day 2 prints over 1 MB of JSON (every
    day-2 ``order_total`` value is a new categorical value). TALK.md gives the command to run on
    stage instead; it must stay short and still show the documented +40% shift."""
    from shape.cli.main import main

    talk = (DEMO / "TALK.md").read_text(encoding="utf-8")
    found = re.search(r"`(shape diff o1\.shape o2\.shape [^`]+)`", talk)
    assert found is not None, "TALK.md gives no stage diff command"
    for day in ("day1", "day2"):
        shape.save(profiles["orders", day], tmp_path / f"o{day[-1]}.shape")
    argv = found.group(1).split()[1:]
    code = main([str(tmp_path / a) if a.endswith(".shape") else a for a in argv])
    out = capsys.readouterr().out
    assert code == 0
    assert len(out) < 4000
    kinds = {(c["column"], c["kind"]) for c in json.loads(out)["changes"]}
    assert ("order_total", "mean_shift") in kinds


def test_contracts_are_valid_json_and_named_after_tables():
    files = sorted(CONTRACTS.glob("*.json"))
    assert {f.stem for f in files} == {"customers", "orders", "products", "d2"}
    for f in files:
        assert isinstance(json.loads(f.read_text(encoding="utf-8")), dict)


def test_talk_day2_numbers_match_real_results(profiles):
    talk = (DEMO / "TALK.md").read_text(encoding="utf-8")
    o2 = shape.check(profiles["orders", "day2"], CONTRACTS / "orders.json")
    top = next(v for v in o2.violations if v["rule"] == "max")["observed"]
    assert f"{top:g}" in talk
    p1 = profiles["orders", "day1"].summary()["columns"]
    cols = p1 if isinstance(p1, dict) else {c["name"]: c for c in p1}
    assert f"{cols['order_total']['max']:g}" in talk
    c1 = profiles["customers", "day1"].summary()["columns"]
    c1 = c1 if isinstance(c1, dict) else {c["name"]: c for c in c1}
    assert f"{c1['email']['null_rate'] * 100:.2f}%" in talk


def test_drift_doc_lists_every_drift():
    text = (DEMO / "DRIFT.md").read_text(encoding="utf-8")
    for needle in (
        "customers.email",
        "orders.status",
        "lost",
        "orders.order_total",
        "products.sku",
        "50",
    ):
        assert needle in text
    for table, rules in EXPECTED_VIOLATIONS.items():
        for col, rule in rules:
            assert re.search(rf"`{table}\.{col}`.*`{rule}`", text, re.S), (table, col, rule)


def test_make_data_cli_writes_the_layout(tmp_path, capsys):
    rc = make_data.main(["--out", str(tmp_path), "--scale", "small", "--seed", "3"])
    assert rc == 0
    assert sorted(p.name for p in (tmp_path / "day1").glob("*.parquet")) == [
        "customers.parquet",
        "d2.parquet",
        "orders.parquet",
        "products.parquet",
        "returns.parquet",
    ]
    assert sorted(p.name for p in (tmp_path / "day2").glob("*.parquet")) == [
        "customers.parquet",
        "orders.parquet",
        "products.parquet",
    ]
    assert "day1/orders.parquet" in capsys.readouterr().out


def test_make_data_is_self_contained():
    """The generator needs no external checkout and imports nothing from benchmarks/."""
    text = (DEMO / "make_data.py").read_text(encoding="utf-8") + (DEMO / "d2_table.py").read_text(
        encoding="utf-8"
    )
    assert "benchmarks" not in text
    assert not names_refengine(text)
    assert "--" + "spin" not in text


def test_no_large_files_committed():
    big = [p for p in DEMO.rglob("*") if p.is_file() and p.stat().st_size > 1_000_000]
    assert not big
    assert not list(DEMO.rglob("*.parquet"))


def _talk_fallback_commands() -> list[str]:
    """The commands of TALK.md's "Nothing works" fallback row, in order, as written."""
    talk = (DEMO / "TALK.md").read_text(encoding="utf-8")
    row = next(line for line in talk.splitlines() if line.startswith("| Nothing works |"))
    return [c for c in re.findall(r"`([^`]+)`", row) if c.split()[0] in ("python", "shape")]


def _run_as_written(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    argv = shlex.split(command)
    head = [sys.executable] if argv[0] == "python" else [sys.executable, "-m", "shape"]
    return subprocess.run(
        head + argv[1:], cwd=cwd, capture_output=True, text=True, check=False, timeout=600
    )


def test_talk_local_fallback_runs_as_written(tmp_path):
    """DEMO-CANDIDATE F1: the "Nothing works" row of TALK.md, run as written from a copy of the
    repository root (``demo/`` linked in): day 1 passes (exit 0) and day 2 fails (exit 1) with
    the ``status`` and ``order_total`` violations of the day-2 table above. Since W1-11 the CLI
    captures safe by default, which withholds ``order_total``'s min and max (exit 2), so the
    row profiles with ``--capture full``. The stage diff and the plain diff print the byte
    counts that TALK.md quotes (``DRIFT.md``)."""
    (tmp_path / "demo").symlink_to(DEMO, target_is_directory=True)
    commands = _talk_fallback_commands()
    assert commands[0] == "python demo/make_data.py --out data", commands
    profiles = [c for c in commands if c.startswith("shape profile ")]
    checks = [c for c in commands if c.startswith("shape check ")]
    assert len(profiles) == 2 and len(checks) == 2, commands
    assert all("--capture full" in c for c in profiles), profiles

    results = [_run_as_written(c, tmp_path) for c in commands]
    for command, result in zip(commands, results, strict=True):
        if not command.startswith("shape check "):
            assert result.returncode == 0, (command, result.stderr)
    day1, day2 = (results[commands.index(c)] for c in checks)
    assert day1.returncode == 0, day1.stdout + day1.stderr
    assert json.loads(day1.stdout) == {"passed": True, "violations": []}
    assert day2.returncode == 1, day2.stdout + day2.stderr
    report = json.loads(day2.stdout)
    assert report["passed"] is False
    assert {(v["column"], v["rule"]) for v in report["violations"]} == EXPECTED_VIOLATIONS["orders"]
    observed = {v["column"]: v["observed"] for v in report["violations"]}
    assert observed["status"] == {"unexpected_values": ["lost"]}
    assert observed["order_total"] == pytest.approx(7189.882)
    talk = (DEMO / "TALK.md").read_text(encoding="utf-8")
    assert "`status` `allowed_values`" in talk and "new value `lost`" in talk
    assert f"max {observed['order_total']:g}" in talk
    assert "(exit 0)" in talk and "(exit 1, the `status` and `order_total` violations)" in talk

    stage_cmd = re.search(r"`(shape diff o1\.shape o2\.shape [^`]+)`", talk)
    assert stage_cmd is not None
    stage = _run_as_written(stage_cmd.group(1), tmp_path)
    plain = _run_as_written("shape diff o1.shape o2.shape", tmp_path)
    assert stage.returncode == 0 and plain.returncode == 0, stage.stderr + plain.stderr
    kinds = {(c["column"], c["kind"]) for c in json.loads(stage.stdout)["changes"]}
    assert ("order_total", "mean_shift") in kinds
    stage_bytes, plain_bytes = len(stage.stdout.encode()), len(plain.stdout.encode())
    assert f"prints {stage_bytes}\nbytes" in talk, stage_bytes
    assert f"it prints {plain_bytes:,} bytes of JSON" in talk, plain_bytes
    drift = (DEMO / "DRIFT.md").read_text(encoding="utf-8")
    assert f"# {stage_bytes} bytes" in drift and f"# {plain_bytes} bytes" in drift


def test_mean_shift_in_baseline_std_is_the_documented_figure(profiles):
    """TALK.md's 0.3876 (and DRIFT.md's derivation): (day-2 mean - day-1 mean) / day-1 std."""

    def column(p):
        cols = p.summary()["columns"]
        cols = cols if isinstance(cols, dict) else {c["name"]: c for c in cols}
        return cols["order_total"]

    c1, c2 = column(profiles["orders", "day1"]), column(profiles["orders", "day2"])
    shift = (c2["mean"] - c1["mean"]) / c1["std"]
    figure = f"{shift:.4f}"
    assert figure == "0.3876"
    derivation = f"{figure} = ({c2['mean']:.4f} − {c1['mean']:.4f}) / {c1['std']:.4f}"
    assert derivation in " ".join((DEMO / "DRIFT.md").read_text(encoding="utf-8").split())
    assert f"({figure}," in (DEMO / "TALK.md").read_text(encoding="utf-8")
