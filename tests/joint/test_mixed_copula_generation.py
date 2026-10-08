"""W3-08 (#232): the mixed-type copula in generation: ``shape generate --from PROFILE
--mixed-copula``, the schema block, the plan, and the guarantee that without it nothing changes."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.generation import copula_mixed as CM
from shape.generation.engine import Engine
from shape.generation.fit import PRESET, fit_schema
from shape.generation.schema import GenSchema
from shape.profile.joint import measures as M

FIXTURES = Path(__file__).parents[1] / "fixtures" / "profiles"
N = 20_000


def _eta(cat: Any, x: Any) -> float:
    lab, codes = np.unique(np.asarray(cat), return_inverse=True)
    return float(M.correlation_ratio(codes, np.asarray(x, dtype=float), len(lab)))


def _cramers_v(a: Any, b: Any) -> float:
    la, ca = np.unique(np.asarray(a), return_inverse=True)
    lb, cb = np.unique(np.asarray(b), return_inverse=True)
    return float(M.cramers_v(M.contingency(ca, cb, len(la), len(lb))))


def _binned(z: np.ndarray, cuts: list[float], labels: list[str]) -> np.ndarray:
    edges = np.quantile(z, np.cumsum(cuts)[:-1])
    return np.array(labels)[np.searchsorted(edges, z)]


def _customers(rho_cc: float, rho_cn: float, seed: int = 21) -> pa.Table:
    """``segment`` and ``channel`` (V near 0.5), ``spend`` (a category-to-number ratio near 0.6),
    ``age`` and a key."""
    rng = np.random.default_rng(seed)
    cov = [[1, rho_cc, rho_cn], [rho_cc, 1, rho_cn * rho_cc], [rho_cn, rho_cn * rho_cc, 1]]
    z = rng.multivariate_normal(np.zeros(3), cov, N)
    return pa.table(
        {
            "customer_id": pa.array(np.arange(N, dtype=np.int64)),
            "segment": pa.array(_binned(z[:, 0], [0.5, 0.3, 0.2], ["lo", "mid", "hi"])),
            "channel": pa.array(_binned(z[:, 1], [0.4, 0.35, 0.25], ["web", "store", "app"])),
            "spend": pa.array(np.exp(0.5 * z[:, 2]) * 40 + 10),
            "age": pa.array(rng.integers(18, 80, N).astype(np.int64)),
        }
    )


@pytest.fixture(scope="module")
def customers() -> pa.Table:
    return _customers(0.8, 0.8)


@pytest.fixture(scope="module")
def profile(customers: pa.Table) -> Any:
    return shape.profile(customers, multivariate=True)


def _tables_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The table documents of a profile document (``Profile.tables`` returns copies)."""
    return list(doc["tables"].values()) if "tables" in doc else [doc]


def _without_conditionals(profile: Any) -> Any:
    doc = profile.to_dict()
    for t in _tables_of(doc):
        t["joint"]["conditionals"] = []
    return doc


def _generate(profile: Any, **kw: Any) -> dict[str, list[Any]]:
    result = shape.generate(profile, seed=3, **kw)
    return dict(result.tables[next(iter(result.tables))].to_pydict())


def _counts(table: dict[str, list[Any]]) -> dict[str, Counter[Any]]:
    return {k: Counter(v) for k, v in table.items()}


# --- the acceptance: both links within 0.05, every column's value counts identical ---------------


@pytest.mark.parametrize("conditionals", [False, True], ids=["no-conditional-table", "conditional"])
@pytest.mark.parametrize(
    "rho", [(0.8, 0.8), (0.7, 0.7), (0.9, 0.6)], ids=["rho.8/.8", "rho.7/.7", "rho.9/.6"]
)
def test_the_category_to_number_ratio_and_cramers_v_are_kept_within_0_05(
    rho: tuple[float, float], conditionals: bool
) -> None:
    table = _customers(*rho)
    prof = shape.profile(table, multivariate=True)
    if not conditionals:
        prof = _without_conditionals(prof)
    seg, chan, spend = (table[c].to_pylist() for c in ("segment", "channel", "spend"))
    want_eta, want_v = _eta(seg, spend), _cramers_v(seg, chan)
    assert 0.45 < want_eta < 0.75 and 0.4 < want_v < 0.65
    plain = _generate(prof)
    mixed = _generate(prof, mixed_copula=True)
    assert abs(_eta(mixed["segment"], mixed["spend"]) - want_eta) <= 0.05
    assert abs(_cramers_v(mixed["segment"], mixed["channel"]) - want_v) <= 0.05
    # without the copula the number is unrelated to the category
    assert _eta(plain["segment"], plain["spend"]) < 0.05
    # every column keeps exactly its generated values
    assert _counts(mixed) == _counts(plain)


