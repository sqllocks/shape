"""Tier 2 fidelity: formats, strings, cardinality and injected anomalies.

* **format preservation**: the dominant format of a text column in the reference data (email,
  US phone, UUID, URL, IPv4, US ZIP, ISO date, US SSN, card number) and how often the synthetic
  column keeps it;
* **string similarity**: cosine similarity of the character-trigram counts of the two columns;
* **cardinality**: whether the synthetic column has about as many distinct values as the
  reference (within 20%);
* **anomaly rate**: whether the share of rows flagged in ``_shape_is_anomaly`` is the expected one.

Pure numpy and pyarrow. A port of the reference implementation's tier 2 (checked by the parity
harness under ``benchmarks/`` in the repository); the flag column is Shape's own name, and a
table that carries it is not scored on columns that start with ``_shape_``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from ._frame import Column, Frame, as_frame, sample_positions
from .tier1 import clean

ANOMALY_COLUMN = "_shape_is_anomaly"
INTERNAL_PREFIX = "_shape_"

FORMAT_PATTERNS: dict[str, re.Pattern[str]] = {
    # local@domain, the domain holding a dot that is neither its first nor its last character.
    # The possessive run finds the first dot after the domain's first character without
    # backtracking (the plain form was quadratic on long dotted values).
    "email": re.compile(r"^[^@\s]+@[^@\s][^@\s.]*+\.[^@\s]+$"),
    "phone_us": re.compile(r"^\+?1?\s*[\-.]?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}$"),
    "uuid": re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
    "url": re.compile(r"^https?://\S+"),
    "ipv4": re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
    "zip_us": re.compile(r"^\d{5}(-\d{4})?$"),
    "date_iso": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "ssn_us": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    "credit_card": re.compile(r"^\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}$"),
}


@dataclass
class FormatPreservationResult:
    column: str
    detected_format: str | None
    real_format_rate: float
    synth_format_rate: float
    delta: float
    passed: bool


@dataclass
class StringSimilarityResult:
    column: str
    ngram_n: int
    cosine_similarity: float
    score: float


@dataclass
class CardinalityConstraintResult:
    column: str
    real_cardinality: int
    synth_cardinality: int
    ratio: float
    deviation: float
    passed: bool


@dataclass
class AnomalyRateResult:
    expected_fraction: float
    actual_fraction: float
    delta: float
    row_count: int
    anomaly_count: int
    passed: bool


def _text_columns(real: Frame, synthetic: Frame) -> list[Column]:
    """Columns that are neither numeric nor timestamps in ``real`` and exist in ``synthetic``."""
    return [c for c in real if not c.is_numeric and not c.is_datetime and c.name in synthetic]


def _sample(strings: list[str], size: int) -> list[str]:
    if len(strings) > size:
        return [strings[i] for i in sample_positions(len(strings), size)]
    return strings


def analyze_format_preservation(
    real: pa.Table | Frame,
    synthetic: pa.Table | Frame,
    threshold: float = 0.10,
    sample_size: int = 500,
) -> dict[str, FormatPreservationResult]:
    """Detect each text column's dominant format in ``real`` and compare ``synthetic`` to it."""
    r, s = as_frame(real), as_frame(synthetic)
    results: dict[str, FormatPreservationResult] = {}
    for col in _text_columns(r, s):
        real_sample = col.text()
        synth_sample = s[col.name].text()
        if not real_sample or not synth_sample:
            continue
        real_sample = _sample(real_sample, sample_size)
        synth_sample = _sample(synth_sample, sample_size)
        best: str | None = None
        best_rate = 0.0
        for name, pattern in FORMAT_PATTERNS.items():
            rate = sum(1 for v in real_sample if pattern.match(v)) / len(real_sample)
            if rate > best_rate:
                best, best_rate = name, rate
        if best is None or best_rate < 0.5:
            continue
        pattern = FORMAT_PATTERNS[best]
        synth_rate = sum(1 for v in synth_sample if pattern.match(v)) / len(synth_sample)
        delta = abs(best_rate - synth_rate)
        results[col.name] = FormatPreservationResult(
            column=col.name,
            detected_format=best,
            real_format_rate=best_rate,
            synth_format_rate=synth_rate,
            delta=delta,
            passed=delta <= threshold,
        )
    return results


