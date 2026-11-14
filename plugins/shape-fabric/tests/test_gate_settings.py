"""Variable Library settings use the documented NotebookUtils surface."""

from types import SimpleNamespace
from typing import Any

import pytest
from shape_fabric.gate_settings import load_gate_settings


def test_library_uses_active_values() -> None:
    values = {
        "gateMode": "enforce",
        "thresholds": '{"mean_shift_std": 0.25}',
        "baselinePath": "shape/test.shape",
    }
    calls = []

    class Library:
        def getVariable(self, name: str) -> str:
            calls.append(name)
            return values[name]

    def get_library(name: str) -> Library:
        assert name == "shape_gate"
        return Library()

    settings = load_gate_settings(
        "shape_gate", utils=SimpleNamespace(variableLibrary=SimpleNamespace(getLibrary=get_library))
    )
    assert settings.gate_mode == "enforce"
    assert settings.thresholds == {"mean_shift_std": 0.25}
    assert settings.baseline_path == "shape/test.shape"
    assert calls == ["gateMode", "thresholds", "baselinePath"]


def test_no_library_uses_defaults() -> None:
    settings = load_gate_settings()
    assert settings.gate_mode == "enforce"
    assert settings.thresholds == {}
    assert settings.baseline_path == ""


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("gateMode", "off", "gateMode"),
        ("thresholds", '{"unknown": 1}', "thresholds"),
        ("thresholds", '{"mean_shift_std": -1}', "thresholds"),
        ("thresholds", '{"mean_shift_std": 1e999}', "thresholds"),
        ("thresholds", "[]", "thresholds"),
        ("thresholds", "bad json", "thresholds"),
        ("baselinePath", 7, "baselinePath"),
    ],
)
def test_invalid_values_are_refused(key: str, value: Any, message: str) -> None:
    values = {"gateMode": "enforce", "thresholds": "{}", "baselinePath": "", key: value}
    utils = SimpleNamespace(
        variableLibrary=SimpleNamespace(
            getLibrary=lambda _: SimpleNamespace(getVariable=values.__getitem__)
        )
    )
    with pytest.raises(ValueError, match=message):
        load_gate_settings("shape_gate", utils=utils)


def test_requested_library_failure_never_silently_disables_gate() -> None:
    def fail(_: str) -> None:
        raise RuntimeError("sensitive service details")

    utils = SimpleNamespace(variableLibrary=SimpleNamespace(getLibrary=fail))
    with pytest.raises(ValueError, match="Cannot read Fabric Variable Library") as exc:
        load_gate_settings("shape_gate", utils=utils)
    assert "sensitive" not in str(exc.value)
