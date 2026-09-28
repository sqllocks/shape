from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Transform:
    name: str
    fn: Callable[[Any], Any]

    def apply(self, value):
        return self.fn(value)


def redact(replacement="[REDACTED]"):
    return Transform("redact", lambda _: replacement)


def generalize_numeric(bucket: float):
    if bucket <= 0:
        raise ValueError("bucket must be positive")
    return Transform(
        "generalize_numeric", lambda x: None if x is None else round(float(x) / bucket) * bucket
    )
