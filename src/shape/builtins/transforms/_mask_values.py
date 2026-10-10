"""Value generators and column-type detection for the ``mask`` transform.

A generator takes the *distinct* original values of one type (text) and returns one replacement
per original, in the same format: a phone number keeps its punctuation, a postcode its digit and
letter layout, an e-mail address stays an e-mail address, a card number stays Luhn-valid, an IBAN
keeps its country and a valid check digit. Replacements never equal the value they replace; for
identifier types they are also distinct from every original value of the type and from each other.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from functools import cache
from importlib import resources
from typing import cast

import numpy as np
import numpy.typing as npt

# ---- the types -----------------------------------------------------------------------------

TYPES: tuple[str, ...] = (
    "email",
    "phone",
    "first_name",
    "last_name",
    "name",
    "address",
    "city",
    "state",
    "zip",
    "ssn",
    "credit_card",
    "ip_address",
    "username",
    "date_of_birth",
    "iban",
)

# Types whose values are drawn from a small domain: a replacement must differ from the value it
# replaces, but may equal another original (a first name or a state is not an identifier).
CATEGORY_TYPES = frozenset({"first_name", "last_name", "city", "state", "date_of_birth"})

# Types that make sense for an integer column (the digits are replaced, the width is kept).
NUMERIC_TYPES = frozenset({"zip", "phone", "ssn", "credit_card"})

# Column-name phrases per type. A phrase matches when its words appear in order and next to each
# other among the words of the column name (`ship_zip_code`, `shipZipCode`, `ZIP4`); the longest
# matching phrase decides, so `ip_address` is an address of the network kind, not a street.
NAME_PHRASES: dict[str, tuple[str, ...]] = {
    "email": ("email", "email_address", "e_mail"),
    "phone": ("phone", "phone_number", "telephone", "mobile", "cell"),
    "name": ("full_name", "name", "given_name"),
    "first_name": ("first_name", "given_name", "fname", "forename"),
    "last_name": ("last_name", "surname", "family_name", "lname"),
    "address": ("address", "street", "street_address", "address_line"),
    "city": ("city", "town"),
    "state": ("state", "province", "region"),
    "zip": ("zip", "zip_code", "zipcode", "postal_code", "postcode"),
    "ssn": ("ssn", "social_security", "social_security_number", "sin"),
    "credit_card": ("credit_card", "card_number", "cc_number", "card_num"),
    "ip_address": ("ip", "ip_address", "ip_addr"),
    "username": ("username", "user_name", "login"),
    "date_of_birth": ("date_of_birth", "dob", "birth_date", "birthdate"),
    "iban": ("iban",),
}

# Names written without separators (`customeremail`, `ipaddress`) match by containment, but only
# for these long, unambiguous words.
FUSED_WORDS: dict[str, str] = {
    "email": "email",
    "phone": "phone",
    "telephone": "phone",
    "firstname": "first_name",
    "lastname": "last_name",
    "fullname": "name",
    "surname": "last_name",
    "zipcode": "zip",
    "postalcode": "zip",
    "postcode": "zip",
    "creditcard": "credit_card",
    "cardnumber": "credit_card",
    "socialsecurity": "ssn",
    "username": "username",
    "birthdate": "date_of_birth",
    "dateofbirth": "date_of_birth",
    "ipaddress": "ip_address",
    "address": "address",
    "iban": "iban",
}

# Value patterns found by the profile engine, and the type they mean (uuid is not personal data).
PATTERN_TYPES: dict[str, str | None] = {
    "email": "email",
    "phone": "phone",
    "ssn": "ssn",
    "ip_address": "ip_address",
    "iban": "iban",
    "postal_code": "zip",
    "cc": "credit_card",
    "uuid": None,
}


def name_words(column: str) -> list[str]:
    """The lower-case words of a column name, split at separators, case changes and digits."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", column)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", "_", spaced)
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w and not w.isdigit()]


@cache
def _phrases() -> list[tuple[int, int, str, tuple[str, ...]]]:
    """(length of the phrase's letters, declaration order, type, words), longest first."""
    out: list[tuple[int, int, str, tuple[str, ...]]] = []
    order = 0
    for type_name, phrases in NAME_PHRASES.items():
        for phrase in phrases:
            words = tuple(phrase.split("_"))
            out.append((len("".join(words)), order, type_name, words))
            order += 1
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def type_from_name(column: str) -> str | None:
    """The personal-data type a column name points to, or None."""
    words = name_words(column)
    if not words:
        return None
    for _, _, type_name, phrase in _phrases():
        n = len(phrase)
        if any(tuple(words[i : i + n]) == phrase for i in range(len(words) - n + 1)):
            return type_name
    if len(words) == 1:
        fused = words[0]
        best = max((w for w in FUSED_WORDS if w in fused), key=len, default=None)
        if best is not None:
            return FUSED_WORDS[best]
    return None


