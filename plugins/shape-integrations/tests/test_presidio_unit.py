"""Item 4 without Presidio: the sample, the label mapping, the confidence and the guards."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from shape_integrations import presidio_detector as pd

MISSING = "Presidio needs the 'presidio' extra: pip install 'sqllocks-shape-integrations[presidio]'"


def test_the_default_sample_is_200_and_the_environment_changes_it(monkeypatch):
    monkeypatch.delenv("SHAPE_PRESIDIO_SAMPLE", raising=False)
    assert pd.sample_size() == 200
    monkeypatch.setenv("SHAPE_PRESIDIO_SAMPLE", "25")
    assert pd.sample_size() == 25
    monkeypatch.setenv("SHAPE_PRESIDIO_SAMPLE", "1")
    assert pd.sample_size() == 1


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "", "1.5", " "])
def test_a_bad_sample_size_is_refused_with_the_variable_name(monkeypatch, bad):
    monkeypatch.setenv("SHAPE_PRESIDIO_SAMPLE", bad)
    with pytest.raises(ValueError, match="SHAPE_PRESIDIO_SAMPLE"):
        pd.sample_size()


def test_the_sample_is_bounded_seeded_and_ignores_nulls_and_blanks():
    values = pa.array([None, "", "  "] + [f"v{i}" for i in range(1000)])
    a = pd.sample_values(values, 50)
    b = pd.sample_values(values, 50)
    assert a == b
    assert len(a) == 50 and len(set(a)) == 50
    assert all(v.startswith("v") for v in a)


def test_a_column_smaller_than_the_sample_is_taken_whole():
    assert sorted(pd.sample_values(pa.array(["x", None, "y"]), 200)) == ["x", "y"]


def test_the_sample_does_not_depend_on_the_column_name_or_batch_layout():
    flat = pa.array([f"v{i}" for i in range(500)])
    chunked = pa.chunked_array([flat.slice(0, 200), flat.slice(200)])
    assert pd.sample_values(flat, 40) == pd.sample_values(chunked, 40)


def test_a_different_sample_size_gives_a_different_sample():
    values = pa.array([f"v{i}" for i in range(1000)])
    assert pd.sample_values(values, 10) != pd.sample_values(values, 11)[:10]


@pytest.mark.parametrize(
    ("entity", "label"),
    [
        ("EMAIL_ADDRESS", "email"),
        ("US_SSN", "us_ssn"),
        ("PHONE_NUMBER", "phone"),
        ("CREDIT_CARD", "credit_card"),
        ("IBAN_CODE", "iban"),
        ("IP_ADDRESS", "ip_address"),
        ("PERSON", "presidio:PERSON"),
        ("URL", "presidio:URL"),
        ("SOMETHING_NEW", "presidio:SOMETHING_NEW"),
    ],
)
def test_entity_types_map_to_labels_and_unmapped_types_are_prefixed(entity, label):
    assert pd.label_for(entity) == label


def test_every_mapped_entity_is_in_the_documented_table():
    from pathlib import Path

    doc = (Path(__file__).parents[3] / "docs" / "plugins" / "integrations.md").read_text()
    for entity, label in pd.ENTITY_LABELS.items():
        assert f"| `{entity}` | `{label}` |" in doc


def test_confidence_is_the_share_detected_times_the_mean_score():
    hits = [{"EMAIL_ADDRESS": 1.0}, {"EMAIL_ADDRESS": 0.5}, {}, {}]
    assert pd.summarize(hits, 4) == ("EMAIL_ADDRESS", pytest.approx(0.5 * 0.75))


def test_the_most_common_entity_wins_then_the_higher_score_then_the_name():
    assert pd.summarize([{"A": 0.9}, {"B": 0.9}, {"B": 0.8}], 3)[0] == "B"
    assert pd.summarize([{"A": 0.5}, {"B": 0.9}], 2)[0] == "B"
    assert pd.summarize([{"B": 0.5}, {"A": 0.5}], 2)[0] == "A"


def test_nothing_detected_is_none():
    assert pd.summarize([{}, {}], 2) is None
    assert pd.summarize([], 0) is None


def test_confidence_stays_within_zero_and_one():
    hits = [{"X": 1.7}, {"X": 3.0}]
    assert pd.summarize(hits, 2) == ("X", 1.0)


def test_non_text_empty_and_all_null_columns_need_no_library(hide_library):
    hide_library("presidio_analyzer")
    d = pd.PresidioDetector()
    assert d.detect(pa.array([1, 2, 3]), "n") is None
    assert d.detect(pa.array([], type=pa.string()), "c") is None
    assert d.detect(pa.array([None, None], type=pa.string()), "c") is None
    assert d.detect(pa.array(["", " "]), "c") is None


def test_a_text_column_without_the_library_names_the_extra(hide_library):
    hide_library("presidio_analyzer")
    with pytest.raises(pd.MissingExtraError) as info:
        pd.PresidioDetector().detect(pa.array(["a@b.com"]), "email")
    assert str(info.value) == MISSING
