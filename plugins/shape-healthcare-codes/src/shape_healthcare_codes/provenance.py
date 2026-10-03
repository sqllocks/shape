"""Provenance and licence of every asset, one record each (the data behind
``THIRD_PARTY_NOTICES.md``; a test keeps the two in step).

``mode`` says how an asset reaches the user:

* ``shipped``: a small subset is inside the wheel (only where the source's own page says the
  content is free to redistribute);
* ``fetch``: free to redistribute, but too large to ship, or not clearly redistributable
  inside a wheel: ``shape healthcare-codes fetch ASSET`` downloads the pinned release from the
  official source on the user's machine and builds the compact Arrow file;
* ``byo``: the licence forbids redistribution (or does not clearly allow it): the user supplies
  the licensed file and :func:`shape_healthcare_codes.byo.load_byo` reads it. No licensed
  content is ever shipped, and no builder downloads it.

Every ``checked_on`` date is the day the licence statement was read on the source's own page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CHECKED = "2026-10-02"


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    title: str
    mode: str  # shipped | fetch | byo
    source_url: str
    release: str
    licence: str
    licence_url: str
    licence_quote: str
    checked_on: str = CHECKED
    download_urls: tuple[str, ...] = ()
    checksums: tuple[tuple[str, str], ...] = ()  # (file name, "algo:hex") pins
    verify: bool = False  # True: licence reading needs a human check ([VERIFY])
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


ASSETS: dict[str, Asset] = {}


def register(asset: Asset) -> Asset:
    ASSETS[asset.id] = asset
    return asset


def all_assets() -> dict[str, Asset]:
    """The registered assets (importing the catalog registers them)."""
    from shape_healthcare_codes import catalog  # noqa: F401

    return dict(ASSETS)
