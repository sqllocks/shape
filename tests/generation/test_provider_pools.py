"""The reference pools of the text providers are built from the file bytes (no Python object per
entry); they must equal the arrays the plain definition gives."""

from __future__ import annotations

from importlib import resources

import pyarrow as pa
import pytest

from shape.builtins.strategies import providers


def _plain(raw: bytes) -> pa.Array:
    return pa.array(raw.decode("utf-8").split("\n")[:-1], type=pa.string())


def _pool_files() -> list[str]:
    root = resources.files(providers.__package__).joinpath("pools")
    return sorted(p.name[:-4] for p in root.iterdir() if p.name.endswith(".txt"))


@pytest.mark.parametrize("name", _pool_files())
def test_every_shipped_pool_equals_the_plain_array(name):
    raw = resources.files(providers.__package__).joinpath(f"pools/{name}.txt").read_bytes()
    built = providers.pool(name)
    built.validate(full=True)
    assert built.type == pa.string()
    assert built.equals(_plain(raw))


@pytest.mark.parametrize(
    "raw",
    [b"", b"abc", b"\n", b"a\n\nb\n", "é\nü\n日本\n".encode(), b"x\ny", b"one\r\ntwo\r\n"],
)
def test_lines_edge_cases(raw):
    built = providers._lines(raw)
    built.validate(full=True)
    assert built.equals(_plain(raw))


def test_a_pool_that_is_not_utf8_is_an_error():
    with pytest.raises(UnicodeDecodeError):
        providers._lines(b"ok\n\xff\xfe\n")


@pytest.mark.parametrize(
    "values",
    [
        ["Ann", "BOB", "Mary Ann", "", "x1Y2"],
        ["Ünal", "ÉMILE", "日本", "ASCII"],  # one non-ASCII value: the Unicode kernel
        [None, "Ann", None],
        [],
    ],
)
def test_lower_equals_the_unicode_kernel(values):
    import pyarrow.compute as pc

    arr = pa.array(values, type=pa.string())
    assert providers._lower(arr).equals(pc.utf8_lower(arr))


def test_lower_of_an_ascii_slice_of_a_mixed_array_is_still_right():
    import pyarrow.compute as pc

    arr = pa.array(["ABC", "DÉF", "GHI"], type=pa.string())
    for part in (arr.slice(0, 1), arr.slice(2), arr):
        assert providers._lower(part).equals(pc.utf8_lower(part))


def test_lower_leaves_other_types_to_the_unicode_kernel():
    import pyarrow.compute as pc

    arr = pa.array(["ÀB", "cD"], type=pa.large_string())
    assert providers._lower(arr).equals(pc.utf8_lower(arr))


@pytest.mark.parametrize("limit", [1, 3, 4, 5, 9, 50])
@pytest.mark.parametrize(
    "values",
    [
        ["abcd", "ab", "", "abcde"],
        ["é" * 3, "日本語", "ab"],  # bytes exceed characters
        [None, "abc", None],
        ["x" * 40],
        [],
    ],
)
def test_truncate_equals_slicing_every_value(values, limit):
    import pyarrow.compute as pc

    arr = pa.array(values, type=pa.string())
    ctx = type("Ctx", (), {"column_def": type("Col", (), {"max_length": limit})()})()
    assert providers._truncate(arr, ctx).equals(pc.utf8_slice_codeunits(arr, 0, limit))
    assert providers._truncate(arr.slice(1), ctx).equals(
        pc.utf8_slice_codeunits(arr.slice(1), 0, limit)
    )


def test_truncate_of_a_column_without_a_limit_is_the_column():
    arr = pa.array(["abc"], type=pa.string())
    ctx = type("Ctx", (), {"column_def": type("Col", (), {"max_length": None})()})()
    assert providers._truncate(arr, ctx) is arr