# ---- reference data ------------------------------------------------------------------------

_STATE_NAMES = (
    "Alabama,Alaska,Arizona,Arkansas,California,Colorado,Connecticut,Delaware,Florida,Georgia,"
    "Hawaii,Idaho,Illinois,Indiana,Iowa,Kansas,Kentucky,Louisiana,Maine,Maryland,Massachusetts,"
    "Michigan,Minnesota,Mississippi,Missouri,Montana,Nebraska,Nevada,New Hampshire,New Jersey,"
    "New Mexico,New York,North Carolina,North Dakota,Ohio,Oklahoma,Oregon,Pennsylvania,"
    "Rhode Island,South Carolina,South Dakota,Tennessee,Texas,Utah,Vermont,Virginia,Washington,"
    "West Virginia,Wisconsin,Wyoming"
).split(",")


@cache
def _pool(name: str) -> list[str]:
    path = resources.files("shape.builtins").joinpath("strategies", "pools", f"{name}.txt")
    return [line for line in path.read_text("utf-8").split("\n") if line]


def _pick(rng: np.random.Generator, pool: list[str], n: int) -> list[str]:
    return [pool[i] for i in rng.integers(0, len(pool), n).tolist()]


def _digits(rng: np.random.Generator, n: int, width: int) -> list[str]:
    return [f"{v:0{width}d}" for v in rng.integers(0, 10**width, n).tolist()]


def _style(original: str, value: str) -> str:
    """``value`` written in the case style of ``original`` (UPPER, lower, or as is)."""
    if original.isupper():
        return value.upper()
    if original.islower():
        return value.lower()
    return value


# ---- format-preserving replacement of digits and letters ------------------------------------

_LUHN_DOUBLE = (0, 2, 4, 6, 8, 1, 3, 5, 7, 9)


def _retemplate(
    originals: list[str],
    rng: np.random.Generator,
    letters: bool = True,
    keep_prefix: Callable[[str], int] | None = None,
) -> list[str]:
    """Every digit replaced by a random digit and (with ``letters``) every ASCII letter by a
    random letter of the same case; all other characters stay. A first digit that is not 0
    stays non-zero, so an integer keeps its width. ``keep_prefix(original)`` says how many
    leading characters are copied unchanged (a country code)."""
    n = len(originals)
    if n == 0:
        return []
    arr = np.array(originals, dtype="U")
    width = arr.dtype.itemsize // 4
    codes = arr.view(np.uint32).reshape(n, width).copy()
    is_digit = (codes >= 48) & (codes <= 57)
    new = codes.copy()
    new[is_digit] = 48 + rng.integers(0, 10, int(is_digit.sum()))
    first = codes[:, 0]
    lead = (first >= 49) & (first <= 57)
    new[lead, 0] = 49 + rng.integers(0, 9, int(lead.sum()))
    if letters:
        upper = (codes >= 65) & (codes <= 90)
        lower = (codes >= 97) & (codes <= 122)
        new[upper] = 65 + rng.integers(0, 26, int(upper.sum()))
        new[lower] = 97 + rng.integers(0, 26, int(lower.sum()))
    out = new.astype(np.uint32).view(f"U{width}").reshape(n).tolist()
    if keep_prefix is not None:
        out = [
            o[:k] + r[k:] if (k := keep_prefix(o)) else r
            for o, r in zip(originals, out, strict=True)
        ]
    return [str(v) for v in out]


def _phone_prefix(value: str) -> int:
    """Length of a leading ``+<country code>`` (kept as it is)."""
    m = re.match(r"\+\d+", value)
    return m.end() if m else 0


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        total += _LUHN_DOUBLE[d] if i % 2 else d
    return total % 10 == 0


