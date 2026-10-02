"""Artifact fuzzer (P7-04).

Two parts:

* ``artifact_cases``: the fixed corpus of malformed ``.shape`` containers.
* A seeded mutation fuzzer over every parser that reads data an attacker can write: ``.shape``
  containers, signatures, manifests, profile artifacts and JSON, contracts, and scenario
  pack / GSL YAML. Each target must either accept the input or raise one of the documented
  *rejection* errors (:data:`REJECTED`). Any other exception (``TypeError``, ``RecursionError``,
  ``MemoryError``, ...) or a run past :data:`TIME_LIMIT_S` is a finding.

Everything derives from the seed, so a finding replays with the same ``(seed, iterations)``.
The smoke run is in the normal test suite (``tests/validation/test_fuzz_smoke.py``); the nightly
job runs ``python scripts/fuzz_artifacts.py`` with a fresh seed and many more iterations.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import random
import signal
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

# What a parser may raise for a hostile input: its own rejection types. ``zipfile.BadZipFile`` is
# a rejection too (the artifact reader re-raises it as ``ArtifactFormatError``). Nothing else.
REJECTED: tuple[type[BaseException], ...] = (ShapeError, ValueError, zipfile.BadZipFile)
TIME_LIMIT_S = 5.0


def artifact_cases() -> dict[str, bytes]:
    cases: dict[str, bytes] = {}
    cases["not_zip"] = b"not a zip"
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("../x", b"x")
        z.writestr("manifest.json", '{"content_hashes":{}}')
    cases["traversal"] = b.getvalue()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", '{"content_hashes":{"x":"deadbeef"}}')
        z.writestr("x", b"x")
    cases["bad_hash"] = b.getvalue()
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", b"{")
    cases["bad_json"] = b.getvalue()
    return cases


# ---- findings ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    target: str
    seed: int
    iteration: int
    error: str
    input: bytes

    def __str__(self) -> str:
        return f"{self.target} seed={self.seed} iteration={self.iteration}: {self.error}"


class _Timeout(BaseException):
    """Raised by the alarm; a BaseException so a parser's ``except Exception`` cannot hide it."""


@contextlib.contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    # SIGALRM works only on the main thread of a POSIX process; elsewhere the limit is not enforced.
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _fire(signum: int, frame: Any) -> None:
        raise _Timeout(f"no result after {seconds}s")

    old = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# ---- mutators ----------------------------------------------------------------------------------

_NASTY_JSON: tuple[Any, ...] = (
    None,
    True,
    -1,
    0,
    2**63,
    10**400,
    1e308,
    -0.0,
    "",
    "\x00",
    "../../etc/passwd",
    "a" * 70_000,
    [],
    {},
    [[]],
    {"": ""},
    "\ud800",
)


def _nest(depth: int, leaf: Any = 0) -> Any:
    x = leaf
    for _ in range(depth):
        x = [x]
    return x


def mutate_bytes(rng: random.Random, data: bytes) -> bytes:
    """One to three byte-level edits: flip, replace, truncate, insert, delete, duplicate."""
    b = bytearray(data)
    for _ in range(rng.randint(1, 3)):
        if not b:
            b.extend(rng.randbytes(rng.randint(1, 8)))
            continue
        op = rng.randrange(6)
        i = rng.randrange(len(b))
        if op == 0:
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1:
            b[i] = rng.randrange(256)
        elif op == 2:
            del b[i:]
        elif op == 3:
            b[i:i] = rng.randbytes(rng.randint(1, 16))
        elif op == 4:
            del b[i : i + rng.randint(1, 16)]
        else:
            j = min(len(b), i + rng.randint(1, 64))
            b[i:i] = b[i:j]
    return bytes(b)


def mutate_json(rng: random.Random, obj: Any, depth: int = 0) -> Any:
    """Replace, drop or add a random node of a JSON document."""
    if isinstance(obj, dict) and obj and rng.random() < 0.8:
        out = dict(obj)
        k = rng.choice(sorted(out))
        r = rng.random()
        if r < 0.15:
            del out[k]
        elif r < 0.25:
            out[rng.choice(("x", "../x", "", "__proto__", "\x00"))] = rng.choice(_NASTY_JSON)
        else:
            out[k] = mutate_json(rng, out[k], depth + 1)
        return out
    if isinstance(obj, list) and obj and rng.random() < 0.7:
        out_l = list(obj)
        i = rng.randrange(len(out_l))
        if rng.random() < 0.2:
            del out_l[i]
        else:
            out_l[i] = mutate_json(rng, out_l[i], depth + 1)
        return out_l
    r = rng.random()
    if r < 0.1:
        return _nest(rng.choice((50, 1_500, 20_000)))
    if r < 0.2:
        return {"a": _nest(rng.choice((50, 1_500)))}
    return rng.choice(_NASTY_JSON)


