"""Item 4 with Presidio installed: real analysis, with no model download."""

from __future__ import annotations

import logging

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from shape_integrations import presidio_detector as pd

from shape.plugins import kit

pytest.importorskip("presidio_analyzer", reason="needs the 'presidio' extra")
pytestmark = pytest.mark.integration

EMAILS = [f"user{i}@example.org" for i in range(40)]
WORDS = ["apple", "banana", "cherry", "plum"] * 10


def test_it_passes_the_conformance_kit():
    kit.check_detector(pd.PresidioDetector(), positives=[EMAILS], negatives=[WORDS])


def test_an_email_column_is_labelled_email_with_full_confidence():
    d = pd.PresidioDetector().detect(pa.array(EMAILS), "contact")
    assert d is not None and d.label == "email"
    assert d.confidence == pytest.approx(1.0)


def test_card_numbers_and_ibans_are_labelled():
    cards = pa.array(["4111 1111 1111 1111", "5500 0000 0000 0004"] * 10)
    assert pd.PresidioDetector().detect(cards, "pan").label == "credit_card"
    ibans = pa.array(["GB82WEST12345698765432", "DE89370400440532013000"] * 10)
    assert pd.PresidioDetector().detect(ibans, "acct").label == "iban"


def test_an_unmapped_entity_type_gives_the_prefixed_label():
    urls = pa.array([f"https://example.org/page/{i}" for i in range(30)])
    d = pd.PresidioDetector().detect(urls, "link")
    assert d is not None and d.label == "presidio:URL"


def test_confidence_is_the_share_of_sampled_values_detected_times_the_mean_score():
    mixed = pa.array(EMAILS[:20] + WORDS[:20])
    d = pd.PresidioDetector().detect(mixed, "mixed")
    assert d is not None and d.label == "email"
    assert d.confidence == pytest.approx(0.5, abs=0.06)


def test_plain_words_give_none():
    assert pd.PresidioDetector().detect(pa.array(WORDS), "fruit") is None


def test_only_a_bounded_sample_is_analyzed(monkeypatch):
    monkeypatch.setenv("SHAPE_PRESIDIO_SAMPLE", "30")
    detector = pd.PresidioDetector()
    calls = []
    real = pd.analyzer().analyze
    monkeypatch.setattr(
        pd.analyzer(), "analyze", lambda **kw: calls.append(kw["text"]) or real(**kw)
    )
    values = pa.array([f"person{i}@example.org" for i in range(1000)])
    assert detector.detect(values, "email") is not None
    assert len(calls) == 30


def test_the_answer_is_the_same_every_time():
    values = pa.array([f"u{i}@x.io" if i % 3 else f"word{i}" for i in range(500)])
    d = pd.PresidioDetector()
    assert d.detect(values, "c") == d.detect(values, "c")


def test_values_are_never_logged_printed_or_in_errors(monkeypatch, caplog, capsys):
    secret = "zebra.9137@secret-company.org"
    caplog.set_level(logging.DEBUG)
    d = pd.PresidioDetector().detect(pa.array([secret] * 5), "email")
    assert d is not None
    out = capsys.readouterr()
    assert secret not in out.out + out.err + caplog.text

    def boom(**kw):
        raise RuntimeError(f"cannot analyze {kw['text']}")

    monkeypatch.setattr(pd.analyzer(), "analyze", boom)
    with pytest.raises(pd.AnalysisError) as info:
        pd.PresidioDetector().detect(pa.array([secret] * 5), "email")
    assert secret not in str(info.value) and secret not in repr(info.value.__cause__)
    assert "email" in str(info.value)


def test_no_model_is_downloaded(monkeypatch):
    """The engine is built without Presidio's own download step."""
    import spacy.cli

    def refuse(*a, **k):
        raise AssertionError("tried to download a model")

    monkeypatch.setattr(spacy.cli, "download", refuse)
    pd.reset()
    assert pd.PresidioDetector().detect(pa.array(EMAILS), "e") is not None


def test_a_named_model_that_is_not_installed_is_an_error_not_a_download(monkeypatch):
    monkeypatch.setenv("SHAPE_PRESIDIO_MODEL", "no_such_model_xyz")
    pd.reset()
    with pytest.raises(pd.AnalysisError, match="no_such_model_xyz"):
        pd.PresidioDetector().detect(pa.array(EMAILS), "e")
    pd.reset()