def test_the_exact_marginals_and_the_reordering_are_not_the_identity() -> None:
    prof = shape.profile(_customers(0.8, 0.8), multivariate=True)
    plain, mixed = _generate(prof), _generate(prof, mixed_copula=True)
    assert plain["customer_id"] == mixed["customer_id"]  # a key is left alone
    assert plain["age"] != mixed["age"] or plain["spend"] != mixed["spend"]
    assert sorted(plain["spend"]) == sorted(mixed["spend"])


def test_the_numeric_correlation_between_numeric_columns_is_kept() -> None:
    rng = np.random.default_rng(5)
    z = rng.multivariate_normal([0, 0], [[1, 0.7], [0.7, 1]], N)
    g = rng.integers(0, 3, N)
    t = pa.table(
        {
            "height": pa.array(170 + 10 * z[:, 0]),
            "weight": pa.array(70 + 12 * z[:, 1]),
            "team": pa.array(np.array(["a", "b", "c"])[g]),
        }
    )
    mixed = _generate(shape.profile(t, multivariate=True), mixed_copula=True)
    r = np.corrcoef(mixed["height"], mixed["weight"])[0, 1]
    assert abs(r - 0.7) <= 0.05


# --- off by default: generation is what it was ---------------------------------------------


def test_without_the_flag_the_output_is_identical_to_a_profile_without_the_copula(
    profile: Any,
) -> None:
    stripped = copy.deepcopy(profile)
    for t in stripped.tables.values():
        for key in ("multivariate_outliers", "pca", "cohorts", "copula"):
            t["joint"].pop(key, None)
    a = shape.generate(profile, seed=3).tables
    b = shape.generate(stripped, seed=3).tables
    c = shape.generate(profile, seed=3, mixed_copula=False).tables
    for name in a:
        assert a[name].equals(b[name]) and a[name].equals(c[name])


def test_the_schema_has_no_copula_block_unless_asked_for(profile: Any) -> None:
    off = fit_schema(profile)
    assert CM.OUTPUT_KEY not in off.schema.generation.output
    on = fit_schema(profile, mixed_copula=True)
    block = on.schema.generation.output[CM.OUTPUT_KEY]
    assert block["format"] == CM.FORMAT and block["version"] == CM.VERSION
    assert set(block["tables"]) == {next(iter(on.schema.tables))}
    # the rest of the schema is the same
    a, b = off.schema.to_dict(), on.schema.to_dict()
    del b["generation"]["output"][CM.OUTPUT_KEY]
    assert a == b


def test_a_profile_without_joint_copula_is_unchanged_with_the_flag() -> None:
    old = shape.load(str(FIXTURES / "pre_w3_08.shape"))
    assert "copula" not in next(iter(old.tables.values()))["joint"]
    off, on = shape.generate(old, seed=1), shape.generate(old, seed=1, mixed_copula=True)
    for name in off.tables:
        assert off.tables[name].equals(on.tables[name])
    assert CM.OUTPUT_KEY not in fit_schema(old, mixed_copula=True).schema.generation.output
    assert not [i for i in fit_schema(old, mixed_copula=True).plan.items if "copula" in i.evidence]


@pytest.mark.parametrize("name", ["pre_w3_07.shape", "pre_w3_08.shape"])
def test_an_older_profile_generates_with_and_without_the_flag(name: str) -> None:
    old = shape.load(str(FIXTURES / name))
    for flag in (False, True):
        result = shape.generate(old, seed=2, mixed_copula=flag)
        assert all(t.num_rows > 0 for t in result.tables.values())


