"""Deterministic adversarial corpus helpers."""

from __future__ import annotations


def hostile_strings():
    return [
        "",
        "\x00",
        "../etc/passwd",
        "..\\..\\windows",
        "A" * 100000,
        "\ud800",
        "\u202eexe.txt",
        "é",
        "e\u0301",
        "💾" * 1000,
        '=HYPERLINK("http://example.invalid")',
        "\n\r\t",
    ]


def numeric_edges():
    return [
        0,
        1,
        -1,
        2**63 - 1,
        -(2**63),
        2**64 - 1,
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.0,
    ]