def _luhn_fix(value: str) -> str:
    """``value`` with its last digit set so that the digits pass the Luhn check."""
    pos = [i for i, c in enumerate(value) if c.isdigit()]
    if len(pos) < 2:
        return value
    digits = [value[i] for i in pos]
    total = 0
    for i, ch in enumerate(reversed(digits[:-1]), start=1):
        d = int(ch)
        total += _LUHN_DOUBLE[d] if i % 2 else d
    digits[-1] = str((10 - total % 10) % 10)
    chars = list(value)
    for i, digit in zip(pos, digits, strict=True):
        chars[i] = digit
    return "".join(chars)


def _iban_check(compact: str) -> str:
    moved = compact[4:] + compact[:2] + "00"
    number = "".join(str(int(c, 36)) for c in moved)
    return f"{98 - int(number) % 97:02d}"


def _iban(originals: list[str], rng: np.random.Generator) -> list[str]:
    out = _retemplate(originals, rng, letters=True, keep_prefix=lambda _: 2)
    fixed: list[str] = []
    for orig, cand in zip(originals, out, strict=True):
        chars = list(cand.upper() if orig.isupper() else cand)
        pos = [i for i, c in enumerate(chars) if c.isalnum()]
        compact = "".join(chars[i] for i in pos).upper()
        if len(compact) > 4 and compact[:2].isalpha():
            check = _iban_check(compact)
            chars[pos[2]], chars[pos[3]] = check[0], check[1]
        fixed.append("".join(chars))
    return fixed


# ---- generators per type -------------------------------------------------------------------


# Reserved by RFC 2606: a masked address can never reach a real mailbox (as in generation, #11).
_RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")


def _email(originals: list[str], rng: np.random.Generator) -> list[str]:
    n = len(originals)
    first = [f.lower() for f in _pick(rng, _pool("first_names"), n)]
    last = [x.lower() for x in _pick(rng, _pool("last_names"), n)]
    sep = _pick(rng, ["", ".", "_"], n)
    num = _digits(rng, n, 3)
    domains = _pick(rng, list(_RESERVED_EMAIL_DOMAINS), n)
    return [
        f"{f}{s}{ln}{d}@{dom}"
        for f, s, ln, d, dom in zip(first, sep, last, num, domains, strict=True)
    ]


def _name(originals: list[str], rng: np.random.Generator) -> list[str]:
    n = len(originals)
    first = _pick(rng, _pool("first_names"), n)
    last = _pick(rng, _pool("last_names"), n)
    out = []
    for orig, f, ln in zip(originals, first, last, strict=True):
        if "," in orig:
            text = f"{ln}, {f}"
        elif len(orig.split()) < 2:
            text = f
        else:
            text = f"{f} {ln}"
        out.append(_style(orig, text))
    return out


def _pool_values(pool_name: str) -> Callable[[list[str], np.random.Generator], list[str]]:
    def make(originals: list[str], rng: np.random.Generator) -> list[str]:
        values = _pick(rng, _pool(pool_name), len(originals))
        return [_style(o, v) for o, v in zip(originals, values, strict=True)]

    return make


def _state(originals: list[str], rng: np.random.Generator) -> list[str]:
    abbreviations = _pool("us_states")
    out = []
    for orig, i in zip(originals, rng.integers(0, 1 << 30, len(originals)).tolist(), strict=True):
        full = len(orig.strip()) > 2
        out.append(
            _style(
                orig,
                _STATE_NAMES[i % len(_STATE_NAMES)]
                if full
                else abbreviations[i % len(abbreviations)],
            )
        )
    return out


def _address(originals: list[str], rng: np.random.Generator) -> list[str]:
    n = len(originals)
    number = rng.integers(1, 10_000, n).tolist()
    street = _pick(rng, _pool("street_names"), n)
    suffix = _pick(rng, _pool("street_suffixes"), n)
    return [
        _style(o, f"{a} {s} {x}")
        for o, a, s, x in zip(originals, number, street, suffix, strict=True)
    ]


def _ip(originals: list[str], rng: np.random.Generator) -> list[str]:
    out = []
    for orig in originals:
        if ":" in orig:  # IPv6: the same number of groups, each a random 16-bit value
            groups = orig.split(":")
            out.append(
                ":".join(
                    f"{v:x}" if g else ""
                    for g, v in zip(
                        groups, rng.integers(1, 65536, len(groups)).tolist(), strict=True
                    )
                )
            )
        else:
            a, b, c, d = rng.integers(1, 255, 4).tolist()
            out.append(f"{int(a)}.{int(b)}.{int(c)}.{int(d)}")
    return out