# --- determinism ---------------------------------------------------------------------------------


def test_the_result_is_the_same_on_every_run_and_changes_with_the_seed(profile: Any) -> None:
    a = shape.generate(profile, seed=5, mixed_copula=True)
    b = shape.generate(profile, seed=5, mixed_copula=True)
    c = shape.generate(profile, seed=6, mixed_copula=True)
    name = next(iter(a.tables))
    assert a.tables[name].equals(b.tables[name])
    assert not a.tables[name].equals(c.tables[name])


def test_the_chunk_size_does_not_change_the_result(profile: Any) -> None:
    schema = fit_schema(profile, mixed_copula=True).schema
    a = Engine(schema, scale=PRESET, seed=4).generate()
    b = Engine(schema, scale=PRESET, seed=4, chunk_rows=1000).generate()
    name = next(iter(a.tables))
    assert a.tables[name].equals(b.tables[name])


# --- the plan ------------------------------------------------------------------------------------


def test_the_plan_lists_the_columns_the_copula_orders(profile: Any) -> None:
    on = [
        i for i in fit_schema(profile, mixed_copula=True).plan.items if "joint.copula" in i.evidence
    ]
    assert len(on) == 1 and on[0].status == "approximate"
    for column in ("spend", "age", "segment"):
        assert column in on[0].reason
    assert "channel" in on[0].reason  # moves with segment (a conditional table draws it from it)
    off = [i for i in fit_schema(profile).plan.items if "joint.copula" in i.evidence]
    assert len(off) == 1 and off[0].status == "not_modelled"
    assert "--mixed-copula" in off[0].reason


def test_the_new_joint_entries_are_listed_as_not_modelled(profile: Any) -> None:
    items = {i.evidence: i for i in fit_schema(profile).plan.items}
    prefix = next(iter(profile.tables))
    for field in ("pca", "cohorts"):
        got = items.get(f"{prefix}.joint.{field}")
        assert got is not None and got.status == "not_modelled"


# --- which columns are left alone ----------------------------------------------------------------


def _schema(columns: dict[str, dict[str, Any]], spec_columns: list[str]) -> GenSchema:
    cols = {"id": {"type": "integer", "generator": {"strategy": "sequence"}}}
    cols.update({k: {"type": "string", "generator": v} for k, v in columns.items()})
    for name, col in cols.items():
        col["name"] = name
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 1},
        "tables": {"t": {"name": "t", "primary_key": ["id"], "columns": cols}},
        "generation": {
            "scale": "s",
            "scales": {"s": {"t": 2000}},
            "output": {
                CM.OUTPUT_KEY: {
                    "format": CM.FORMAT,
                    "version": 1,
                    "tables": {
                        "t": {
                            "columns": spec_columns,
                            "numeric": [],
                            "categories": {c: ["a", "b", "c"] for c in spec_columns},
                            "correlation": np.eye(len(spec_columns)).tolist(),
                        }
                    },
                }
            },
        },
    }
    return GenSchema.from_dict(doc)


def _enum(values: list[str]) -> dict[str, Any]:
    return {"strategy": "weighted_enum", "values": {v: 1 for v in values}}


def test_keys_derived_columns_and_the_columns_they_read_are_left_alone() -> None:
    schema = _schema(
        {
            "a": _enum(["a", "b", "c"]),
            "b": _enum(["a", "b", "c"]),
            "src": _enum(["a", "b", "c"]),
            "derived_from_src": {"strategy": "derived", "source": "src", "rule": "upper"},
            "order_id": _enum(["a", "b", "c"]),
        },
        ["a", "b", "src", "derived_from_src", "order_id", "id"],
    )
    assert CM.ordered_columns(schema, "t") == ["a", "b"]


def test_fewer_than_two_free_columns_orders_nothing() -> None:
    schema = _schema({"a": _enum(["a", "b", "c"]), "k_id": _enum(["a", "b", "c"])}, ["a", "k_id"])
    assert CM.ordered_columns(schema, "t") == []
    assert CM.ordered_groups(schema, "t") == {}


