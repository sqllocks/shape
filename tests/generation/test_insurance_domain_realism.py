"""The insurance domain's dates, amounts and policy lines hold together (ISS-gen).

The baseline's insurance schema drew claim, payment and underwriting dates independently over the
whole date range, filed every claim exactly 7 days after its incident, let payments exceed the
claim, drew a policy type's category independently of its name, and carried its two date rules
with empty text, so nothing enforced or reported them. The corrections are listed in
``benchmarks/vs_refengine/domain_1to1/domain_corrections.py``; these tests check the data.

Every scale preset is generated. The presets differ only in the number of policyholders; a preset
whose policy table would pass ``TRUE_SIZE_POLICIES`` rows (``xlarge``, ``xxl``, ``xxxl``: 9, 36
and 180 million policies) is generated with the same schema and preset but its policyholder count
capped, and its test id says so. Every date is computed per row from its own row and its parent's
(``derived`` with a parent read by key), so the ordering does not depend on the number of rows,
the chunk size or the rule repairs, which ``test_the_dates_hold_by_construction_in_chunks``
checks without them.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.generation.domains import load_domain
from shape.generation.engine import Engine
from shape.generation.rules import validate_rules

BENCH = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine" / "domain_1to1"
sys.path.insert(0, str(BENCH))

import domain_corrections  # noqa: E402

POLICIES_PER_HOLDER = 1.8
TRUE_SIZE_POLICIES = 2_000_000
CAPPED_HOLDERS = 100_000
PRESETS = list(load_domain("insurance").schema.generation.scales)


def _days(values: pa.ChunkedArray | pa.Array) -> np.ndarray:
    """Whole days since 1970 of a date or timestamp column (a finished table has dates, a
    generated one timestamps with the time of day)."""
    nanos = np.asarray(pc.cast(pc.cast(values, pa.timestamp("ns")), pa.int64()))
    return nanos // 86_400_000_000_000


def _by_key(keys: pa.ChunkedArray, wanted: pa.ChunkedArray, values: pa.ChunkedArray) -> np.ndarray:
    """``values`` at the row of ``keys`` equal to each ``wanted`` key (every key has a row)."""
    position = pc.index_in(wanted, value_set=keys.combine_chunks())
    assert position.null_count == 0
    return _days(pc.take(values, position))


def _pairs(table: pa.Table, key: str, value: str) -> dict:
    return dict(zip(table[key].to_pylist(), table[value].to_pylist(), strict=True))


def _schema_at(preset: str, mode: str = "3nf"):
    """The insurance schema at ``preset``, with its policyholders capped when the preset is too
    large to generate in a test run; the second value says whether it was capped."""
    schema = copy.deepcopy(load_domain("insurance", mode=mode).schema)
    holders = schema.generation.scales[preset]["policyholder"]
    capped = holders * POLICIES_PER_HOLDER > TRUE_SIZE_POLICIES
    if capped:
        schema.generation.scales[preset]["policyholder"] = CAPPED_HOLDERS
    return schema, capped


@pytest.fixture(
    scope="module",
    params=PRESETS,
    ids=[
        f"{p}-capped" if _schema_at(p)[1] else p
        for p in PRESETS  # the id says when a preset ran with fewer policyholders
    ],
)
def run(request):
    schema, _ = _schema_at(request.param)
    return schema, Engine(schema, scale=request.param, seed=1042).generate()


def _assert_dates_in_order(tables: dict[str, pa.Table]) -> None:
    policy, claim = tables["policy"], tables["claim"]
    effective = _by_key(policy["policy_id"], claim["policy_id"], policy["effective_date"])
    expires = _by_key(policy["policy_id"], claim["policy_id"], policy["expiration_date"])
    incident, filed = _days(claim["incident_date"]), _days(claim["filed_date"])
    assert claim.num_rows > 0
    assert (incident >= effective).all(), "a claim's incident before its policy took effect"
    assert (incident < expires).all(), "a claim's incident after its policy expired"
    assert (filed >= incident).all(), "a claim filed before its incident"
    assert (filed >= effective).all(), "a claim filed before its policy took effect"
    payment = tables["claim_payment"]
    claim_filed = _by_key(claim["claim_id"], payment["claim_id"], claim["filed_date"])
    assert (_days(payment["payment_date"]) > claim_filed).all(), "a payment before the filing"


def test_no_claim_is_filed_before_its_incident_or_dated_before_its_policy(run):
    _, result = run
    _assert_dates_in_order(result.tables)
    premium, under, policy = (
        result.tables[t] for t in ("premium_payment", "underwriting", "policy")
    )
    start = _by_key(policy["policy_id"], premium["policy_id"], policy["effective_date"])
    end = _by_key(policy["policy_id"], premium["policy_id"], policy["expiration_date"])
    paid = _days(premium["payment_date"])
    assert ((paid >= start) & (paid < end)).all(), "a premium paid outside the policy's term"
    effective = _by_key(policy["policy_id"], under["policy_id"], policy["effective_date"])
    assert (_days(under["assessed_date"]) < effective).all(), "underwriting after the policy began"


def test_the_payments_of_a_claim_do_not_exceed_it(run):
    _, result = run
    claim, payment = result.tables["claim"], result.tables["claim_payment"]
    paid = payment.group_by("claim_id").aggregate([("payment_amount", "sum")])
    total = _pairs(paid, "claim_id", "payment_amount_sum")
    amounts = _pairs(claim, "claim_id", "claim_amount")
    recorded = _pairs(claim, "claim_id", "total_paid")
    assert total, "no claim has a payment"
    for claim_id, amount in amounts.items():
        assert recorded[claim_id] == pytest.approx(total.get(claim_id, 0.0), abs=0.01)
        assert total.get(claim_id, 0.0) <= amount + 1e-9, claim_id


def test_every_business_rule_holds_and_none_is_left_unchecked(run):
    schema, result = run
    assert result.remaining_violations == []
    assert validate_rules(result.tables, schema) == []
    assert not [i for i in schema.validate() if i.location.startswith("business_rules.")]
    rules = {r.name: (r.type, r.rule, r.table, r.via) for r in schema.business_rules}
    assert rules["claim_after_policy_effective"] == (
        "cross_table",
        "claim.incident_date >= policy.effective_date",
        None,
        "policy_id",
    )
    assert rules["filed_after_incident"] == (
        "cross_column",
        "filed_date >= incident_date",
        "claim",
        None,
    )


@pytest.mark.parametrize("mode", ["3nf", "star"])
def test_the_dates_hold_by_construction_in_chunks(mode):
    """Without the rule repairs (which run on whole tables), and with chunks far smaller than a
    table, the dates are still in order: a streamed or chunked run gets them right."""
    schema = copy.deepcopy(load_domain("insurance", mode=mode).schema)
    schema.business_rules = []
    engine = Engine(schema, scale="medium", seed=7, chunk_rows=997)
    tables = {t: engine.generate_table(t) for t in ("policy", "claim", "claim_payment")}
    _assert_dates_in_order(tables)


def test_the_same_seed_gives_the_same_tables_and_another_seed_does_not():
    schema = load_domain("insurance").schema
    one = Engine(schema, scale="small", seed=5).generate().tables
    two = Engine(schema, scale="small", seed=5).generate().tables
    other = Engine(schema, scale="small", seed=6).generate().tables
    assert set(one) == set(two)
    for name in one:
        assert one[name].equals(two[name]), name
    for name, column in (("claim", "incident_date"), ("claim", "filed_date"), ("claim", "cause")):
        assert not one[name][column].equals(other[name][column]), (name, column)


@pytest.fixture(scope="module")
def small():
    return Engine(load_domain("insurance").schema, scale="small", seed=1042).generate().tables


def test_the_filing_lag_is_a_distribution_not_a_constant(small):
    claim = small["claim"]
    lag = _days(claim["filed_date"]) - _days(claim["incident_date"])
    assert len(set(lag.tolist())) > 20
    assert 2 <= np.median(lag) <= 10 and lag.min() >= 0 and np.percentile(lag, 95) <= 60


def test_each_policy_type_appears_once_in_its_own_line(small):
    types = small["policy_type"]
    names = types["type_name"].to_pylist()
    assert sorted(names) == sorted(domain_corrections.INSURANCE_LINES)
    for name, category in zip(names, types["category"].to_pylist(), strict=True):
        assert category == domain_corrections.INSURANCE_LINES[name], name
    policy = small["policy"]
    line = _pairs(types, "policy_type_id", "category")
    for type_id, lob in zip(
        policy["policy_type_id"].to_pylist(), policy["line_of_business"].to_pylist(), strict=True
    ):
        assert lob == line[type_id]


def test_claim_causes_and_coverages_follow_the_policy_line(small):
    lob = _pairs(small["policy"], "policy_id", "line_of_business")
    claim, coverage = small["claim"], small["coverage"]
    seen: set[str] = set()
    for policy_id, line, cause in zip(
        claim["policy_id"].to_pylist(),
        claim["line_of_business"].to_pylist(),
        claim["cause"].to_pylist(),
        strict=True,
    ):
        assert line == lob[policy_id]
        assert cause in domain_corrections.INSURANCE_CAUSES[line], (line, cause)
        seen.add(line)
    assert seen == set(domain_corrections.INSURANCE_CAUSES)
    for policy_id, line, kind in zip(
        coverage["policy_id"].to_pylist(),
        coverage["line_of_business"].to_pylist(),
        coverage["coverage_type"].to_pylist(),
        strict=True,
    ):
        assert line == lob[policy_id]
        assert kind in domain_corrections.INSURANCE_COVERAGES[line], (line, kind)


def test_the_claim_categories_are_the_vocabulary_and_peril_types_is_not_shipped():
    definition = load_domain("insurance").definition
    assert set(definition.reference_data) == {
        "claim_categories",
        "policy_types",
        "us_zip_locations",
    }
    categories = set(definition.reference_data["claim_categories"]["name"].to_pylist())
    for line in ("Auto", "Home", "Commercial", "Umbrella"):
        own = {"Workplace Injury", "Professional Error"}  # commercial causes the dataset lacks
        assert set(domain_corrections.INSURANCE_CAUSES[line]) - own <= categories, line
