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
    return ErrorModel(
        "hyperloglog",
        False,
        0.95,
        1.04 / (m**0.5),
        {"p": p, "registers": m, "estimator": "ertl-improved", "hash": "xxh3-64"},
    )


def kll_error(k):
    # rank error of the geometric-capacity KLL is about 1.65/k (0.8% at k=200)
    return ErrorModel("kll-v2", False, 0.99, min(1.0, 2.0 / k), {"k": k, "error": "rank"})


def space_saving_error(capacity):
    # count - error <= true count <= count, and error <= n / capacity
    return ErrorModel(
        "space-saving", False, 1.0, None, {"capacity": capacity, "error_bound": "n/capacity"}
    )
