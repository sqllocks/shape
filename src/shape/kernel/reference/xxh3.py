"""XXH3-64 with a seed, in pure Python (no dependency).

The reference kernel hashes through the ``xxhash`` package when it is installed (fast, and a
``[dev]`` dependency, T-03). This module is the fallback for environments without it, such as
the pure ``py3-none-any`` wheel, so that hashes are identical everywhere. It is a direct port
of the reference algorithm (XXH3 0.8, 64-bit, ``XXH3_64bits_withSeed``) and is tested against
``xxhash`` and the Rust kernel on every input length class.
"""

from __future__ import annotations

import struct

M64 = 0xFFFFFFFFFFFFFFFF
M32 = 0xFFFFFFFF
PRIME32_1 = 0x9E3779B1
PRIME32_2 = 0x85EBCA77
PRIME32_3 = 0xC2B2AE3D
PRIME64_1 = 0x9E3779B185EBCA87
PRIME64_2 = 0xC2B2AE3D27D4EB4F
PRIME64_3 = 0x165667B19E3779F9
PRIME64_4 = 0x85EBCA77C2B2AE63
PRIME64_5 = 0x27D4EB2F165667C5

STRIPE_LEN = 64
SECRET_CONSUME_RATE = 8
ACC_NB = 8
SECRET_MERGEACCS_START = 11
SECRET_LASTACC_START = 7
MID_SIZE_MAX = 240
SECRET_SIZE_MIN = 136
DEFAULT_SECRET_SIZE = 192

DEFAULT_SECRET = bytes(
    [
        0xB8,
        0xFE,
        0x6C,
        0x39,
        0x23,
        0xA4,
        0x4B,
        0xBE,
        0x7C,
        0x01,
        0x81,
        0x2C,
        0xF7,
        0x21,
        0xAD,
        0x1C,
        0xDE,
        0xD4,
        0x6D,
        0xE9,
        0x83,
        0x90,
        0x97,
        0xDB,
        0x72,
        0x40,
        0xA4,
        0xA4,
        0xB7,
        0xB3,
        0x67,
        0x1F,
        0xCB,
        0x79,
        0xE6,
        0x4E,
        0xCC,
        0xC0,
        0xE5,
        0x78,
        0x82,
        0x5A,
        0xD0,
        0x7D,
        0xCC,
        0xFF,
        0x72,
        0x21,
        0xB8,
        0x08,
        0x46,
        0x74,
        0xF7,
        0x43,
        0x24,
        0x8E,
        0xE0,
        0x35,
        0x90,
        0xE6,
        0x81,
        0x3A,
        0x26,
        0x4C,
        0x3C,
        0x28,
        0x52,
        0xBB,
        0x91,
        0xC3,
        0x00,
        0xCB,
        0x88,
        0xD0,
        0x65,
        0x8B,
        0x1B,
        0x53,
        0x2E,
        0xA3,
        0x71,
        0x64,
        0x48,
        0x97,
        0xA2,
        0x0D,
        0xF9,
        0x4E,
        0x38,
        0x19,
        0xEF,
        0x46,
        0xA9,
        0xDE,
        0xAC,
        0xD8,
        0xA8,
        0xFA,
        0x76,
        0x3F,
        0xE3,
        0x9C,
        0x34,
        0x3F,
        0xF9,
        0xDC,
        0xBB,
        0xC7,
        0xC7,
        0x0B,
        0x4F,
        0x1D,
        0x8A,
        0x51,
        0xE0,
        0x4B,
        0xCD,
        0xB4,
        0x59,
        0x31,
        0xC8,
        0x9F,
        0x7E,
        0xC9,
        0xD9,
        0x78,
        0x73,
        0x64,
        0xEA,
        0xC5,
        0xAC,
        0x83,
        0x34,
        0xD3,
        0xEB,
        0xC3,
        0xC5,
        0x81,
        0xA0,
        0xFF,
        0xFA,
        0x13,
        0x63,
        0xEB,
        0x17,
        0x0D,
        0xDD,
        0x51,
        0xB7,
        0xF0,
        0xDA,
        0x49,
        0xD3,
        0x16,
        0x55,
        0x26,
        0x29,
        0xD4,
        0x68,
        0x9E,
        0x2B,
        0x16,
        0xBE,
        0x58,
        0x7D,
        0x47,
        0xA1,
        0xFC,
        0x8F,
        0xF8,
        0xB8,
        0xD1,
        0x7A,
        0xD0,
        0x31,
        0xCE,
        0x45,
        0xCB,
        0x3A,
        0x8F,
        0x95,
        0x16,
        0x04,
        0x28,
        0xAF,
        0xD7,
        0xFB,
        0xCA,
        0xBB,
        0x4B,
        0x40,
        0x7E,
    ]
)