def _username(originals: list[str], rng: np.random.Generator) -> list[str]:
    n = len(originals)
    first = [f.lower() for f in _pick(rng, _pool("first_names"), n)]
    last = [x.lower() for x in _pick(rng, _pool("last_names"), n)]
    num = _digits(rng, n, 3)
    return [
        _style(o, f"{f[0]}{ln}{d}") for o, f, ln, d in zip(originals, first, last, num, strict=True)
    ]


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%Y%m%d", "%d-%b-%Y", "%b %d, %Y")


def _date_of_birth(originals: list[str], rng: np.random.Generator) -> list[str]:
    """A random date inside the range of the original dates, written in their format. Values
    that are not dates in any one common format fall back to a digit-for-digit replacement."""
    for fmt in _DATE_FORMATS:
        try:
            parsed = [dt.datetime.strptime(o, fmt).date() for o in originals]
        except ValueError:
            continue
        lo = min(parsed).toordinal()
        hi = max(parsed).toordinal()
        if hi == lo:
            hi = lo + 365
        days = rng.integers(lo, hi + 1, len(originals)).tolist()
        return [dt.date.fromordinal(d).strftime(fmt) for d in days]
    return _retemplate(originals, rng)


def _generic(originals: list[str], rng: np.random.Generator) -> list[str]:
    return _retemplate(originals, rng)


GENERATORS: dict[str, Callable[[list[str], np.random.Generator], list[str]]] = {
    "email": _email,
    "phone": lambda o, r: _retemplate(o, r, letters=False, keep_prefix=_phone_prefix),
    "first_name": _pool_values("first_names"),
    "last_name": _pool_values("last_names"),
    "name": _name,
    "address": _address,
    "city": _pool_values("us_cities"),
    "state": _state,
    "zip": _generic,
    "ssn": lambda o, r: _retemplate(o, r, letters=False),
    "credit_card": lambda o, r: [
        _luhn_fix(c) if _luhn_ok("".join(ch for ch in orig if ch.isdigit())) else c
        for orig, c in zip(o, _retemplate(o, r, letters=False), strict=True)
    ],
    "ip_address": _ip,
    "username": _username,
    "date_of_birth": _date_of_birth,
    "iban": _iban,
}


def replacements(
    type_name: str,
    originals: list[str],
    rng: np.random.Generator,
    max_rounds: int = 60,
) -> list[str]:
    """One replacement for each distinct original value, in order.

    Each replacement differs from its original. For identifier types it is also none of the
    originals of the type and distinct from every other replacement. A round draws candidates for
    the values still unresolved; running out of rounds (a domain too small for the number of
    distinct values) is an error, never a leak."""
    generate = GENERATORS[type_name]
    identifier = type_name not in CATEGORY_TYPES
    forbidden = set(originals) if identifier else set()
    used: set[str] = set()
    result: list[str | None] = [None] * len(originals)
    pending = list(range(len(originals)))
    for _ in range(max_rounds):
        if not pending:
            break
        candidates = generate([originals[i] for i in pending], rng)
        still: list[int] = []
        for i, cand in zip(pending, candidates, strict=True):
            ok = cand != originals[i]
            if ok and identifier:
                ok = cand not in forbidden and cand not in used
            if ok:
                result[i] = cand
                if identifier:
                    used.add(cand)
            else:
                still.append(i)
        pending = still
    if pending:
        raise ValueError(
            f"cannot find replacements for {len(pending)} of {len(originals)} distinct "
            f"{type_name} values that differ from the originals"
        )
    return [r for r in result if r is not None]


def float_replacements(
    values: npt.NDArray[np.float64], rng: np.random.Generator
) -> npt.NDArray[np.float64]:
    """Numbers drawn from a normal distribution with the mean and spread of ``values``, kept
    inside their range, each different from the value it replaces."""
    if values.size == 0:
        return values.copy()
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if values.size > 1 else 0.0
    lo, hi = (
        float(cast(Callable[[], np.float64], values.min)()),
        float(cast(Callable[[], np.float64], values.max)()),
    )
    scale = std if std > 0 else max(abs(mean), 1.0) * 0.1
    out = np.clip(rng.normal(mean, scale, values.size), lo, hi)
    for _ in range(20):
        same = out == values
        if not same.any():
            return out
        out[same] = np.clip(rng.normal(mean, scale, int(same.sum())), lo, hi)
    raise ValueError("cannot draw numbers that differ from the originals (a constant column?)")
