"""One policy for every persisted kind (W1-01, issue 55, items 1, 2 and 3).

For each kind that Shape writes, through its real writer:

* the file declares ``format`` and an integer ``version`` (same key names for every kind), the
  writer's ``shape_version`` and the first release that reads it, ``min_shape_version``;
* a file with only the key names older releases wrote (no unified keys) still reads, to the same
  content;
* a version newer than this release reads fails with the minimum Shape release that reads it;
* conflicting version keys fail.

Kinds that users author (scenario packs, generation specs, verify configurations) have no writer;
their documents are checked the same way from a document the reader accepts today.
"""

from __future__ import annotations

import copy
import json
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest

import shape
from shape import compat
from shape.artifact import codec
from shape.artifact.io import (
    ArtifactError,
    canonical_json,
    read_artifact,
    read_manifest_bytes,
    write_container,
)
from shape.compat import BOOKKEEPING_KEYS, KINDS, FormatError, UnsupportedVersionError

NEWER_MINIMUM = "9.9.0"


def canon(value: Any) -> str:
    return json.dumps(codec.encode(value), sort_keys=True, default=str)


def strip(doc: dict[str, Any], keep: tuple[str, ...] = ()) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in BOOKKEEPING_KEYS or k in keep}


@pytest.fixture(scope="module")
def csv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("kinds") / "orders.csv"
    lines = ["id,status,amount"] + [f"{i},{'a' if i % 2 else 'b'},{i * 1.5}" for i in range(30)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def profile(csv: Path) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile(str(csv))


@dataclass
class Case:
    kind: str
    make: Callable[[Path, Any], Any]  # (tmp_path, profile) -> subject
    declaration: Callable[[Any], dict[str, Any]]
    edit: Callable[[Any, Path, Callable[[dict[str, Any]], None]], Any]
    read: Callable[[Any], str]
    # keys of the declaration that are older than the unified policy and are not removed when
    # the test strips the unified keys
    legacy_keep: tuple[str, ...] = ()
    authored: bool = False
    error: type[Exception] = ValueError


# --- subject types: a zip artifact, a JSON file, a dict ---------------------------------


def _zip_declaration(path: Path) -> dict[str, Any]:
    doc = json.loads(read_manifest_bytes(path))
    assert isinstance(doc, dict)
    return doc


def _zip_edit(path: Path, tmp: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, components = read_artifact(path, notice=False)
    manifest = copy.deepcopy(manifest)
    edit(manifest)
    out = tmp / "edited.shape"
    write_container(out, canonical_json(manifest), dict(components))
    return out


def _json_declaration(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _json_edit(path: Path, tmp: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    doc = _json_declaration(path)
    edit(doc)
    out = tmp / "edited.json"
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def _dict_declaration(doc: dict[str, Any]) -> dict[str, Any]:
    return doc


def _dict_edit(doc: dict[str, Any], tmp: Path, edit: Callable[[dict[str, Any]], None]) -> Any:
    out = copy.deepcopy(doc)
    edit(out)
    return out


# --- the cases -------------------------------------------------------------------------


def _artifact_make(tmp: Path, profile: Any) -> Path:
    from shape.artifact import write_model
    from shape.capture import capture_rows

    cap = capture_rows([{"id": i, "v": i * 2} for i in range(10)])
    out = tmp / "m.shape"
    write_model(out, cap.to_dict() if hasattr(cap, "to_dict") else cap, name="m")
    return out


def _artifact_read(path: Path) -> str:
    from shape.artifact import read_model

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, model = read_model(path)
    return canon([manifest["name"], manifest["shape_content_id"], model])


def _profile_artifact_make(tmp: Path, profile: Any) -> Path:
    out = tmp / "p.shape"
    shape.save(profile, str(out))
    return out


def _profile_artifact_read(path: Path) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return canon(shape.load(str(path)).to_dict())


def _safe_make(tmp: Path, profile: Any) -> Path:
    from shape.privacy.safe_profile import to_safe_profile

    return to_safe_profile(profile).save(tmp / "s.json")


def _safe_read(path: Path) -> str:
    from shape.privacy.safe_profile import SafeProfile

    return canon(strip(SafeProfile.load(path).to_dict()))


_GENSCHEMA = {
    "schema_version": 1,
    "model": {"name": "m", "seed": 7},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["k"],
            "columns": {
                "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}}
            },
        }
    },
    "generation": {"scale": "s", "scales": {"s": {"t": 5}}},
}


def _genschema_make(tmp: Path, profile: Any) -> Path:
    from shape.generation.schema import GenSchema

    out = tmp / "g.json"
    out.write_text(json.dumps(GenSchema.from_dict(_GENSCHEMA).to_dict()), encoding="utf-8")
    return out


def _genschema_read(path: Path) -> str:
    from shape.generation.schema import GenSchema

    return canon(strip(GenSchema.from_dict(json.loads(path.read_text())).to_dict()))


def _run_manifest_make(tmp: Path, profile: Any) -> Path:
    from shape.scenario.manifest import ManifestBuilder

    b = ManifestBuilder()
    b.start(None, None, "retail", "small", 1)
    b.record_output("t", 3, 2, [])
    out = tmp / "run.json"
    ManifestBuilder.to_file(b.finish(), out)
    return out


def _run_manifest_read(path: Path) -> str:
    from shape.scenario.manifest import ManifestBuilder

    d = ManifestBuilder.from_file(path).to_dict()
    d.pop("timestamps", None)
    return canon(strip(d))


def _contract_make(tmp: Path, profile: Any) -> Path:
    from shape.generation.schema import GenSchema
    from shape.integrations.fabric.generation import domain_contract

    out = tmp / "c.json"
    doc = domain_contract(GenSchema.from_dict(_GENSCHEMA), {"t": 5})
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def _contract_read(path: Path) -> str:
    from shape.contracts import v1

    doc = v1._load_contract(path)
    v1._validate_contract(doc)
    return canon(strip(doc))


def _contract_model_make(tmp: Path, profile: Any) -> Path:
    from shape.spec import FieldContract, ShapeContract

    out = tmp / "cm.json"
    c = ShapeContract("customer", fields=(FieldContract("id", "integer", False),)).validate()
    out.write_text(c.to_json(), encoding="utf-8")
    return out


def _contract_model_read(path: Path) -> str:
    from shape.spec import ShapeContract

    return canon(strip(ShapeContract.from_json(path.read_text()).to_dict(), keep=("version",)))


def _gate_make(tmp: Path, profile: Any) -> Path:
    from shape.quality.gatespec import GateSchema

    out = tmp / "gate.json"
    out.write_text(json.dumps(GateSchema.from_dict(profile.to_dict()).to_dict()), encoding="utf-8")
    return out


def _gate_read(path: Path) -> str:
    from shape.quality.gatespec import GateSchema

    return canon(strip(GateSchema.from_dict(json.loads(path.read_text())).to_dict(), ("version",)))


def _export_make(tmp: Path, profile: Any) -> Path:
    from shape.cli.profiles import export_document

    out = tmp / "e.json"
    out.write_text(json.dumps(export_document(profile)), encoding="utf-8")
    return out


def _export_read(path: Path) -> str:
    from shape.cli.profiles import read_export

    return canon(read_export(str(path)).to_dict())


def _registry_make(tmp: Path, profile: Any) -> Path:
    from shape.registry import LocalRegistry

    root = tmp / "reg"
    LocalRegistry(root)
    return root / "layout.json"


def _registry_read(path: Path) -> str:
    import shutil

    from shape.registry import LocalRegistry

    root = path.parent / f"read-{path.stem}"
    root.mkdir(exist_ok=True)
    shutil.copy(path, root / "layout.json")
    return str(LocalRegistry(root).layout_version)


def _profile_registry_make(tmp: Path, profile: Any) -> Path:
    from shape.registry.profiles import ProfileRegistry

    root = tmp / "preg"
    ProfileRegistry(root)
    return root / "_layout.json"


def _profile_registry_read(path: Path) -> str:
    import shutil

    from shape.registry.profiles import ProfileRegistry

    root = path.parent / f"read-{path.stem}"
    root.mkdir(exist_ok=True)
    shutil.copy(path, root / "_layout.json")
    return str(ProfileRegistry(root).layout_version)


def _signed_make(tmp: Path, profile: Any) -> Path:
    from shape.artifact import signing

    out = _artifact_make(tmp, profile)
    signing.sign_artifact(out, b"\x02" * 32)
    return out


PUB = None


def _signature_declaration(path: Path) -> dict[str, Any]:
    import zipfile

    with zipfile.ZipFile(path) as z:
        doc = json.loads(z.read("manifest.sig"))
    assert isinstance(doc, dict)
    return doc


def _signature_edit(path: Path, tmp: Path, edit: Callable[[dict[str, Any]], None]) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest, components = read_artifact(path, notice=False)
    import zipfile

    with zipfile.ZipFile(path) as z:
        sig = json.loads(z.read("manifest.sig"))
        raw_manifest = z.read("manifest.json")
    edit(sig)
    out = tmp / "sig-edited.shape"
    write_container(out, raw_manifest, dict(components), json.dumps(sig).encode())
    return out


def _signature_read(path: Path) -> str:
    from shape.artifact import signing

    pub = signing.public_key_of(b"\x02" * 32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        read_artifact(path, verify_key=pub)
    return "verified"


_PACK = {"pack_version": 1, "id": "p", "kind": "file_drop", "domain": "retail"}
_GSL = {"version": 1, "name": "n", "schema": {"type": "domain", "domain": "retail"}}
_VERIFY = {"format": "shape-verify-config", "version": 1, "no_future": ["t.d"]}


def _pack_read(doc: dict[str, Any]) -> str:
    from shape.scenario.loader import PackLoader

    d = asdict(PackLoader().parse(doc))
    return canon(d)


def _gsl_read(doc: dict[str, Any]) -> str:
    from shape.scenario.gsl import GSLParser

    d = asdict(GSLParser().parse_dict(doc))
    d.pop("path", None)
    d.pop("_base_dir", None)
    return canon(d)


def _verify_read(doc: dict[str, Any]) -> str:
    from shape.quality.verifyconfig import VerifyConfig

    cfg = VerifyConfig.from_dict(doc)
    return canon({"rules": dict(cfg.rules), "files": list(cfg.file_paths)})


def _const(doc: dict[str, Any]) -> Callable[[Path, Any], dict[str, Any]]:
    return lambda tmp, profile: copy.deepcopy(doc)


def _zip(kind: str, make: Any, read: Any, **kw: Any) -> Case:
    return Case(kind, make, _zip_declaration, _zip_edit, read, error=ArtifactError, **kw)


def _json(kind: str, make: Any, read: Any, **kw: Any) -> Case:
    return Case(kind, make, _json_declaration, _json_edit, read, **kw)


CASES: dict[str, Case] = {
    c.kind: c
    for c in (
        _zip("artifact", _artifact_make, _artifact_read, legacy_keep=("format",)),
        _zip(
            "profile-artifact",
            _profile_artifact_make,
            _profile_artifact_read,
            legacy_keep=("format", "kind"),
        ),
        _json("safe-profile", _safe_make, _safe_read),
        _json("generation-schema", _genschema_make, _genschema_read),
        _json("run-manifest", _run_manifest_make, _run_manifest_read),
        _json("contract", _contract_make, _contract_read),
        _json(
            "contract-model", _contract_model_make, _contract_model_read, legacy_keep=("version",)
        ),
        _json("gate-schema", _gate_make, _gate_read, legacy_keep=("format", "version")),
        _json("profile-export", _export_make, _export_read, legacy_keep=("format",)),
        _json("registry-layout", _registry_make, _registry_read),
        _json("profile-registry-layout", _profile_registry_make, _profile_registry_read),
        Case(
            "signature",
            _signed_make,
            _signature_declaration,
            _signature_edit,
            _signature_read,
            error=ArtifactError,
        ),
        Case(
            "scenario-pack",
            _const(_PACK),
            _dict_declaration,
            _dict_edit,
            _pack_read,
            authored=True,
        ),
        Case(
            "generation-spec",
            _const(_GSL),
            _dict_declaration,
            _dict_edit,
            _gsl_read,
            legacy_keep=("version",),
            authored=True,
        ),
        Case(
            "verify-config",
            _const(_VERIFY),
            _dict_declaration,
            _dict_edit,
            _verify_read,
            legacy_keep=("format", "version"),
            authored=True,
        ),
    )
}

# kinds covered elsewhere: the model (its version lives in the content-addressed body) and the
# migration receipt (written by shape migrate)
COVERED_ELSEWHERE = {"model", "migration-receipt"}


def test_every_kind_is_covered() -> None:
    assert set(CASES) | COVERED_ELSEWHERE == set(KINDS)


# The generation schema's writer keeps the baseline layout (parity harnesses pin the file equal to
# the baseline's), so it declares nothing yet; its reader accepts the declaration.
UNSTAMPED_WRITER = {"generation-schema"}
WRITTEN = [k for k, c in CASES.items() if not c.authored and k not in UNSTAMPED_WRITER]
ALL = list(CASES)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.parametrize("kind", WRITTEN)
def test_writer_declares_the_unified_keys(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    decl = case.declaration(case.make(work, profile))
    k = KINDS[kind]
    assert decl["format"] == k.format
    assert decl["version"] == k.current
    assert decl["shape_version"] == shape.__version__
    assert decl["min_shape_version"] == k.first_release[k.current]
    for legacy in k.legacy_version_keys:
        assert decl[legacy] == k.current, "the old key is written beside `version` (1.x)"
    assert compat.parse_release(decl["shape_version"]) is not None


@pytest.mark.parametrize("kind", ALL)
def test_a_file_with_only_the_old_keys_reads_the_same(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    subject = case.make(work, profile)
    expected = case.read(subject)

    def to_old(doc: dict[str, Any]) -> None:
        for key in BOOKKEEPING_KEYS:
            if key not in case.legacy_keep:
                doc.pop(key, None)

    old = case.edit(subject, work, to_old)
    declared = case.declaration(old)
    assert not {"shape_version", "min_shape_version"} & set(declared) or kind in {"gate-schema"}
    assert case.read(old) == expected


# Packs and generation specs are authored; their validators (which every run goes through) are
# where a newer version is reported, with the same message, so a file that parses can still be
# examined and the problems of a whole spec are listed together.
REPORTED_BY_VALIDATOR = {"scenario-pack", "generation-spec"}


@pytest.mark.parametrize("kind", [k for k in ALL if k not in REPORTED_BY_VALIDATOR])
def test_a_newer_version_names_the_minimum_release(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    k = KINDS[kind]
    subject = case.make(work, profile)

    def newer(doc: dict[str, Any]) -> None:
        doc["version"] = k.current + 1
        for legacy in k.legacy_version_keys:
            doc[legacy] = k.current + 1
        doc["min_shape_version"] = NEWER_MINIMUM
        doc["shape_version"] = NEWER_MINIMUM

    with pytest.raises(UnsupportedVersionError) as e:
        case.read(case.edit(subject, work, newer))
    assert f"Shape {NEWER_MINIMUM} or newer" in str(e.value)
    assert f"unsupported {k.label} version {k.current + 1}" in str(e.value)
    assert isinstance(e.value, case.error)


@pytest.mark.parametrize("kind", [k for k in ALL if KINDS[k].legacy_version_keys])
def test_conflicting_version_keys_fail(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    k = KINDS[kind]
    subject = case.make(work, profile)

    def conflict(doc: dict[str, Any]) -> None:
        doc["version"] = 1
        doc[k.legacy_version_keys[0]] = 2

    # a key above what this release reads is refused as "newer"; below it, the keys conflict
    with pytest.raises(FormatError, match="conflicting version keys|unsupported") as e:
        case.read(case.edit(subject, work, conflict))
    assert isinstance(e.value, case.error)


@pytest.mark.parametrize("kind", ALL)
def test_a_malformed_version_fails(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    subject = case.make(work, profile)

    def bad(doc: dict[str, Any]) -> None:
        doc["version"] = "1"
        for legacy in KINDS[kind].legacy_version_keys:
            doc.pop(legacy, None)

    with pytest.raises(FormatError, match="must be an integer"):
        case.read(case.edit(subject, work, bad))


@pytest.mark.parametrize("kind", [k for k in ALL if KINDS[k].legacy_version_keys])
def test_strict_mode_refuses_old_key_names(kind: str, work: Path, profile: Any) -> None:
    case = CASES[kind]
    subject = case.make(work, profile)

    def to_old(doc: dict[str, Any]) -> None:
        doc.pop("version", None)

    old = case.edit(subject, work, to_old)
    case.read(old)  # lenient: fine
    with compat.strict_formats(), pytest.raises(FormatError, match="strict mode"):
        case.read(old)


def test_the_model_version_stays_in_the_body() -> None:
    """The model inside a .shape artifact is content-addressed, so its version stays under
    ``schema_version`` (changing the body would change every content id); the unified keys are in
    the artifact manifest. A newer model version is refused, not read as a capture."""
    from shape.spec.migrate import to_model
    from shape.spec.model import MODEL_VERSION

    with pytest.raises(UnsupportedVersionError, match=r"unsupported Shape model version 3"):
        to_model(
            {
                "schema_version": MODEL_VERSION + 1,
                "tables": {},
                "min_shape_version": NEWER_MINIMUM,
            }
        )
    with pytest.raises(ValueError, match="content-addressed"):
        compat.stamp("model", {})


def test_a_newer_pack_is_reported_by_the_validator() -> None:
    from shape.generation.schema import GenSchema
    from shape.scenario.loader import PackLoader
    from shape.scenario.validator import PackValidator

    doc = {**_PACK, "pack_version": 2, "min_shape_version": NEWER_MINIMUM}
    pack = PackLoader().parse(doc)
    assert pack.pack_version == 2 and pack.needs_release == NEWER_MINIMUM
    errors = PackValidator().validate(pack, GenSchema.from_dict(_GENSCHEMA)).errors
    assert any(
        "Unsupported pack_version 2" in e and f"Shape {NEWER_MINIMUM} or newer" in e for e in errors
    ), errors


def test_a_newer_pack_through_the_unified_key() -> None:
    from shape.scenario.loader import PackLoader

    pack = PackLoader().parse(
        {"id": "p", "kind": "file_drop", "version": 1, "format": "shape-scenario-pack"}
    )
    assert pack.pack_version == 1 and pack.extra_keys == []


def test_a_newer_generation_spec_is_reported_by_the_validator() -> None:
    from shape.scenario.gsl import GSLParser
    from shape.scenario.resolve import validate_spec

    doc = {**_GSL, "version": 2, "min_shape_version": NEWER_MINIMUM}
    spec = GSLParser().parse_dict(doc)
    errors = validate_spec(spec).errors
    assert any(
        "Unsupported spec version 2" in e and f"Shape {NEWER_MINIMUM} or newer" in e for e in errors
    ), errors


def test_the_generation_schema_writer_keeps_the_baseline_layout(work: Path, profile: Any) -> None:
    from shape.generation.schema import GenSchema

    doc = GenSchema.from_dict(_GENSCHEMA).to_dict()
    assert list(doc) == [
        "schema_version",
        "model",
        "tables",
        "relationships",
        "business_rules",
        "generation",
        "correlated_columns",
    ]
    # ... and the reader takes the declaration when a file has it
    stamped = compat.stamp("generation-schema", doc)
    assert canon(GenSchema.from_dict(stamped).to_dict()) == canon(doc)