def _char_ngrams(text: str, n: int) -> dict[str, int]:
    grams: dict[str, int] = {}
    for i in range(len(text) - n + 1):
        gram = text[i : i + n]
        grams[gram] = grams.get(gram, 0) + 1
    return grams


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    keys = sorted(set(a) | set(b))
    if not keys:
        return 1.0
    va = np.array([a.get(k, 0) for k in keys], dtype=float)
    vb = np.array([b.get(k, 0) for k in keys], dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def analyze_string_similarity(
    real: pa.Table | Frame,
    synthetic: pa.Table | Frame,
    ngram_n: int = 3,
    sample_size: int = 1000,
) -> dict[str, StringSimilarityResult]:
    """Character n-gram cosine similarity of every shared text column."""
    r, s = as_frame(real), as_frame(synthetic)
    results: dict[str, StringSimilarityResult] = {}
    for col in _text_columns(r, s):
        real_vals = col.text()
        synth_vals = s[col.name].text()
        if len(real_vals) < 10 or len(synth_vals) < 10:
            continue
        real_vals = _sample(real_vals, sample_size)
        synth_vals = _sample(synth_vals, sample_size)
        sim = _cosine(
            _char_ngrams(" ".join(real_vals), ngram_n), _char_ngrams(" ".join(synth_vals), ngram_n)
        )
        results[col.name] = StringSimilarityResult(
            column=col.name, ngram_n=ngram_n, cosine_similarity=sim, score=round(sim * 100, 2)
        )
    return results


def check_cardinality(
    real: pa.Table | Frame, synthetic: pa.Table | Frame, max_deviation: float = 0.20
) -> dict[str, CardinalityConstraintResult]:
    """Distinct-value counts of every shared column, synthetic against reference."""
    r, s = as_frame(real), as_frame(synthetic)
    results: dict[str, CardinalityConstraintResult] = {}
    for col in r:
        if col.name not in s or col.name.startswith(INTERNAL_PREFIX):
            continue
        real_card = col.nunique()
        synth_card = s[col.name].nunique()
        if real_card == 0:
            continue
        ratio = synth_card / real_card
        deviation = abs(1.0 - ratio)
        results[col.name] = CardinalityConstraintResult(
            column=col.name,
            real_cardinality=real_card,
            synth_cardinality=synth_card,
            ratio=round(ratio, 4),
            deviation=round(deviation, 4),
            passed=deviation <= max_deviation,
        )
    return results


def check_anomaly_rates(
    data: pa.Table | Frame,
    expected_fractions: dict[str, float] | None = None,
    tolerance: float = 0.05,
) -> AnomalyRateResult | None:
    """The share of rows flagged in ``_shape_is_anomaly``, against the sum of
    ``expected_fractions`` (0 when none are given). ``None`` when the table has no such column."""
    frame = as_frame(data)
    if ANOMALY_COLUMN not in frame:
        return None
    col = frame[ANOMALY_COLUMN]
    row_count = len(frame)
    flagged = (
        int(np.sum(col.values[col.valid].astype(np.int64)))
        if col.is_numeric
        else int(sum(1 for v in col.present() if v))
    )
    actual = flagged / row_count if row_count > 0 else 0.0
    expected = sum(expected_fractions.values()) if expected_fractions else 0.0
    delta = abs(actual - expected)
    return AnomalyRateResult(
        expected_fraction=expected,
        actual_fraction=round(actual, 4),
        delta=round(delta, 4),
        row_count=row_count,
        anomaly_count=flagged,
        passed=delta <= tolerance,
    )


@dataclass
class Tier2Report:
    """The four tier 2 checks of one table."""

    format_preservation: dict[str, FormatPreservationResult] = field(default_factory=dict)
    string_similarity: dict[str, StringSimilarityResult] = field(default_factory=dict)
    cardinality: dict[str, CardinalityConstraintResult] = field(default_factory=dict)
    anomaly_rate: AnomalyRateResult | None = None

    def passing_rate(self) -> float:
        """Share of the format, cardinality and anomaly checks that passed (1.0 with none)."""
        checks = [r.passed for r in self.format_preservation.values()]
        checks += [r.passed for r in self.cardinality.values()]
        if self.anomaly_rate is not None:
            checks.append(self.anomaly_rate.passed)
        return sum(checks) / len(checks) if checks else 1.0

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["passing_rate"] = self.passing_rate()
        return dict(clean(out))

    def summary(self) -> str:
        lines = ["Tier 2 fidelity report", "=" * 50, f"Passing rate: {self.passing_rate():.1%}"]
        if self.format_preservation:
            lines.append("\nFormat preservation:")
            for col, f in self.format_preservation.items():
                lines.append(
                    f"  [{'PASS' if f.passed else 'FAIL'}] {col}: {f.detected_format} "
                    f"reference={f.real_format_rate:.2%} synthetic={f.synth_format_rate:.2%}"
                )
        if self.string_similarity:
            lines.append("\nString similarity (trigram cosine):")
            for col, st in self.string_similarity.items():
                lines.append(f"  {col}: {st.cosine_similarity:.3f} ({st.score:.1f}/100)")
        if self.cardinality:
            lines.append("\nCardinality:")
            for col, c in self.cardinality.items():
                lines.append(
                    f"  [{'PASS' if c.passed else 'FAIL'}] {col}: reference={c.real_cardinality} "
                    f"synthetic={c.synth_cardinality} ratio={c.ratio:.3f}"
                )
        if self.anomaly_rate:
            a = self.anomaly_rate
            lines.append(
                f"\nAnomaly rate: [{'PASS' if a.passed else 'FAIL'}] "
                f"expected={a.expected_fraction:.2%} actual={a.actual_fraction:.2%}"
            )
        return "\n".join(lines)


def run_tier2(
    real: pa.Table | Frame,
    synthetic: pa.Table | Frame,
    expected_anomaly_fractions: dict[str, float] | None = None,
) -> Tier2Report:
    """All tier 2 checks of ``synthetic`` against ``real``."""
    r, s = as_frame(real), as_frame(synthetic)
    return Tier2Report(
        format_preservation=analyze_format_preservation(r, s),
        string_similarity=analyze_string_similarity(r, s),
        cardinality=check_cardinality(r, s),
        anomaly_rate=check_anomaly_rates(s, expected_anomaly_fractions),
    )
