"""Whole-word matching of column and table names for the DDL import (P4-01c).

Names are split into words (``OrderDate`` and ``order_date`` both give ``order``, ``date``) and a
rule matches a *word* (or a run of words), never a part of a longer word: ``discount_pct`` does
not contain the word ``count`` and ``model`` does not contain ``mode``. A word also matches its
plural (``prices``, ``categories``).

Stable interface: :func:`snake`, :func:`words` and :func:`word_pattern`.
"""

from __future__ import annotations

import re


def snake(name: str) -> str:
    """``OrderDate`` -> ``order_date``, ``SalesOrderID`` -> ``sales_order_id``."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def words(name: str) -> list[str]:
    """The lower-case words of a snake_case, CamelCase or spaced name."""
    return [w for w in re.split(r"[^a-z0-9]+", snake(name)) if w]


def _alternative(term: str) -> str:
    """One term as a pattern: a whole word (or words) with an optional plural. A term ending in
    ``_`` is a prefix (``is_``): it needs a word after it. A term ending in ``*`` is a stem
    (``expir*``: expiry, expires, expiration)."""
    if term.endswith("_"):
        return re.escape(term)
    if term.endswith("*"):
        return re.escape(term[:-1]) + r"[a-z0-9]*(?![a-z0-9])"
    if term.endswith("y"):
        return re.escape(term[:-1]) + r"(?:y|ies)(?![a-z0-9])"
    return re.escape(term) + r"(?:s|es)?(?![a-z0-9])"


def word_pattern(*terms: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    """A pattern that finds any of ``terms`` as whole words in a snake_case name (``_`` and any
    other non-alphanumeric character separate words). A term may be several words
    (``tax_amount``)."""
    body = "|".join(_alternative(t) for t in terms)
    return re.compile(rf"(?<![a-z0-9])(?:{body})", flags)