def _dump_json(rng: random.Random, obj: Any) -> bytes:
    """JSON text of ``obj``; sometimes with non-standard tokens a lenient parser accepts."""
    try:
        text = json.dumps(obj, sort_keys=True)
    except (ValueError, TypeError, RecursionError):
        return b"{"
    if rng.random() < 0.1:
        text = text.replace("0", "NaN", 1) if rng.random() < 0.5 else text.replace("1", "Infinity", 1)
    return text.encode("utf-8", "surrogatepass")


def _rewrap(
    members: dict[str, bytes], rng: random.Random, *, rehash: bool, method: int | None = None
) -> bytes:
    """A zip of ``members``; with ``rehash`` the manifest's hashes are made to match first, so
    the mutation reaches the parsers behind the checksum instead of stopping at it."""
    members = dict(members)
    if rehash and "manifest.json" in members:
        with contextlib.suppress(ValueError, TypeError):
            m = json.loads(members["manifest.json"])
            if isinstance(m, dict) and isinstance(m.get("content_hashes"), dict):
                m["content_hashes"] = {
                    k: hashlib.sha256(members[k]).hexdigest()
                    for k in m["content_hashes"]
                    if k in members
                }
                for comp in ("shape.json", "profile.json"):
                    if comp in members and "shape_content_id" in m:
                        m["shape_content_id"] = hashlib.sha256(members[comp]).hexdigest()
                members["manifest.json"] = json.dumps(m, sort_keys=True).encode()
    out = io.BytesIO()
    compression = rng.choice((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)) if method is None else method
    with zipfile.ZipFile(out, "w", compression) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


# ---- seeds -------------------------------------------------------------------------------------


def _seed_model() -> dict[str, Any]:
    return {
        "tables": {
            "orders": {
                "row_count": 100,
                "columns": {
                    "id": {"dtype": "int64", "stats": {"min": 1, "max": 100}},
                    "status": {"dtype": "string", "values": {"open": 60, "closed": 40}},
                },
            }
        }
    }


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


@dataclass
class Seeds:
    """Valid inputs the mutators start from, built once in a scratch directory."""

    shape: dict[str, bytes]
    signed: dict[str, bytes]
    profile: dict[str, bytes]
    public_key: bytes | None
    contract: dict[str, Any]
    safe_profile: dict[str, Any]
    pack_yaml: str
    gsl_yaml: str


_PACK_YAML = """\
pack_version: 1
id: orders_daily
kind: file_drop
domain: retail
description: daily drop
file_drop:
  cadence: daily
  partitioning: dt=YYYY-MM-DD
  formats: [parquet, csv]
chaos:
  enabled: true
"""

_GSL_YAML = """\
version: 1
name: demo
schema:
  type: domain
  domain: retail
scenario:
  pack: orders_daily
  scale: small
  seed: 7
outputs:
  lakehouse:
    mode: files
    tables: [orders]
    landing_zone:
      root: Files/landing
validation:
  gates: [schema]
"""


def build_seeds(scratch: Path) -> Seeds:
    from shape.artifact import write_model

    shape_path = scratch / "seed.shape"
    write_model(shape_path, _seed_model(), name="seed")
    signed: dict[str, bytes] = {}
    pub: bytes | None = None
    try:
        from shape.artifact import sign_artifact
        from shape.artifact.signing import generate_keypair

        sk, pub = generate_keypair()
        signed_path = scratch / "signed.shape"
        write_model(signed_path, _seed_model(), name="seed")
        sign_artifact(signed_path, sk)
        signed = _members(signed_path)
    except ImportError:  # no `cryptography`: the signature targets are skipped
        pub = None
    profile: dict[str, bytes] = {}
    try:
        from shape.artifact import codec
        from shape.artifact.io import write_artifact

        body = codec.dumps(
            {"row_count": 100, "columns": {"id": {"dtype": "int64", "stats": {"min": 1}}}},
            sort_keys=False,
        )
        prof_path = scratch / "profile.shape"
        write_artifact(
            prof_path,
            {
                "format": "shape",
                "format_version": 1,
                "kind": "profile",
                "name": "seed",
                "shape_content_id": hashlib.sha256(body).hexdigest(),
            },
            {"profile.json": body},
        )
        profile = _members(prof_path)
    except ImportError:
        profile = {}
    return Seeds(
        shape=_members(shape_path),
        signed=signed,
        profile=profile,
        public_key=pub,
        contract={
            "row_count": {"min": 1, "max": 1000},
            "required_columns": ["id"],
            "columns": {"id": {"min": 1, "max": 100, "allowed_values": [1, 2, 3]}},
        },
        safe_profile={
            "schema_version": 1,
            "unsafe": False,
            "tables": {"orders": {"row_count": 100, "columns": {"id": {"dtype": "int64"}}}},
        },
        pack_yaml=_PACK_YAML,
        gsl_yaml=_GSL_YAML,
    )


