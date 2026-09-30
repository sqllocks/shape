"""Profile.to_html: self-contained report."""

from __future__ import annotations

import re

import pyarrow as pa

import shape


def test_html_is_self_contained(orders):
    doc = shape.profile(orders, name="orders").to_html()
    assert doc.startswith("<!doctype html>")
    for col in orders.column_names:
        assert col in doc
    # no network assets: no http(s) URL in any src/href attribute, and nothing else external
    assert not re.findall(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", doc, re.I)
    assert "http" not in doc.lower()
    assert "<script" not in doc.lower()
    assert "<link" not in doc.lower()
    assert "@import" not in doc and "url(" not in doc
    assert "<svg" in doc  # distributions rendered inline


def test_html_shows_keys_patterns_and_distributions(orders, customers):
    doc = shape.profile({"orders": orders, "customer": customers}).to_html()
    assert "PK" in doc
    assert "FK → customer" in doc
    assert "email" in doc  # detected pattern
    assert "lognormal" in doc
    assert "Relationships" in doc


def test_html_escapes_values():
    t = pa.table({"<b>x</b>": ["<script>alert(1)</script>", "a"] * 10})
    doc = shape.profile(t, name="<i>n</i>").to_html()
    assert "<script>alert" not in doc
    assert "&lt;script&gt;" in doc
    assert "<b>x</b>" not in doc


def test_html_for_constant_null_and_empty_columns():
    t = pa.table({"n": pa.array([None] * 5, pa.float64()), "c": [1] * 5})
    doc = shape.profile(t).to_html()
    assert "</html>" in doc
