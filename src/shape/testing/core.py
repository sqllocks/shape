"""Generate positive and adversarial datasets from Shapes/contracts."""

from __future__ import annotations

from dataclasses import dataclass

from shape.generation import generate_from_shape


@dataclass(frozen=True, slots=True)
class TestCase:
    name: str
    rows: dict
    expected: str


def generate_tests(shape, contract=None, rows=100, seed=0):
    import numpy as np

    base, _ = generate_from_shape(shape, rows, seed)
    cases = [TestCase("valid", base, "pass")]
    contract = contract or {}
    for name, spec in contract.get("columns", {}).items():
        if name not in base:
            continue
        if spec.get("required", True):
            x = {k: v.copy() if hasattr(v, "copy") else list(v) for k, v in base.items()}
            x.pop(name, None)
            cases.append(TestCase(f"missing_{name}", x, "fail"))
        if "max" in spec:
            x = {k: v.copy() if hasattr(v, "copy") else list(v) for k, v in base.items()}
            a = np.asarray(x[name]).copy()
            a[0] = spec["max"] + 1
            x[name] = a
            cases.append(TestCase(f"above_max_{name}", x, "fail"))
        if "min" in spec:
            x = {k: v.copy() if hasattr(v, "copy") else list(v) for k, v in base.items()}
            a = np.asarray(x[name]).copy()
            a[0] = spec["min"] - 1
            x[name] = a
            cases.append(TestCase(f"below_min_{name}", x, "fail"))
    return tuple(cases)
