from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def semantic_shape(texts: list[Any]) -> dict[str, Any]:
    lens = [len(str(x)) for x in texts]
    tokens = Counter(w.lower() for x in texts for w in str(x).split())
    return {
        "count": len(lens),
        "mean_length": sum(lens) / len(lens) if lens else 0,
        "vocabulary": len(tokens),
        "top_tokens": tokens.most_common(20),
    }


def template_generate(template: str, rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return [template.format(**r) for r in rows]
