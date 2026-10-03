"""Fixtures of the safe-by-default capture tests (W1-11): one dataset with planted values.

Every planted value is something a profile must not carry in its default form: a category with 1
to ``k - 1`` rows, an email, an SSN, a card number, an extreme number, a rare city.
"""

from __future__ import annotations

import random
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import shape

ROWS = 400
PLANTED = {
    "email": "planted.person@leak.example",
    "ssn": "078-05-1120",
    "card": "4000 0000 0000 0002",
    "extreme": "987654321.13",
    "rare_city": "Zzyzx",
    "rare_one": "edge1",
    "rare_four": "edge4",
    "note": "aaa leaked note about a patient",
}
KEPT_FIVE = "edge5"  # a category with exactly k = 5 rows: it stays


def planted_table() -> pa.Table:
    random.seed(7)
    n = ROWS
    grade = ["A"] * 200 + ["B"] * 190 + [KEPT_FIVE] * 5 + ["edge4"] * 4 + ["edge1"]
    random.shuffle(grade)
    emails = [f"user{i}@example.com" for i in range(n)]
    emails[17] = PLANTED["email"]
    ssn = [f"{100 + i % 800:03d}-{10 + i % 80:02d}-{1000 + i:04d}" for i in range(n)]
    ssn[5] = PLANTED["ssn"]
    card = [f"4111 1111 1111 {1000 + i:04d}" for i in range(n)]
    card[8] = PLANTED["card"]
    amount = [round(random.uniform(5, 500), 2) for _ in range(n)]
    amount[3] = float(PLANTED["extreme"])
    age = [random.randint(20, 60) for _ in range(n)]
    city = [random.choice(["Paris", "Rome", "Oslo"]) for _ in range(n)]
    city[1] = PLANTED["rare_city"]
    note = [f"free text number {i} here" for i in range(n)]
    note[17] = PLANTED["note"]
    return pa.table(
        {
            "id": list(range(n)),
            "grade": grade,
            "email": emails,
            "ssn": ssn,
            "card": card,
            "amount": amount,
            "age": age,
            "city": city,
            "note": note,
        }
    )


@pytest.fixture(scope="session")
def table() -> pa.Table:
    return planted_table()


@pytest.fixture(scope="session")
def profile(table: pa.Table) -> Any:
    return shape.profile(table)


@pytest.fixture
def artifact_text() -> Callable[[Path], str]:
    """Every member of a ``.shape`` archive, decompressed and joined: what is in its bytes."""

    def read(path: Path) -> str:
        with zipfile.ZipFile(path) as z:
            return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist())

    return read


@pytest.fixture(scope="session")
def planted() -> dict[str, str]:
    return dict(PLANTED)
