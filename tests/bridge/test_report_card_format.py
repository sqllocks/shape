"""CR-0117: report-card input format selection is additive in bridge 1.4."""

from __future__ import annotations

import pytest


def test_format_is_gated_before_1_4(bridge, tmp_path):
    for version in ("1.0", "1.1", "1.2", "1.3"):
        response = bridge.handle(
            {
                "api_version": version,
                "command": "report_card",
                "args": {"real": str(tmp_path), "synthetic": str(tmp_path), "format": "csv"},
            }
        )
        assert not response["ok"]
        assert response["error"]["code"] == (
            "usage.unknown_command" if version in ("1.0", "1.1") else "usage.unknown_argument"
        )


@pytest.mark.parametrize("data_format", ["csv", "parquet", "jsonl", "auto"])
def test_format_is_forwarded_to_report_card(bridge, tmp_path, monkeypatch, data_format):
    import shape.quality.reportcard as module

    calls = []

    class Card:
        def to_dict(self):
            return {"format": "shape-report-card", "version": 1}

    def fake_card(*args, **kwargs):
        calls.append(kwargs)
        return Card()

    monkeypatch.setattr(module, "report_card", fake_card)
    response = bridge.handle(
        {
            "api_version": "1.4",
            "command": "report_card",
            "args": {"real": str(tmp_path), "synthetic": str(tmp_path), "format": data_format},
        }
    )
    assert response["ok"], response
    assert calls[0]["data_format"] == data_format