def test_a_column_drawn_from_another_by_a_conditional_table_moves_with_it() -> None:
    table = {"x": {"p": {"a": 1.0}, "q": {"b": 1.0}}}
    del table
    schema = _schema(
        {
            "a": _enum(["a", "b", "c"]),
            "b": _enum(["a", "b", "c"]),
            "c": {
                "strategy": "conditional_table",
                "source_column": "a",
                "table": {"a": {"a": 1.0}, "b": {"b": 1.0}, "c": {"c": 1.0}},
                "values": {"a": 1, "b": 1, "c": 1},
            },
        },
        ["a", "b", "c"],
    )
    assert CM.ordered_groups(schema, "t") == {"a": ["c"], "b": []}


def test_a_group_is_held_when_a_member_is_read_by_another_column() -> None:
    schema = _schema(
        {
            "a": _enum(["a", "b", "c"]),
            "b": _enum(["a", "b", "c"]),
            "c": {
                "strategy": "conditional_table",
                "source_column": "a",
                "table": {"a": {"a": 1.0}},
                "values": {"a": 1},
            },
            "d": {"strategy": "derived", "source": "c", "rule": "upper"},
        },
        ["a", "b", "c", "d"],
    )
    groups = CM.ordered_groups(schema, "t")
    assert "a" not in groups  # c, a member of its group, is read by d


def test_pairs_of_the_numeric_copula_that_touch_an_ordered_column_are_dropped() -> None:
    pairs = [["a", "b", 0.9], ["b", "c", 0.8], ["c", "d", 0.7]]
    assert CM.without_pairs(pairs, ["b"]) == [["c", "d", 0.7]]
    assert CM.without_pairs(pairs, []) == pairs


# --- the block is a persisted format -------------------------------------------------------------


def test_the_block_round_trips_through_the_schema_file(profile: Any, tmp_path: Path) -> None:
    schema = fit_schema(profile, mixed_copula=True).schema
    path = tmp_path / "s.json"
    path.write_text(json.dumps(schema.to_dict()))
    again = GenSchema.from_dict(json.loads(path.read_text()))
    assert again.generation.output[CM.OUTPUT_KEY] == schema.generation.output[CM.OUTPUT_KEY]
    a = Engine(schema, scale=PRESET, seed=2).generate()
    b = Engine(again, scale=PRESET, seed=2).generate()
    name = next(iter(a.tables))
    assert a.tables[name].equals(b.tables[name])


def test_a_block_of_a_newer_version_or_another_format_is_refused(profile: Any) -> None:
    schema = fit_schema(profile, mixed_copula=True).schema
    block = schema.generation.output[CM.OUTPUT_KEY]
    table = next(iter(schema.tables))
    for change, message in (
        ({"version": 2}, "upgrade Shape"),
        ({"version": "1"}, "integer"),
        ({"format": "other"}, "format"),
    ):
        schema.generation.output[CM.OUTPUT_KEY] = {**block, **change}
        with pytest.raises(ValueError, match=message):
            CM.ordered_columns(schema, table)
    schema.generation.output[CM.OUTPUT_KEY] = block
    assert CM.ordered_columns(schema, table)


def test_the_profile_entry_of_another_version_is_not_used(profile: Any) -> None:
    other = profile.to_dict()
    for t in _tables_of(other):
        t["joint"]["copula"]["version"] = 2
    assert CM.OUTPUT_KEY not in fit_schema(other, mixed_copula=True).schema.generation.output


# --- the other ways to run it --------------------------------------------------------------------


def test_a_schema_with_the_block_needs_the_whole_table(profile: Any) -> None:
    from shape.generation.output import needs_post_pass

    assert not needs_post_pass(fit_schema(profile).schema)
    assert needs_post_pass(fit_schema(profile, mixed_copula=True).schema)


