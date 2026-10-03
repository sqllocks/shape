"""The state and compatibility primitives (W1-01, issue 55, items 1, 2, 3, 9, 10)."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta, timezone

import pytest

import shape
from shape import compat
from shape.compat import (
    KINDS,
    Deprecation,
    FormatDeprecationWarning,
    FormatError,
    Kind,
    UnsupportedVersionError,
)

SAMPLE = Kind(
    name="sample",
    label="sample file",
    format="shape-sample",
    current=3,
    legacy_version_keys=("schema_version",),
    first_release={1: "0.9.0", 2: "0.9.0", 3: "0.9.0"},
    implicit_version=1,
)


def test_every_kind_is_declared_completely() -> None:
    assert len(KINDS) >= 17
    formats = [k.format for k in KINDS.values() if k.name not in {"artifact", "profile-artifact"}]
    assert len(formats) == len(set(formats)), "two kinds share a format name"
    for name, kind in KINDS.items():
        assert kind.name == name
        assert kind.current >= 1
        assert set(kind.first_release) == set(range(1, kind.current + 1)), name
        assert all(compat.parse_release(r) for r in kind.first_release.values())


def test_the_policy_names_every_persisted_kind() -> None:
    required = {
        "artifact",
        "safe-profile",
        "model",
        "generation-spec",
        "scenario-pack",
        "registry-layout",
        "run-manifest",
        "contract",
        "signature",
    }
    assert required <= set(KINDS)


def test_stamp_adds_format_version_writer_and_minimum() -> None:
    doc = compat.stamp(SAMPLE, {"rows": 3})
    assert doc["format"] == "shape-sample"
    assert doc["version"] == 3
    assert doc["shape_version"] == shape.__version__
    assert doc["min_shape_version"] == "0.9.0"
    assert doc["schema_version"] == 3, "the legacy key is written too in the 1.x series"
    assert doc["rows"] == 3


def test_stamp_does_not_mutate_and_keeps_unknown_fields() -> None:
    src = {"rows": 3, "x_future": {"a": [1, 2]}}
    out = compat.stamp(SAMPLE, src)
    assert src == {"rows": 3, "x_future": {"a": [1, 2]}}
    assert out["x_future"] == {"a": [1, 2]}


def test_stamp_an_older_version_records_its_own_minimum() -> None:
    old = Kind(**{**SAMPLE.__dict__, "first_release": {1: "0.8.0", 2: "0.9.0", 3: "0.9.0"}})
    assert compat.stamp(old, {}, version=1)["min_shape_version"] == "0.8.0"


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({"version": 2}, 2),
        ({"schema_version": 2}, 2),  # the old key name is still read
        ({"version": 2, "schema_version": 2}, 2),
        ({}, 1),  # an older writer declared nothing: the implicit version
    ],
)
def test_declared_version(doc: dict, expected: int) -> None:
    assert compat.declared_version(SAMPLE, doc) == expected


@pytest.mark.parametrize(
    ("doc", "problem"),
    [
        ({"version": 2, "schema_version": 3}, "conflict"),
        ({"version": True}, "integer"),
        ({"version": "2"}, "integer"),
        ({"version": 0}, "at least 1"),
        ({"version": -1}, "at least 1"),
        ({"version": 1.5}, "integer"),
    ],
)
def test_declared_version_rejects_malformed(doc: dict, problem: str) -> None:
    with pytest.raises(FormatError, match=problem):
        compat.declared_version(SAMPLE, doc)


def test_disagreeing_keys_above_the_supported_version_are_a_newer_file() -> None:
    """The highest claim decides: a file that says it is newer is refused as newer, never read
    as the older version one of its keys names."""
    with pytest.raises(UnsupportedVersionError, match="version 9"):
        compat.check_readable(SAMPLE, {"version": 1, "schema_version": 9})
    with pytest.raises(UnsupportedVersionError, match="version 9"):
        compat.check_readable(SAMPLE, {"version": 9, "schema_version": 1})


def test_a_kind_without_an_implicit_version_needs_one() -> None:
    strict = Kind(**{**SAMPLE.__dict__, "implicit_version": None})
    with pytest.raises(FormatError, match="declares no version"):
        compat.declared_version(strict, {})


def test_newer_version_names_the_minimum_shape_that_reads_it() -> None:
    doc = {"version": 4, "shape_version": "1.7.2", "min_shape_version": "1.6.0"}
    with pytest.raises(UnsupportedVersionError) as e:
        compat.check_readable(SAMPLE, doc, source="x.json")
    msg = str(e.value)
    assert msg.startswith("x.json: unsupported sample file version 4")
    assert "Shape 1.6.0 or newer" in msg
    assert "x.json" in msg
    assert e.value.min_shape_version == "1.6.0"
    assert e.value.found == 4 and e.value.supported == 3
    assert isinstance(e.value, ValueError)


def test_newer_version_without_a_minimum_falls_back_to_the_writer() -> None:
    with pytest.raises(UnsupportedVersionError, match=r"Shape 1\.7\.2 or newer"):
        compat.check_readable(SAMPLE, {"version": 4, "shape_version": "1.7.2"})


def test_newer_version_from_a_writer_that_said_nothing() -> None:
    with pytest.raises(UnsupportedVersionError, match="a newer Shape release"):
        compat.check_readable(SAMPLE, {"version": 4})


def test_a_hostile_minimum_is_not_echoed() -> None:
    doc = {"version": 4, "min_shape_version": "1.0\n; rm -rf /"}
    with pytest.raises(UnsupportedVersionError) as e:
        compat.check_readable(SAMPLE, doc)
    assert "rm -rf" not in str(e.value)


def test_error_class_keeps_the_callers_exception_type() -> None:
    from shape.artifact.io import ArtifactError

    with pytest.raises(ArtifactError) as e:
        compat.check_readable(SAMPLE, {"version": 9}, error=ArtifactError)
    assert isinstance(e.value, UnsupportedVersionError)


def test_current_and_older_versions_are_readable() -> None:
    for v in (1, 2, 3):
        assert compat.check_readable(SAMPLE, {"version": v}) == v


def test_check_format() -> None:
    compat.check_format(SAMPLE, {"format": "shape-sample"})
    compat.check_format(SAMPLE, {})  # older files declared no format
    with pytest.raises(FormatError, match="not a sample file"):
        compat.check_format(SAMPLE, {"format": "shape-other"})
    with pytest.raises(FormatError, match="not a sample file"):
        compat.check_format(SAMPLE, {"format": 7})


def test_legacy_format_names_are_accepted() -> None:
    k = Kind(**{**SAMPLE.__dict__, "legacy_formats": ("old-sample",)})
    compat.check_format(k, {"format": "old-sample"})


# --- strict mode, deprecation ------------------------------------------------------------------

DEPRECATED = Kind(
    **{
        **SAMPLE.__dict__,
        "deprecated": {
            1: Deprecation(since="1.4.0", removed_in="2.0.0", migrate_with="shape migrate"),
        },
    }
)


def test_reading_a_deprecated_version_warns_and_names_the_way_out() -> None:
    with pytest.warns(FormatDeprecationWarning, match=r"removed in Shape 2\.0\.0.*shape migrate"):
        assert compat.check_readable(DEPRECATED, {"version": 1}) == 1


def test_a_current_version_does_not_warn() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compat.check_readable(DEPRECATED, {"version": 3})


def test_strict_mode_turns_deprecation_into_an_error() -> None:
    with compat.strict_formats(), pytest.raises(FormatError, match="deprecated"):
        compat.check_readable(DEPRECATED, {"version": 1})


def test_strict_mode_refuses_legacy_key_names() -> None:
    with compat.strict_formats(), pytest.raises(FormatError, match="schema_version"):
        compat.check_readable(SAMPLE, {"schema_version": 2})
    with compat.strict_formats():
        assert compat.check_readable(SAMPLE, {"version": 2}) == 2


def test_strict_mode_refuses_unknown_fields() -> None:
    compat.check_unknown(SAMPLE, {"a": 1, "x_extra": 2}, known={"a"})  # lenient: ignored
    with compat.strict_formats(), pytest.raises(FormatError, match="x_extra"):
        compat.check_unknown(SAMPLE, {"a": 1, "x_extra": 2}, known={"a"})


def test_strict_mode_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not compat.is_strict()
    monkeypatch.setenv("SHAPE_STRICT_FORMATS", "1")
    assert compat.is_strict()
    monkeypatch.setenv("SHAPE_STRICT_FORMATS", "0")
    assert not compat.is_strict()


def test_strict_mode_restores_on_exit() -> None:
    with compat.strict_formats():
        assert compat.is_strict()
    assert not compat.is_strict()


def test_support_table_covers_every_version() -> None:
    rows = compat.support_rows()
    assert {(r["kind"], r["version"]) for r in rows} == {
        (k.name, v) for k in KINDS.values() for v in range(1, k.current + 1)
    }
    assert all(r["status"] == "supported" for r in rows), "no version is deprecated today"


# --- dates and decimals --------------------------------------------------------------------------


def test_utc_iso_is_utc_with_z() -> None:
    d = datetime(2026, 10, 3, 4, 5, 6, 123456, tzinfo=UTC)
    assert compat.utc_iso(d) == "2026-10-03T04:05:06.123456Z"
    assert compat.utc_iso(datetime(2026, 10, 3, 4, 5, 6, tzinfo=UTC)) == "2026-10-03T04:05:06Z"


def test_utc_iso_converts_other_zones() -> None:
    d = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert compat.utc_iso(d) == "2026-10-03T03:30:00Z"


def test_utc_iso_refuses_a_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        compat.utc_iso(datetime(2026, 10, 3))


def test_utc_now_parses_back() -> None:
    now = compat.parse_utc_iso(compat.utc_iso())
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "text",
    ["2026-10-03T04:05:06Z", "2026-10-03T04:05:06+00:00", "2026-10-03T06:05:06+02:00"],
)
def test_parse_utc_iso_accepts_every_iso_form_of_one_instant(text: str) -> None:
    assert compat.parse_utc_iso(text) == datetime(2026, 10, 3, 4, 5, 6, tzinfo=UTC)


@pytest.mark.parametrize("text", ["2026-10-03T04:05:06", "03/10/2026", "", "2026-10-03 04:05"])
def test_parse_utc_iso_refuses_naive_and_local_forms(text: str) -> None:
    with pytest.raises(ValueError):
        compat.parse_utc_iso(text)


def test_json_default_writes_decimals_as_strings() -> None:
    from decimal import Decimal

    assert compat.json_default(Decimal("10.50")) == "10.50"
    assert compat.json_default(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05Z"
    with pytest.raises(TypeError):
        compat.json_default(object())
    with pytest.raises(TypeError, match="naive"):
        compat.json_default(datetime(2026, 1, 2))
