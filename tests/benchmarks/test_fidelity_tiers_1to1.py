"""P4-11: the fidelity tiers equal the baseline's, field by field.

``benchmarks/vs_refengine/fidelity_tiers_1to1/fixtures/expected_tiers.json`` holds what the
baseline's tiers gave for the pairs of ``tiers_golden_data.py`` (``golden.py --check`` proves the
file still matches them). Shape's output must equal it under the harness's rules
(``tiers_common.compare``): integers, names and flags equal, floats within 1e-9 relative, and the
adversarial AUC and accuracy and every mixture-fit field within 0.02. The pairs cover numbers with
nulls, integers, a two-component mixture, booleans, categoricals with nulls, e-mails, ZIP codes
and UUIDs, long text, timestamps, dates and decimals read as text, an all-null column, a periodic
series, a 12-row table, a table above every sampling cap, and an anomaly flag. The parts that need
scikit-learn run when it is installed; the rest never need it. Nothing here needs the baseline's
venv; ``fidelity_tiers_1to1/run.py`` does the same comparison on retail medium.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine" / "fidelity_tiers_1to1"
sys.path.insert(0, str(BENCH))

import tiers_common  # noqa: E402
import tiers_golden_data  # noqa: E402

SEED, SMALL_ROWS = 7, 200
_FIXTURE = json.loads((BENCH / "fixtures" / "expected_tiers.json").read_text())
EXPECTED = _FIXTURE["tables"]
SPREAD = _FIXTURE["spread"]
REAL, SYNTH = tiers_golden_data.tables()
NAMES = sorted(EXPECTED)
SKLEARN_KEYS = ("tier1", "tier1_single")


@pytest.fixture(scope="module")
def plain():
    """Everything but tier 1 (needs no scikit-learn)."""
    return {
        n: tiers_common.shape_tiers(
            n, REAL[n], SYNTH[n], tier1=False, small_rows=SMALL_ROWS, seed=SEED
        )
        for n in NAMES
    }


@pytest.fixture(scope="module")
def with_tier1():
    pytest.importorskip("sklearn")
    return {
        n: tiers_common.shape_tiers(
            n, REAL[n], SYNTH[n], tier1=True, small_rows=SMALL_ROWS, seed=SEED
        )
        for n in NAMES
    }


def _expect(name, *keys):
    return {k: v for k, v in EXPECTED[name].items() if k in keys}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("part", ["tier2", "tier3"])
def test_tier_2_and_3_equal_the_baseline(plain, name, part):
    d = tiers_common.compare(plain[name][part], EXPECTED[name][part], f"/{name}/{part}")
    assert d.ok, d.mismatches[:5]
    assert d.compared > 0
    assert d.max_tight < 1e-9


@pytest.mark.parametrize("name", [n for n in NAMES if "bootstrap" in EXPECTED[n]])
def test_bootstrap_and_noise_for_a_seed_equal_the_baseline(plain, name):
    d = tiers_common.compare(
        {k: plain[name][k] for k in ("bootstrap", "dp")},
        _expect(name, "bootstrap", "dp"),
        f"/{name}",
    )
    assert d.ok, d.mismatches[:5]
    assert d.compared > 1000 and d.max_tight == 0.0  # the same draws, bit for bit


def test_the_small_tables_are_the_ones_with_bootstrap_and_noise():
    assert [n for n in NAMES if "bootstrap" in EXPECTED[n]] == ["mini", "tiny"]


@pytest.mark.parametrize("name", NAMES)
def test_tier_1_equals_the_baseline(with_tier1, name):
    d = tiers_common.compare(
        {k: with_tier1[name][k] for k in SKLEARN_KEYS if k in EXPECTED[name]},
        _expect(name, *SKLEARN_KEYS),
        f"/{name}",
        spread=SPREAD,
    )
    assert d.ok, d.mismatches[:5]
    # the mixture fits are within 0.02; the adversarial fields follow the baseline's hash seed, so
    # they are within 0.02 of the baseline's output or of what it gives under another hash seed
    assert max((v for k, v in d.loose.items() if "/adversarial/" not in k), default=0) <= 0.02


def test_the_mixture_fits_are_equal_not_merely_close(with_tier1):
    fits = with_tier1["mixed"]["tier1"]["gmm_fits"]
    want = EXPECTED["mixed"]["tier1"]["gmm_fits"]
    assert set(fits) == set(want) and "bimodal" in fits
    for col, f in fits.items():
        assert f["n_components"] == want[col]["n_components"]
        assert f["bic"] == pytest.approx(want[col]["bic"], rel=1e-6)


def test_every_golden_column_is_covered():
    cols = {c for t in REAL.values() for c in t.column_names}
    assert {
        "amount",
        "bimodal",
        "flag",
        "segment",
        "email",
        "empty",
        "wave",
        "day",
        "price",
    } <= cols
    anomaly = EXPECTED["mixed"]["tier2"]["anomaly_rate"]
    assert anomaly is not None and anomaly["anomaly_count"] > 0


def test_the_comparison_can_fail(plain):
    t = json.loads(json.dumps(plain["mixed"]))
    t["tier2"]["cardinality"]["qty"]["synth_cardinality"] += 1
    assert not tiers_common.compare(t["tier2"], EXPECTED["mixed"]["tier2"]).ok
    t = json.loads(json.dumps(plain["tiny"]))
    t["dp"]["laplace"]["frame"]["v"][0] += 1e-6
    assert not tiers_common.compare(t["dp"], EXPECTED["tiny"]["dp"]).ok
    t = json.loads(json.dumps(plain["mixed"]))
    t["tier3"]["drift"]["columns"]["amount"]["is_drifted"] ^= True
    assert not tiers_common.compare(t["tier3"], EXPECTED["mixed"]["tier3"]).ok
