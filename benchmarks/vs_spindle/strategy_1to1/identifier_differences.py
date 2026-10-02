"""The deliberate differences of Shape's identifier providers from the baseline (ISS-gen).

The owner decided (2026-10-01) to fix behaviour of the baseline that harms trust, and to record
each such difference here, narrowly and with its reason. Synthetic data must not contain values
that belong to real people, so by default Shape's providers produce reserved values:

* ``email``, ``company_email`` and ``uri`` use the host names RFC 2606 reserves (``example.com``,
  ``example.org``, ``example.net``; ``.example`` for ``company_email``);
* ``ssn`` uses the 9xx areas, which are never assigned;
* ``phone_number`` uses the 555-0100 to 555-0199 lines reserved for fiction.

The baseline's values are one explicit option away (``"domains": "realistic"``,
``"range": "assignable"``). The equivalence cases of ``cases_p404b.py`` generate Shape's side with
that option, so everything else (the names, the number parts, the lengths) is still compared with
the baseline under T-21; ``tests/generation/test_iss_gen_identifiers.py`` proves each default here
differs from the baseline in exactly the listed way, so an entry cannot go stale.
"""

from __future__ import annotations

from typing import Any

# provider -> the spec option that gives the baseline's values
REALISTIC_OPTIONS: dict[str, dict[str, Any]] = {
    "email": {"domains": "realistic"},
    "company_email": {"domains": "realistic"},
    "uri": {"domains": "realistic"},
    "ssn": {"range": "assignable"},
    "phone_number": {"range": "assignable"},
}

# provider -> why the default differs from the baseline
DELIBERATE: dict[str, str] = {
    "email": (
        "The baseline draws the domain from 50 real mail providers (gmail.com, yahoo.com, ...), so "
        "synthetic addresses can be real people's mailboxes. Shape uses example.com, example.org "
        "and example.net, which can never be a mailbox."
    ),
    "company_email": (
        "The baseline builds <first>.<last>@<company>.com, which can be a real domain. Shape "
        "uses <company>.example, a top-level domain reserved for examples."
    ),
    "uri": (
        "The baseline draws the host from a pool of real-looking .com/.org/.dev names that can "
        "be real sites. Shape uses example.com, example.org and example.net."
    ),
    "ssn": (
        "The baseline emits only assignable numbers (area 001 to 899 without 666), so a value can "
        "be a real person's. Shape uses the 9xx areas, which the SSA never assigns."
    ),
    "phone_number": (
        "The baseline emits any (AAA) EEE-SSSS, which can be a real subscriber. Shape uses "
        "(AAA) 555-0100 to 555-0199, the lines reserved for fiction."
    ),
}
