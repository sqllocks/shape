"""Language-neutral test vectors for the canonical forms and content ids (W1-01, issue 55, item 8).

``docs/specs/vectors/state_vectors.json`` is committed and must equal what the reference
implementation produces now. A second implementation, written here from the prose of
``docs/specs/STATE_AND_COMPATIBILITY.md`` with only the standard library, must reproduce every
vector: so the file, the prose and the code agree, and another language has something to port to.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_vectors_build as vectors  # noqa: E402

FILE = vectors.TARGET
DATA = json.loads(FILE.read_text(encoding="utf-8"))

DECLARATION = ("format", "version", "shape_version", "min_shape_version")


def test_the_committed_vectors_are_current() -> None:
    assert FILE.read_text(encoding="utf-8") == vectors.render(vectors.build()), (
        "run `python tests/state/state_vectors_build.py --write`; "
        "a changed vector is a format change"
    )


def test_the_vector_file_is_plain_json_any_language_reads() -> None:
    assert DATA["format"] == "shape-test-vectors" and DATA["version"] == 1

    def plain(node: Any) -> None:
        if isinstance(node, dict):
            assert all(isinstance(k, str) for k in node)
            for v in node.values():
                plain(v)
        elif isinstance(node, list):
            for v in node:
                plain(v)
        else:
            assert node is None or isinstance(node, (str, int, bool)), node
            assert not isinstance(node, float), "no float literal in the vector file itself"

    plain(DATA)


# --- a second implementation, from the prose, standard library only --------------------


class Tuple(tuple):  # noqa: SLOT001 - a marker type
    pass


def decode(node: Any) -> Any:
    if isinstance(node, dict):
        if len(node) == 1:
            ((tag, body),) = node.items()
            if tag == "$float":
                if body not in ("nan", "inf", "-inf"):
                    raise ValueError("bad $float")
                return float(body)
            if tag == "$tuple":
                if not isinstance(body, list):
                    raise ValueError("bad $tuple")
                return Tuple(decode(v) for v in body)
            if tag == "$dict":
                if not isinstance(body, list) or any(
                    not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str))
                    for p in body
                ):
                    raise ValueError("bad $dict")
                return {"$pairs": [(k, decode(v)) for k, v in body]}
        if any(k in ("$float", "$tuple", "$dict") for k in node):
            raise ValueError("a tag key must be alone")
        return {k: decode(v) for k, v in node.items()}
    if isinstance(node, list):
        return [decode(v) for v in node]
    return node


def parse(text: str) -> Any:
    def refuse(name: str) -> None:
        raise ValueError(name)

    return decode(json.loads(text, parse_constant=refuse))


_SHORT = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def write_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _SHORT:
            out.append(_SHORT[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def write(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return '{"$float":"nan"}'
        if v in (float("inf"), float("-inf")):
            return '{"$float":"inf"}' if v > 0 else '{"$float":"-inf"}'
        return repr(v)
    if isinstance(v, str):
        return write_string(v)
    if isinstance(v, Tuple):
        return '{"$tuple":[' + ",".join(write(x) for x in v) + "]}"
    if isinstance(v, list):
        return "[" + ",".join(write(x) for x in v) + "]"
    if isinstance(v, dict):
        if "$pairs" in v and len(v) == 1:
            pairs = v["$pairs"]
            return (
                '{"$dict":[' + ",".join(f"[{write_string(k)},{write(x)}]" for k, x in pairs) + "]}"
            )
        if any(k.startswith("$") for k in v):
            return (
                '{"$dict":['
                + ",".join(f"[{write_string(k)},{write(x)}]" for k, x in v.items())
                + "]}"
            )
        keys = sorted(v, key=lambda k: [ord(c) for c in k])  # by Unicode code point
        return "{" + ",".join(f"{write_string(k)}:{write(v[k])}" for k in keys) + "}"
    raise TypeError(type(v).__name__)


def manifest_canonical(v: Any) -> str:
    assert not isinstance(v, float), "a manifest has no floats"
    if isinstance(v, dict):
        keys = sorted(v, key=lambda k: [ord(c) for c in k])
        return "{" + ",".join(f"{write_string(k)}:{manifest_canonical(v[k])}" for k in keys) + "}"
    if isinstance(v, list):
        return "[" + ",".join(manifest_canonical(x) for x in v) + "]"
    return write(v)


@pytest.mark.parametrize("vec", DATA["codec"], ids=[v["id"] for v in DATA["codec"]])
def test_the_second_implementation_reproduces_every_codec_vector(vec: dict[str, Any]) -> None:
    out = write(parse(vec["input_json"]))
    assert out == vec["canonical"]
    assert out.encode("utf-8").hex() == vec["canonical_hex"]
    assert hashlib.sha256(out.encode("utf-8")).hexdigest() == vec["sha256"]


@pytest.mark.parametrize("vec", DATA["codec_errors"], ids=[v["id"] for v in DATA["codec_errors"]])
def test_the_second_implementation_refuses_every_error_vector(vec: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse(vec["input_json"])


@pytest.mark.parametrize("vec", DATA["manifests"], ids=[v["id"] for v in DATA["manifests"]])
def test_manifest_canonical_form(vec: dict[str, Any]) -> None:
    out = manifest_canonical(vec["manifest"])
    assert out == vec["canonical"]
    assert hashlib.sha256(out.encode("utf-8")).hexdigest() == vec["sha256"]


def test_content_ids_of_bodies() -> None:
    for vec in DATA["bodies"]:
        assert hashlib.sha256(vec["body"].encode("utf-8")).hexdigest() == vec["shape_content_id"]


LEGACY = {"safe-profile": ("schema_version",)}


@pytest.mark.parametrize("vec", DATA["documents"], ids=[v["id"] for v in DATA["documents"]])
def test_document_content_ids(vec: dict[str, Any]) -> None:
    drop = {*DECLARATION, "migrated_from", "source_content_id", *LEGACY.get(vec["kind"], ())}
    clean = {k: v for k, v in vec["document"].items() if k not in drop}
    assert (
        hashlib.sha256(write(parse(json.dumps(clean))).encode("utf-8")).hexdigest()
        == (vec["content_id"])
    )


def test_the_old_and_new_key_names_give_one_content_id() -> None:
    by_id = {v["id"]: v["content_id"] for v in DATA["documents"]}
    assert by_id["safe-profile-new-keys"] == by_id["safe-profile-old-keys"]
    assert by_id["run-manifest"] != by_id["safe-profile-old-keys"]


def test_the_signature_vector_verifies_with_an_independent_ed25519() -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    sig = DATA["signature"]
    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(sig["public_key_hex"]))
    message = (sig["domain"] + sig["manifest_canonical"]).encode("utf-8")
    public.verify(base64.b64decode(sig["signature_base64"], validate=True), message)
    with pytest.raises(InvalidSignature):
        public.verify(base64.b64decode(sig["signature_base64"]), message + b"x")
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sig["private_key_seed_hex"]))
    assert private.sign(message) == base64.b64decode(sig["signature_base64"])  # deterministic
    assert hashlib.sha256(bytes.fromhex(sig["public_key_hex"])).hexdigest()[:16] == sig["key_id"]
    assert sig["algorithm"] == "Ed25519"


def test_utc_vectors() -> None:
    from shape import compat

    for vec in DATA["utc"]:
        if vec["utc_iso"] is None:
            with pytest.raises(ValueError):
                compat.parse_utc_iso(vec["input"])
        else:
            assert compat.utc_iso(compat.parse_utc_iso(vec["input"])) == vec["utc_iso"]


def test_the_reference_implementation_reproduces_every_codec_vector() -> None:
    from shape.artifact import codec

    for vec in DATA["codec"]:
        assert (
            codec.dumps(codec.loads(vec["input_json"]), sort_keys=True).decode()
            == (vec["canonical"])
        )