def test_nulls_stay_where_they_are_and_the_non_null_values_are_reordered() -> None:
    rng = np.random.default_rng(8)
    n = 6000
    z = rng.multivariate_normal([0, 0], [[1, 0.8], [0.8, 1]], n)
    x = z[:, 0].copy()
    x[rng.random(n) < 0.1] = np.nan
    g = np.array(["p", "q", "r"])[np.searchsorted(np.quantile(z[:, 1], [0.5, 0.8]), z[:, 1])]
    t = pa.table({"x": pa.array(x, from_pandas=True), "g": pa.array(g)})
    plain, mixed = (
        _generate(shape.profile(t, multivariate=True)),
        _generate(shape.profile(t, multivariate=True), mixed_copula=True),
    )
    nulls = [i for i, v in enumerate(plain["x"]) if v is None]
    assert [i for i, v in enumerate(mixed["x"]) if v is None] == nulls
    assert sorted(v for v in mixed["x"] if v is not None) == sorted(
        v for v in plain["x"] if v is not None
    )
    assert _eta(mixed["g"], [0.0 if v is None else v for v in mixed["x"]]) > 0.3


def test_the_command_line_flag(tmp_path: Path, profile: Any) -> None:
    from shape.cli.main import main

    shape.save(profile, str(tmp_path / "p.shape"), capture="full")
    outs = {}
    for flag in ([], ["--mixed-copula"]):
        out = tmp_path / ("mixed" if flag else "plain")
        code = main(
            [
                "generate",
                "--from",
                str(tmp_path / "p.shape"),
                "--seed",
                "3",
                "-f",
                "csv",
                "-o",
                str(out),
                *flag,
            ]
        )
        assert code == 0
        outs[bool(flag)] = out
    import pyarrow.csv as pcsv

    a = pcsv.read_csv(next(outs[False].rglob("*.csv")))
    b = pcsv.read_csv(next(outs[True].rglob("*.csv")))
    assert a["customer_id"].equals(b["customer_id"])
    assert _eta(b["segment"].to_pylist(), b["spend"].to_pylist()) > 0.4
    assert _eta(a["segment"].to_pylist(), a["spend"].to_pylist()) < 0.05
    assert Counter(a["spend"].to_pylist()) == Counter(b["spend"].to_pylist())


def test_the_flag_needs_a_profile_and_plan_takes_it(
    tmp_path: Path, profile: Any, capsys: Any
) -> None:
    from shape.cli.main import main

    assert main(["generate", "retail", "--mixed-copula", "--dry-run"]) != 0
    shape.save(profile, str(tmp_path / "p.shape"), capture="full")
    assert main(["plan", str(tmp_path / "p.shape"), "--mixed-copula"]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out[out.index("{") :])
    status = {
        i["evidence"].split(".joint.")[-1]: i["status"]
        for i in plan["items"]
        if "joint." in i["evidence"]
    }
    assert status["copula"] == "approximate"
    assert main(["plan", str(tmp_path / "p.shape")]) == 0
    out = capsys.readouterr().out
    plan = json.loads(out[out.index("{") :])
    status = {
        i["evidence"].split(".joint.")[-1]: i["status"]
        for i in plan["items"]
        if "joint." in i["evidence"]
    }
    assert status["copula"] == "not_modelled"


def test_a_table_the_copula_changes_is_written_once_it_is_final(tmp_path: Path) -> None:
    """The writer receives a table the numeric or the mixed copula reorders after the post-passes
    (it used to be held back forever, so ``-f csv`` wrote an empty directory)."""
    import pyarrow.csv as pcsv

    from shape.generation.output import write_engine

    rng = np.random.default_rng(1)
    z = rng.multivariate_normal([0, 0], [[1, 0.8], [0.8, 1]], 3000)
    prof = shape.profile(
        pa.table({"x": pa.array(z[:, 0]), "y": pa.array(z[:, 1])}), joint=False, multivariate=True
    )
    for mixed in (False, True):
        schema = fit_schema(prof, mixed_copula=mixed).schema
        assert schema.correlated_columns
        out = tmp_path / f"numeric-{mixed}"
        paths = write_engine(Engine(schema, scale=PRESET, seed=3), "csv", out)
        assert len(paths) == 1 and paths[0].exists()
        written = pcsv.read_csv(paths[0])
        assert written.num_rows == 3000
        assert (
            abs(np.corrcoef(written["x"].to_pylist(), written["y"].to_pylist())[0, 1] - 0.8) < 0.05
        )