_INITIAL_ACC = (
    PRIME32_3,
    PRIME64_1,
    PRIME64_2,
    PRIME64_3,
    PRIME64_4,
    PRIME32_2,
    PRIME64_5,
    PRIME32_1,
)
_U64 = struct.Struct("<Q")
_U32 = struct.Struct("<I")


def _r64(b: bytes, o: int) -> int:
    return int(_U64.unpack_from(b, o)[0])


def _r32(b: bytes, o: int) -> int:
    return int(_U32.unpack_from(b, o)[0])


def _swap64(x: int) -> int:
    return int.from_bytes(x.to_bytes(8, "little"), "big")


def _swap32(x: int) -> int:
    return int.from_bytes(x.to_bytes(4, "little"), "big")


def _rotl64(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & M64


def _mul128_fold64(a: int, b: int) -> int:
    p = a * b
    return (p & M64) ^ (p >> 64)


def _xxh64_avalanche(h: int) -> int:
    h ^= h >> 33
    h = (h * PRIME64_2) & M64
    h ^= h >> 29
    h = (h * PRIME64_3) & M64
    return h ^ (h >> 32)


def _avalanche(h: int) -> int:
    h ^= h >> 37
    h = (h * 0x165667919E3779F9) & M64
    return h ^ (h >> 32)


def _strong_avalanche(h: int, length: int) -> int:
    h ^= _rotl64(h, 49) ^ _rotl64(h, 24)
    h = (h * 0x9FB21C651E98DF25) & M64
    h ^= ((h >> 35) + length) & M64
    h = (h * 0x9FB21C651E98DF25) & M64
    return h ^ (h >> 28)


def _mix16(data: bytes, o: int, secret: bytes, so: int, seed: int) -> int:
    lo = _r64(data, o) ^ ((_r64(secret, so) + seed) & M64)
    hi = _r64(data, o + 8) ^ ((_r64(secret, so + 8) - seed) & M64)
    return _mul128_fold64(lo, hi)


def _custom_secret(seed: int) -> bytes:
    if seed == 0:
        return DEFAULT_SECRET
    out = bytearray()
    for i in range(DEFAULT_SECRET_SIZE // 16):
        out += ((_r64(DEFAULT_SECRET, i * 16) + seed) & M64).to_bytes(8, "little")
        out += ((_r64(DEFAULT_SECRET, i * 16 + 8) - seed) & M64).to_bytes(8, "little")
    return bytes(out)


def _len_0_to_16(data: bytes, seed: int) -> int:
    n = len(data)
    s = DEFAULT_SECRET
    if n > 8:
        flip1 = ((_r64(s, 24) ^ _r64(s, 32)) + seed) & M64
        flip2 = ((_r64(s, 40) ^ _r64(s, 48)) - seed) & M64
        lo = _r64(data, 0) ^ flip1
        hi = _r64(data, n - 8) ^ flip2
        acc = (n + _swap64(lo) + hi + _mul128_fold64(lo, hi)) & M64
        return _avalanche(acc)
    if n >= 4:
        seed ^= _swap32(seed & M32) << 32
        in1 = _r32(data, 0)
        in2 = _r32(data, n - 4)
        flip = ((_r64(s, 8) ^ _r64(s, 16)) - seed) & M64
        keyed = ((in2 + (in1 << 32)) & M64) ^ flip
        return _strong_avalanche(keyed, n)
    if n > 0:
        combo = (data[0] << 16) | (data[n >> 1] << 24) | data[n - 1] | (n << 8)
        flip = ((_r32(s, 0) ^ _r32(s, 4)) + seed) & M64
        return _xxh64_avalanche(combo ^ flip)
    return _xxh64_avalanche(seed ^ _r64(s, 56) ^ _r64(s, 64))


def _len_17_to_128(data: bytes, seed: int) -> int:
    n = len(data)
    s = DEFAULT_SECRET
    acc = (n * PRIME64_1) & M64
    if n > 32:
        if n > 64:
            if n > 96:
                acc += _mix16(data, 48, s, 96, seed)
                acc += _mix16(data, n - 64, s, 112, seed)
            acc += _mix16(data, 32, s, 64, seed)
            acc += _mix16(data, n - 48, s, 80, seed)
        acc += _mix16(data, 16, s, 32, seed)
        acc += _mix16(data, n - 32, s, 48, seed)
    acc += _mix16(data, 0, s, 0, seed)
    acc += _mix16(data, n - 16, s, 16, seed)
    return _avalanche(acc & M64)


def _len_129_to_240(data: bytes, seed: int) -> int:
    n = len(data)
    s = DEFAULT_SECRET
    acc = (n * PRIME64_1) & M64
    rounds = n // 16
    for i in range(8):
        acc += _mix16(data, 16 * i, s, 16 * i, seed)
    acc = _avalanche(acc & M64)
    for i in range(8, rounds):
        acc += _mix16(data, 16 * i, s, 16 * (i - 8) + 3, seed)
    acc += _mix16(data, n - 16, s, SECRET_SIZE_MIN - 17, seed)
    return _avalanche(acc & M64)


def _accumulate_512(acc: list[int], data: bytes, o: int, secret: bytes, so: int) -> None:
    for i in range(ACC_NB):
        dv = _r64(data, o + 8 * i)
        dk = dv ^ _r64(secret, so + 8 * i)
        acc[i ^ 1] = (acc[i ^ 1] + dv) & M64
        acc[i] = (acc[i] + (dk & M32) * (dk >> 32)) & M64


def _scramble(acc: list[int], secret: bytes, so: int) -> None:
    for i in range(ACC_NB):
        a = acc[i]
        a ^= a >> 47
        a ^= _r64(secret, so + 8 * i)
        acc[i] = (a * PRIME32_1) & M64


def _merge_accs(acc: list[int], secret: bytes, so: int, start: int) -> int:
    result = start
    for i in range(4):
        result += _mul128_fold64(
            acc[2 * i] ^ _r64(secret, so + 16 * i), acc[2 * i + 1] ^ _r64(secret, so + 16 * i + 8)
        )
    return _avalanche(result & M64)


def _long(data: bytes, seed: int) -> int:
    n = len(data)
    secret = _custom_secret(seed)
    acc = list(_INITIAL_ACC)
    stripes_per_block = (len(secret) - STRIPE_LEN) // SECRET_CONSUME_RATE
    block_len = STRIPE_LEN * stripes_per_block
    blocks = (n - 1) // block_len
    for b in range(blocks):
        for s in range(stripes_per_block):
            _accumulate_512(
                acc, data, b * block_len + s * STRIPE_LEN, secret, s * SECRET_CONSUME_RATE
            )
        _scramble(acc, secret, len(secret) - STRIPE_LEN)
    stripes = ((n - 1) - block_len * blocks) // STRIPE_LEN
    for s in range(stripes):
        _accumulate_512(
            acc, data, blocks * block_len + s * STRIPE_LEN, secret, s * SECRET_CONSUME_RATE
        )
    _accumulate_512(
        acc, data, n - STRIPE_LEN, secret, len(secret) - STRIPE_LEN - SECRET_LASTACC_START
    )
    return _merge_accs(acc, secret, SECRET_MERGEACCS_START, (n * PRIME64_1) & M64)


def xxh3_64_with_seed(data: bytes, seed: int = 0) -> int:
    """XXH3-64 of ``data`` with a 64-bit ``seed``."""
    seed &= M64
    n = len(data)
    if n <= 16:
        return _len_0_to_16(data, seed)
    if n <= 128:
        return _len_17_to_128(data, seed)
    if n <= MID_SIZE_MAX:
        return _len_129_to_240(data, seed)
    return _long(data, seed)