# ---- targets -----------------------------------------------------------------------------------

Target = Callable[[bytes, Path, Seeds], None]


def _write(scratch: Path, name: str, data: bytes) -> Path:
    p = scratch / name
    p.write_bytes(data)
    return p


def _t_container(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.artifact import read_model
    from shape.artifact.io import read_artifact

    p = _write(scratch, "c.shape", data)
    read_artifact(p)
    read_model(p)


def _t_signature(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.artifact import verify_artifact

    assert seeds.public_key is not None
    verify_artifact(_write(scratch, "s.shape", data), seeds.public_key)


def _t_profile_artifact(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.profile.reference.profile import load

    prof = load(_write(scratch, "p.shape", data))
    prof.to_dict()


def _t_profile_json(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.privacy.safe_validator import SafeProfileValidator

    SafeProfileValidator().validate_file(_write(scratch, "sp.json", data))


def _t_contract(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.contracts.v1 import _load_contract, _validate_contract

    _validate_contract(_load_contract(_write(scratch, "c.json", data)))


def _t_pack_yaml(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.scenario.loader import PackLoader

    PackLoader().load(_write(scratch, "pack.yaml", data))


def _t_gsl_yaml(data: bytes, scratch: Path, seeds: Seeds) -> None:
    from shape.scenario.gsl import GSLParser

    GSLParser().parse(_write(scratch, "spec.yaml", data))


# ---- input generators (one per target) -----------------------------------------------------------

Gen = Callable[[random.Random, Seeds], bytes]


def _pick(rng: random.Random, members: dict[str, bytes]) -> dict[str, bytes]:
    return dict(members)


def _g_container(rng: random.Random, s: Seeds) -> bytes:
    base = s.shape
    mode = rng.randrange(5)
    if mode == 0:  # byte-level damage to the whole file
        return mutate_bytes(rng, _rewrap(base, rng, rehash=False))
    members = dict(base)
    if mode == 1:  # manifest JSON mutated, hashes made to match
        m = json.loads(members["manifest.json"])
        members["manifest.json"] = _dump_json(rng, mutate_json(rng, m))
        return _rewrap(members, rng, rehash=rng.random() < 0.7)
    if mode == 2:  # model JSON mutated, hashes and content id made to match
        body = json.loads(members["shape.json"])
        members["shape.json"] = _dump_json(rng, mutate_json(rng, body))
        return _rewrap(members, rng, rehash=True)
    if mode == 3:  # member set changed: extra, renamed, duplicate-looking, unsafe names
        name = rng.choice(("../x", "/abs", "a\\b", "x\x00y", "C:evil", "./x", "a//b", "extra", ""))
        members[name] = rng.randbytes(rng.randint(0, 32))
        return _rewrap(members, rng, rehash=rng.random() < 0.5)
    members["shape.json"] = mutate_bytes(rng, members["shape.json"])  # raw model bytes damaged
    return _rewrap(members, rng, rehash=True)


def _g_signature(rng: random.Random, s: Seeds) -> bytes:
    members = dict(s.signed)
    mode = rng.randrange(4)
    if mode == 0:
        members["manifest.sig"] = mutate_bytes(rng, members["manifest.sig"])
    elif mode == 1:
        doc = json.loads(members["manifest.sig"])
        members["manifest.sig"] = _dump_json(rng, mutate_json(rng, doc))
    elif mode == 2:
        members["manifest.sig"] = rng.choice(
            (b"", b"[" * 5000, b"{" * 5000, b'{"algorithm":"Ed25519","signature":1}', b"null")
        )
    else:
        members["manifest.json"] = mutate_bytes(rng, members["manifest.json"])
    return _rewrap(members, rng, rehash=False)


def _g_profile_artifact(rng: random.Random, s: Seeds) -> bytes:
    members = dict(s.profile)
    if rng.random() < 0.3:
        return mutate_bytes(rng, _rewrap(members, rng, rehash=False))
    body = json.loads(members["profile.json"])
    members["profile.json"] = _dump_json(rng, mutate_json(rng, body))
    return _rewrap(members, rng, rehash=True)


def _g_json(seed_key: str) -> Gen:
    def gen(rng: random.Random, s: Seeds) -> bytes:
        doc = getattr(s, seed_key)
        if rng.random() < 0.25:
            return mutate_bytes(rng, json.dumps(doc).encode())
        return _dump_json(rng, mutate_json(rng, doc))

    return gen


def _g_yaml(seed_key: str) -> Gen:
    alias_bomb = (
        "a: &a [x, x, x, x, x, x, x, x, x]\n"
        + "".join(
            f"{chr(98 + i)}: &{chr(98 + i)} [" + ", ".join([f"*{chr(97 + i)}"] * 9) + "]\n"
            for i in range(8)
        )
    )

    def gen(rng: random.Random, s: Seeds) -> bytes:
        text = getattr(s, seed_key)
        mode = rng.randrange(6)
        if mode == 0:
            return mutate_bytes(rng, text.encode())
        if mode == 1:
            lines = text.splitlines()
            if lines:
                i = rng.randrange(len(lines))
                lines[i] = rng.choice(
                    (
                        "",
                        "  - " + "[" * 3000,
                        "x: !!python/object/apply:os.system ['true']",
                        "x: *missing",
                        "x: &a {y: *a}",
                        "? [a]\n: b",
                        "k: 0x" + "f" * 500,
                        "k: 1e999999",
                        "k: .inf",
                        "k: " + "9" * 5000,
                        "\t" + lines[i],
                    )
                )
            return "\n".join(lines).encode()
        if mode == 2:
            return alias_bomb.encode()
        if mode == 3:
            return (text + "\n" + text).encode()
        if mode == 4:
            return ("[" * rng.choice((100, 1_200, 10_000))).encode()
        return text.replace(":", rng.choice((": []", ": {}", ": null", ": 1", ": [[1]]")), 1).encode()

    return gen


@dataclass(frozen=True)
class _TargetSpec:
    name: str
    run: Target
    gen: Gen
    needs_sign: bool = False


TARGETS: tuple[_TargetSpec, ...] = (
    _TargetSpec("container", _t_container, _g_container),
    _TargetSpec("signature", _t_signature, _g_signature, needs_sign=True),
    _TargetSpec("profile-artifact", _t_profile_artifact, _g_profile_artifact),
    _TargetSpec("profile-json", _t_profile_json, _g_json("safe_profile")),
    _TargetSpec("contract", _t_contract, _g_json("contract")),
    _TargetSpec("pack-yaml", _t_pack_yaml, _g_yaml("pack_yaml")),
    _TargetSpec("gsl-yaml", _t_gsl_yaml, _g_yaml("gsl_yaml")),
)


def target_names() -> list[str]:
    return [t.name for t in TARGETS]


def run_fuzz(
    seed: int,
    iterations: int,
    targets: list[str] | None = None,
    *,
    time_limit: float = TIME_LIMIT_S,
) -> list[Finding]:
    """Run ``iterations`` mutated inputs per target; return the findings (empty when clean).

    Deterministic: the same ``seed`` and ``iterations`` generate the same inputs."""
    findings: list[Finding] = []
    selected = [t for t in TARGETS if targets is None or t.name in targets]
    unknown = set(targets or ()) - set(target_names())
    if unknown:
        raise ValueError(f"unknown fuzz targets: {sorted(unknown)}")
    with tempfile.TemporaryDirectory(prefix="shape-fuzz-") as d:
        scratch = Path(d)
        seeds = build_seeds(scratch)
        for spec in selected:
            if spec.needs_sign and seeds.public_key is None:
                continue
            if spec.name == "profile-artifact" and not seeds.profile:
                continue
            rng = random.Random(f"{seed}:{spec.name}")
            for i in range(iterations):
                data = spec.gen(rng, seeds)
                start = time.monotonic()
                try:
                    with _time_limit(time_limit):
                        spec.run(data, scratch, seeds)
                except REJECTED:
                    pass
                except _Timeout as e:
                    findings.append(Finding(spec.name, seed, i, f"timeout: {e}", data))
                except BaseException as e:  # noqa: BLE001 - every other outcome is a finding
                    if isinstance(e, KeyboardInterrupt | SystemExit):
                        raise
                    findings.append(
                        Finding(spec.name, seed, i, f"{type(e).__name__}: {str(e)[:200]}", data)
                    )
                else:
                    if time.monotonic() - start > time_limit:
                        findings.append(Finding(spec.name, seed, i, "slow: over the limit", data))
    return findings
