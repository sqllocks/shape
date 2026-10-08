"""W1-11 deliverable 7: the default output of ``shape profile`` passes ``validate --safe`` on the
datasets the privacy tests use: emails, SSNs, cards, rare categories, sparse personal data."""

from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

from shape.cli.main import main


def _ssn(rng: random.Random) -> str:
    return f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"


def datasets() -> dict[str, pa.Table]:
    rng = random.Random(5)
    n = 400
    orders = pa.table(
        {
            "id": list(range(120)),
            "status": ["paid" if i % 2 else "new" for i in range(120)],
            "email": [f"u{i}@x.com" for i in range(120)],
            "amount": [i * 1.5 for i in range(120)],
        }
    )
    rare = pa.table(
        {
            "id": list(range(n)),
            "status": ["vip"] * 3 + ["paid" if i % 2 else "new" for i in range(n - 3)],
            "tier": ["gold" if i % 10 == 0 else "basic" for i in range(n)],
            "store": [100 + i % 4 for i in range(n)],
            "amount": [round(i * 1.37, 2) for i in range(n)],
            "email": [f"user{i}@example.com" for i in range(n)],
            "note": [f"free text number {i} here" for i in range(n)],
        }
    )
    sparse = pa.table(
        {
            "note": [
                f"ssn {_ssn(rng)}" if i % 100 == 0 else rng.choice(["gift", "fragile", "door"])
                for i in range(2000)
            ],
            "clean": [rng.choice(["a", "b", "c"]) for _ in range(2000)],
        }
    )
    embedded = pa.table(
        {
            "text": (
                ["call me, SSN 123-45-6789", "mail a@b.com now", "card 4111 1111 1111 1111"] * 40
            ),
            "kind": ["x", "y", "z"] * 40,
        }
    )
    ssns = pa.table({"ssn": [_ssn(rng) for _ in range(300)], "g": ["a", "b", "c"] * 100})
    cards = pa.table(
        {"card": [f"4111 1111 1111 {1000 + i}" for i in range(300)], "g": ["a", "b", "c"] * 100}
    )
    return {
        "orders": orders,
        "rare": rare,
        "sparse_ssn": sparse,
        "embedded": embedded,
        "ssns": ssns,
        "cards": cards,
    }


@pytest.mark.parametrize("name", sorted(datasets()))
def test_the_default_output_validates_clean(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    pacsv.write_csv(datasets()[name], "d.csv")
    assert main(["profile", "d.csv", "-o", "d.shape"]) == 0
    capsys.readouterr()
    code = main(["profile", "validate", "--safe", "d.shape"])
    out = capsys.readouterr()
    assert code == 0, out.err
    assert main(["profile", "d.csv", "-o", "f.shape", "--capture", "full"]) == 0
    capsys.readouterr()
    assert main(["profile", "validate", "--safe", "f.shape"]) == 1
