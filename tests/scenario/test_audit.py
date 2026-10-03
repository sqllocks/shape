"""AUD-scenario: regression tests for the defects the audit lane found (one section per issue)."""

from __future__ import annotations

from pathlib import Path

from shape.generation.schema import GenSchema
from shape.scenario import PackLoader, PackRunner

FILE_DROP = {
    "pack_version": 1,
    "id": "t",
    "kind": "file_drop",
    "domain": "",
    "description": "",
    "fabric_targets": {"x": 1},
    "file_drop": {"formats": ["csv"], "entities": ["customer"]},
}


def no_presets(retail, domain: str | None = None) -> GenSchema:
    """The retail schema with no scale presets (so any scale name is taken)."""
    doc = retail.schema.to_dict()
    doc["generation"]["scales"] = {}
    doc["generation"].pop("scale", None)
    if domain is not None:
        doc["model"]["domain"] = domain
    return GenSchema.from_dict(doc)


def everything_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*")}


# ---- #281: the run id is built from plain names, the manifest stays in the output ---------------


def test_281_a_scale_name_with_a_path_does_not_leave_the_output(tmp_path, retail):
    out = tmp_path / "a" / "b"
    pack = PackLoader().parse(FILE_DROP)
    result = PackRunner().run(pack, no_presets(retail), "../../../evil", 1, out)
    assert result.is_success, result.errors
    manifest = Path(result.files_written[-1])
    assert manifest.parent.resolve() == out.resolve()
    assert "/" not in result.manifest.run_id and "\\" not in result.manifest.run_id
    outside = everything_under(tmp_path) - everything_under(out) - {out.resolve()}
    assert outside == {(tmp_path / "a").resolve()}
    assert result.manifest.scale == "../../../evil"  # the record keeps what was asked


def test_281_a_domain_name_with_a_path_does_not_leave_the_output(tmp_path, retail):
    out = tmp_path / "work" / "out"
    pack = PackLoader().parse(FILE_DROP)
    schema = no_presets(retail, domain="../../../../ESCAPED_DOMAIN")
    result = PackRunner().run(pack, schema, "small", 1, out)
    assert result.is_success, result.errors
    assert Path(result.files_written[-1]).parent.resolve() == out.resolve()
    escaped = [p for p in tmp_path.rglob("*ESCAPED_DOMAIN*") if p.parent.resolve() != out.resolve()]
    assert escaped == []
