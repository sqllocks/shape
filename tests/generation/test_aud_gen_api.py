"""AUD-gen: small public API errors (#211)."""

from __future__ import annotations

import pytest


def test_no_evidence_reaches_no_level():
    # 211: assess_fidelity(set()) returned 'bronze', which needs schema and nullability.
    from shape.generation import assess_fidelity

    assert assess_fidelity(set()) == "none"
    assert assess_fidelity({"schema", "nullability"}) == "bronze"


def test_the_summary_format_is_refused_with_a_message_that_does_not_offer_it():
    # 211: "unknown format 'summary'; choose one of summary, csv, ...".
    from shape.generation.output import _check_format

    with pytest.raises(ValueError) as exc:
        _check_format("summary")
    choices = str(exc.value).split("choose one of", 1)[1]
    assert "summary" not in choices


def test_a_negative_seed_is_not_its_positive_twin():
    # 211: random.Random seeds with abs(): seed 1 and -1 gave the same row 0.
    from shape.generation.strategies import GenerationPlan, Uniform

    def row(seed):
        return GenerationPlan((("x", Uniform(0, 1)),), seed=seed).row_at(0)["x"]

    assert row(1) != row(-1)
    assert row(1) == row(1)
