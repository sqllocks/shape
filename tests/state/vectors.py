"""Build the language-neutral test vectors for the canonical forms and content ids.

    python tests/state/vectors.py            # check docs/specs/vectors/state_vectors.json
    python tests/state/vectors.py --write    # rewrite it

``docs/specs/STATE_AND_COMPATIBILITY.md`` ("Canonical forms") states the rules in prose; the file
holds inputs as JSON text (so any language can parse them) and the exact output bytes (as text,
and their SHA-256), produced by the reference implementation. An implementation in any language
is conformant when it reproduces every ``canonical`` and ``sha256`` below and fails every entry of
``errors``. ``tests/state/test_vectors.py`` re-derives the codec and manifest vectors with a
second, stdlib-only implementation written from the prose, so the file and the rules agree.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "docs" / "specs" / "vectors" / "state_vectors.json"

# Inputs as JSON text. The codec reads these tagged forms back into values (NaN, tuples, mappings
# with a key that starts with ``$``); the canonical form is what ``codec.dumps(sort_keys=True)``
# writes for the value they decode to.
CODEC_INPUTS: list[tuple[str, str, str]] = [
    ("empty-object", "{}", "an empty object"),
    ("key-order", '{"b":1,"a":2,"B":3,"_":4,"é":5}', "keys sorted by Unicode code point"),
    (
        "key-order-astral",
        '{"\\ud83d\\ude00":1,"\\uffff":2,"z":3}',
        "code point order, not UTF-16 order: U+FFFF sorts before U+1F600",
    ),
    ("nesting", '{"z":{"y":[1,{"b":null,"a":true}],"x":false},"a":[]}', "nested objects sorted"),
    ("whitespace", ' { "a" : [ 1 , 2 ] , "b" : "x" } ', "insignificant whitespace is dropped"),
    ("unicode-text", '{"name":"Zoë \\u00e9 \\u4e2d\\u6587 😀"}', "non-ASCII is written as UTF-8"),
    (
        "escapes",
        '{"s":"quote\\" back\\\\ nl\\n tab\\t cr\\r bs\\b ff\\f nul\\u0000 us\\u001f del\\u007f"}',
        "short escapes for quote, backslash, \\n \\r \\t \\b \\f; \\u00XX for the other C0 "
        "controls; DEL raw",
    ),
    ("separators", '{"line":"a\\u2028b\\u2029c"}', "U+2028 and U+2029 are written raw"),
    ("integers", "[0,-1,9007199254740993,18446744073709551616]", "integers are exact, any size"),
    ("floats", "[0.1,1.5,-2.25,100.0,1e22,1e-7,1.7976931348623157e308,5e-324]", "float repr"),
    ("float-integral", "[1.0,-0.0,0.0,1e16,123456789012345680.0]", "integral floats keep .0"),
    ("bools-null", "[true,false,null]", "literals"),
    ("nan", '{"x":{"$float":"nan"}}', "a non-finite float is a tagged object"),
    ("infinities", '[{"$float":"inf"},{"$float":"-inf"}]', "both infinities"),
    ("tuple", '{"t":{"$tuple":[1,"a",{"$tuple":[]}]}}', "a tuple is a tagged object"),
    (
        "dollar-keys",
        '{"$dict":[["$float","not a tag"],["a",1]]}',
        "a mapping with a $ key is written as $dict pairs, in insertion order",
    ),
    ("deep-list", "[[[[[[[[1]]]]]]]]", "nesting depth"),
]

ERRORS: list[tuple[str, str, str]] = [
    ("bare-nan", '{"x":NaN}', "bare NaN is not JSON and is refused"),
    ("bare-infinity", '{"x":Infinity}', "bare Infinity is refused"),
    ("bare-minus-infinity", '{"x":-Infinity}', "bare -Infinity is refused"),
    ("bad-float-tag", '{"$float":"1.5"}', "$float takes nan, inf or -inf only"),
    ("tag-with-neighbours", '{"$float":"nan","a":1}', "a tag key may only appear alone"),
    ("tuple-not-a-list", '{"$tuple":3}', "$tuple needs a list"),
    ("dict-not-pairs", '{"$dict":[["a"]]}', "$dict needs [[key, value], ...]"),
    ("unterminated", '{"a":', "not JSON"),
]

MANIFESTS: list[tuple[str, dict[str, Any], str]] = [
    (
        "minimal",
        {"format": "shape", "version": 2, "format_version": 2, "name": "m"},
        "the manifest: sorted keys, no spaces",
    ),
    (
        "with-hashes",
        {
            "content_hashes": {"shape.json": "0" * 64},
            "format": "shape",
            "format_version": 2,
            "name": "ünïcode",
            "shape_content_id": "0" * 64,
            "metadata": {"b": [1, 2], "a": None},
        },
        "content hashes and metadata",
    ),
]

DOCUMENTS: list[tuple[str, str, dict[str, Any], str]] = [
    (
        "safe-profile-new-keys",
        "safe-profile",
        {
            "format": "shape-safe-profile",
            "version": 1,
            "schema_version": 1,
            "shape_version": "1.0.0",
            "min_shape_version": "0.9.0",
            "unsafe": False,
            "tables": {},
            "relationships": [],
            "redaction_manifest": {},
        },
        "declaration, old key and migration record are not part of the content id",
    ),
    (
        "safe-profile-old-keys",
        "safe-profile",
        {
            "schema_version": 1,
            "unsafe": False,
            "tables": {},
            "relationships": [],
            "redaction_manifest": {},
        },
        "the same document written before the unified keys: the same content id",
    ),
    (
        "run-manifest",
        "run-manifest",
        {"run_id": "r", "spec_hash": "", "pack_id": "p", "seed": 1, "version": 1},
        "a document of another kind",
    ),
]

BODIES: list[tuple[str, str, str]] = [
    (
        "empty-model-body",
        '{"schema_version":2,"tables":{}}',
        "shape_content_id is the SHA-256 of the body bytes as written",
    ),
]

UTC: list[tuple[str, str | None, str]] = [
    ("2026-10-03T04:05:06Z", "2026-10-03T04:05:06Z", "whole seconds: no fraction"),
    ("2026-10-03T04:05:06.123456Z", "2026-10-03T04:05:06.123456Z", "microseconds"),
    ("2026-10-03T06:05:06+02:00", "2026-10-03T04:05:06Z", "an offset becomes UTC"),
    ("2026-10-03T04:05:06+00:00", "2026-10-03T04:05:06Z", "+00:00 is read, Z is written"),
    ("2026-10-03T04:05:06", None, "a local (naive) time is refused"),
    ("2026-10-03 04:05:06Z", None, "a space is not T"),
    ("03/10/2026", None, "a locale form is refused"),
]

SIGNING_SEED = b"\x01" * 32  # a published test key, for these vectors only


def _hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build() -> dict[str, Any]:
    from shape import compat, migrate
    from shape.artifact import codec, signing
    from shape.artifact.io import canonical_json

    codec_vectors = []
    for id_, text, why in CODEC_INPUTS:
        canonical = codec.dumps(codec.loads(text), sort_keys=True)
        codec_vectors.append(
            {
                "id": id_,
                "why": why,
                "input_json": text,
                "canonical": canonical.decode("utf-8"),
                "canonical_hex": canonical.hex(),
                "sha256": _hex(canonical),
            }
        )
    error_vectors = []
    for id_, text, why in ERRORS:
        try:
            codec.loads(text)
        except (ValueError, TypeError):
            pass
        else:  # pragma: no cover - a defect in the vectors
            raise SystemExit(f"error vector {id_} did not fail")
        error_vectors.append({"id": id_, "why": why, "input_json": text})
    manifests = []
    for id_, manifest, why in MANIFESTS:
        canonical = canonical_json(manifest)
        manifests.append(
            {
                "id": id_,
                "why": why,
                "manifest": manifest,
                "canonical": canonical.decode("utf-8"),
                "sha256": _hex(canonical),
            }
        )
    documents = []
    for id_, kind, doc, why in DOCUMENTS:
        documents.append(
            {
                "id": id_,
                "why": why,
                "kind": kind,
                "document": doc,
                "content_id": migrate.document_content_id(kind, doc),
            }
        )
    bodies = [
        {"id": id_, "why": why, "body": text, "shape_content_id": _hex(text.encode("utf-8"))}
        for id_, text, why in BODIES
    ]
    utc = []
    for text, out, why in UTC:
        try:
            got: str | None = compat.utc_iso(compat.parse_utc_iso(text))
        except ValueError:
            got = None
        if got != out:  # pragma: no cover - a defect in the vectors
            raise SystemExit(f"utc vector {text!r}: {got!r} != {out!r}")
        utc.append({"input": text, "utc_iso": out, "why": why})

    public = signing.public_key_of(SIGNING_SEED)
    signed_manifest = canonical_json({"format": "shape", "format_version": 2, "name": "vector"})
    from shape.security.crypto import sign_ed25519

    signature = sign_ed25519(signing._message(signed_manifest), SIGNING_SEED)
    import base64

    return {
        "format": "shape-test-vectors",
        "version": 1,
        "description": "Language-neutral vectors for the canonical forms and content ids of "
        "docs/specs/STATE_AND_COMPATIBILITY.md. Regenerate with tests/state/vectors.py --write; "
        "a change to any vector is a format change and needs a version bump.",
        "codec": codec_vectors,
        "codec_errors": error_vectors,
        "manifests": manifests,
        "documents": documents,
        "bodies": bodies,
        "utc": utc,
        "signature": {
            "why": "Ed25519 (deterministic, RFC 8032) over domain + canonical manifest bytes",
            "algorithm": signing.ALGORITHM,
            "domain": signing.DOMAIN.decode("utf-8"),
            "manifest_canonical": signed_manifest.decode("utf-8"),
            "private_key_seed_hex": SIGNING_SEED.hex(),
            "public_key_hex": public.hex(),
            "key_id": signing.key_id(public),
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
    }


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    text = render(build())
    if "--write" in sys.argv:
        TARGET.write_text(text, encoding="utf-8")
        print(f"wrote {TARGET}")
    elif TARGET.read_text(encoding="utf-8") != text:
        raise SystemExit(f"{TARGET} is out of date: run with --write")
