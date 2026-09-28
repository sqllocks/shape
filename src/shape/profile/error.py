from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ErrorModel:
    algorithm: str
    exact: bool
    confidence: float | None = None
    relative_error: float | None = None
    parameters: dict | None = None

    def to_dict(self):
        return asdict(self)


def hll_error(p):
    m = 1 << p
    return ErrorModel("hyperloglog", False, 0.95, 1.04 / (m**0.5), {"p": p, "registers": m})


def kll_error(k):
    return ErrorModel("kll-reference-v1", False, 0.99, min(1.0, 2.0 / (k**0.5)), {"k": k})
