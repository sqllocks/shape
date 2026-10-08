"""AUD-gen: reference datasets read from JSON files (#209)."""

from __future__ import annotations

import pytest

from shape.generation import reference


@pytest.mark.parametrize("text", ['{"red": 1, "blue": 2}', '"hello"', "[1, 2]", "{not json"])
def test_a_reference_file_that_is_not_a_list_names_the_file(tmp_path, text):
    # 209: an object loaded as its keys, a string as its characters, and invalid JSON raised a
    # bare JSONDecodeError naming no file.
    (tmp_path / "colors.json").write_text(text, encoding="utf-8")
    reference.add_search_path(tmp_path)
    try:
        with pytest.raises(ValueError, match="colors.json"):
            reference.load_dataset("colors")
    finally:
        reference.clear_search_paths()


@pytest.mark.parametrize("data", [{"red": 1}, "hello"])
def test_registering_a_mapping_or_a_string_is_an_error(data):
    with pytest.raises(ValueError, match="list"):
        reference.register_dataset("x", data)


def test_a_list_still_loads(tmp_path):
    (tmp_path / "ok.json").write_text('["a", "b"]', encoding="utf-8")
    reference.add_search_path(tmp_path)
    try:
        assert len(reference.load_dataset("ok")) == 2
    finally:
        reference.clear_search_paths()
