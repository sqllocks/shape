"""The ``presidio`` semantic detector (``shape.detectors``).

``detect`` takes a bounded, seeded sample of the non-empty values of a text column (200 by
default; ``SHAPE_PRESIDIO_SAMPLE`` changes it), runs Presidio's analyzer on each value, and
returns the entity type found in the most sampled values as a :class:`Detection`:

* ``label``: the Shape label of the entity type (:data:`ENTITY_LABELS`), or
  ``presidio:<TYPE>`` for a type with no entry;
* ``confidence``: the share of sampled values with that type, times the mean of Presidio's
  score for it (Presidio's best score per value).

Values are never logged, printed or put in an error message. The analyzer is built without
Presidio's own model download: by default it runs the pattern recognizers (email, phone, card
number, IBAN, SSN, IP address and the like) on a blank English pipeline. ``SHAPE_PRESIDIO_MODEL``
names an *installed* spaCy model to add Presidio's name and place recognition; a model that is
not installed is an error, never a download.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.plugins.api.v1 import Detection

from .extras import MissingExtraError, require

SHAPE_API = "1.0"

DEFAULT_SAMPLE = 200
SAMPLE_ENV = "SHAPE_PRESIDIO_SAMPLE"
MODEL_ENV = "SHAPE_PRESIDIO_MODEL"
SAMPLE_SEED = 20261003
LANGUAGE = "en"

# Presidio entity type -> Shape semantic label (documented in docs/plugins/integrations.md).
ENTITY_LABELS: dict[str, str] = {
    "EMAIL_ADDRESS": "email",
    "US_SSN": "us_ssn",
    "PHONE_NUMBER": "phone",
    "CREDIT_CARD": "credit_card",
    "IBAN_CODE": "iban",
    "IP_ADDRESS": "ip_address",
}

__all__ = [
    "ENTITY_LABELS",
    "AnalysisError",
    "MissingExtraError",
    "PresidioDetector",
    "analyzer",
    "label_for",
    "reset",
    "sample_size",
    "sample_values",
    "summarize",
]


class AnalysisError(RuntimeError):
    """Presidio could not analyze a column. The message never holds a value."""


def sample_size() -> int:
    raw = os.environ.get(SAMPLE_ENV)
    if raw is None:
        return DEFAULT_SAMPLE
    try:
        n = int(raw.strip())
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f"{SAMPLE_ENV} must be a whole number of at least 1, got {raw!r}")
    return n


def label_for(entity_type: str) -> str:
    return ENTITY_LABELS.get(entity_type, f"presidio:{entity_type}")


def sample_values(values: pa.Array | pa.ChunkedArray, n: int) -> list[str]:
    """Up to ``n`` of the non-empty string values, chosen with a fixed seed.

    The choice depends on the values and ``n`` only: not on the column name, the chunk layout
    or the run.
    """
    arr = values.combine_chunks() if isinstance(values, pa.ChunkedArray) else values
    text = pc.cast(arr, pa.string())
    keep = pc.and_(pc.is_valid(text), pc.not_equal(pc.utf8_trim_whitespace(text), ""))
    pool = text.filter(keep).to_pylist()
    if len(pool) <= n:
        return [str(v) for v in pool]
    rng = np.random.default_rng(SAMPLE_SEED)
    picks = np.sort(rng.choice(len(pool), size=n, replace=False))
    return [str(pool[i]) for i in picks]


def summarize(hits: Sequence[dict[str, float]], sampled: int) -> tuple[str, float] | None:
    """The winning entity type and the confidence, from the best score per value and type."""
    if sampled <= 0:
        return None
    scores: dict[str, list[float]] = {}
    for per_value in hits:
        for entity, score in per_value.items():
            scores.setdefault(entity, []).append(score)
    if not scores:
        return None
    best = min(scores, key=lambda e: (-len(scores[e]), -(sum(scores[e]) / len(scores[e])), e))
    found = scores[best]
    confidence = (len(found) / sampled) * (sum(found) / len(found))
    return best, max(0.0, min(1.0, confidence))


_ENGINE: Any = None


def reset() -> None:
    """Forget the analyzer (the next detection builds it again)."""
    global _ENGINE
    _ENGINE = None


def analyzer() -> Any:
    """The shared Presidio analyzer, built on first use."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _build_analyzer()
    return _ENGINE


def _build_analyzer() -> Any:
    require("presidio_analyzer", "presidio", name="Presidio")
    spacy = require("spacy", "presidio", name="Presidio")
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import SpacyNlpEngine

    model = os.environ.get(MODEL_ENV, "").strip()

    class OfflineSpacyEngine(SpacyNlpEngine):  # type: ignore[misc]
        """Presidio's spaCy engine without its download step."""

        def load(self) -> None:
            if model and not spacy.util.is_package(model):
                raise AnalysisError(
                    f"{MODEL_ENV}={model!r}: that spaCy model is not installed "
                    f"(Shape never downloads one; install it with `python -m spacy download "
                    f"{model}` first)"
                )
            self.nlp = {LANGUAGE: spacy.load(model) if model else spacy.blank(LANGUAGE)}

    engine = OfflineSpacyEngine(models=[{"lang_code": LANGUAGE, "model_name": model or "blank"}])
    engine.load()
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    registry.load_predefined_recognizers(languages=[LANGUAGE], nlp_engine=engine)
    return AnalyzerEngine(nlp_engine=engine, registry=registry, supported_languages=[LANGUAGE])


class PresidioDetector:
    """Presidio's analyzer as a ``shape.detectors`` plugin."""

    name = "presidio"

    def detect(self, values: pa.Array, column: str) -> Detection | None:
        arr = values
        if not (pa.types.is_string(arr.type) or pa.types.is_large_string(arr.type)):
            return None
        sample = sample_values(arr, sample_size())
        if not sample:
            return None
        engine = analyzer()
        hits: list[dict[str, float]] = []
        for text in sample:
            try:
                found = engine.analyze(text=text, language=LANGUAGE)
            except Exception as exc:
                # Presidio's message can contain the value: keep the type, drop the text.
                raise AnalysisError(
                    f"Presidio failed on a sampled value of column {column!r} "
                    f"({type(exc).__name__})"
                ) from None
            best: dict[str, float] = {}
            for r in found:
                best[r.entity_type] = max(best.get(r.entity_type, 0.0), float(r.score))
            hits.append(best)
        result = summarize(hits, len(sample))
        if result is None:
            return None
        entity, confidence = result
        return Detection(label_for(entity), confidence)
