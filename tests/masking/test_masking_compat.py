"""W5-01 (#62): compatibility tests that pin the public interface and its outputs.

A failure here means a change that the stability promise (docs/MASK.md) forbids within 1.x:
if the change is intended it needs a new MASKING_API_VERSION major, not an edited vector.
"""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

import shape.masking as masking
from shape.masking import Masker

KEY = b"0123456789abcdef0123456789abcdef"

# (kind, input, options, expected output) for KEY, produced by MASKING_API_VERSION 1.0.
GOLDEN = [
    ("identifier", "AB-1234-xy", {}, "HQ-6368-yw"),
    ("identifier", 100200300, {}, 675303842),
    ("name", "Alice Johnson", {}, "Osin Oroland"),
    ("name", "SMITH, JOHN", {}, "MCCLSEN, DAINE"),
    ("name", "Bob", {"part": "last"}, "Acocroft"),
    ("email", "Ada@Corp.io", {}, "giis_salason038@example.com"),
    ("phone", "+44 20 7946 0958", {}, "+44 59 5246 6780"),
    ("phone", "(555) 201-3344", {}, "(784) 803-9393"),
    ("date", "2021-03-04", {"max_days": 30}, "2021-03-10"),
    ("date", "2021-03-04 10:11:12", {"max_days": 30}, "2021-03-06 10:11:12"),
    ("date", "04/03/2021", {"subject": "p1"}, "06/01/2021"),
    (
        "text",
        "mail alice@corp.io or call (555) 201-3344 from 10.1.2.3, ssn 123-45-6789",
        {},
        "mail sia.frost004@example.org or call (784) 803-9393 from 183.112.172.183, "
        "ssn 728-40-5899",
    ),
]

# The name lists the masks draw from are part of the promise: changing a file changes outputs.
POOL_SHA256 = {
    "first_names.txt": "183a523eda450536c99eaaa236cee224f1e1c628a7689dc7bb8892a984f9ba8d",
    "last_names.txt": "a0e0a6507067b351884d7922ad225556280953d69e051dfbc618a81612798dd7",
}


@pytest.mark.parametrize(("kind", "value", "options", "expected"), GOLDEN)
def test_golden_vectors(kind, value, options, expected):
    assert Masker(KEY).mask(kind, value, **options) == expected


def test_name_pools_are_frozen():
    data = Path(masking.__file__).parent / "data"
    for name, digest in POOL_SHA256.items():
        assert hashlib.sha256((data / name).read_bytes()).hexdigest() == digest, name


def test_public_surface_is_exactly_the_documented_one():
    assert masking.MASKING_API_VERSION == "1.0"
    assert masking.MIN_KEY_BYTES == 16
    assert masking.KINDS == ("identifier", "name", "email", "phone", "date", "text")
    assert sorted(masking.__all__) == sorted(
        [
            "KINDS",
            "MASKING_API_VERSION",
            "MIN_KEY_BYTES",
            "Masker",
            "MaskingError",
            "MaskingKeyError",
            "generate_key",
            "load_key",
        ]
    )
    assert issubclass(masking.MaskingError, ValueError)
    assert issubclass(masking.MaskingKeyError, ValueError)


def test_public_signatures_are_unchanged():
    sig = {n: str(inspect.signature(getattr(Masker, n))) for n in ("mask", "mask_column")}
    assert sig["mask"] == "(self, kind: 'str', value: 'Any', **options: 'Any') -> 'Any'"
    assert (
        sig["mask_column"] == "(self, kind: 'str', values: 'Any', **options: 'Any') -> 'pa.Array'"
    )
    assert str(inspect.signature(masking.load_key)) == (
        "(*, file: 'str | Path | None' = None, env: 'str | None' = None) -> 'bytes'"
    )
    assert str(inspect.signature(masking.generate_key)) == "(nbytes: 'int' = 32) -> 'bytes'"
    assert list(inspect.signature(Masker.mask_tables).parameters) == ["self", "tables", "columns"]
    assert list(inspect.signature(Masker.__init__).parameters) == ["self", "key"]
