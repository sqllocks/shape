"""Reference Address/Geospatial Pack with coherent location generation."""

from __future__ import annotations

import random
from dataclasses import dataclass

from shape.location import Location, LocationScope


@dataclass(frozen=True, slots=True)
class AddressReference:
    street: str
    city: str
    county: str
    state: str
    postal_code: str
    country: str
    latitude: float
    longitude: float
    timezone: str | None = None
    canonical_location_id: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedAddress:
    address_line_1: str
    city: str
    county: str
    state: str
    postal_code: str
    country: str
    latitude: float
    longitude: float
    timezone: str | None
    mode: str
    reference_id: str | None = None


class AddressPack:
    """Offline reference-backed generator. Reference assets are supplied by the deployment."""

    def __init__(self, rows: list[AddressReference], version: str = "local"):
        if not rows:
            raise ValueError("AddressPack requires reference rows")
        self.rows = tuple(rows)
        self.version = version

    @staticmethod
    def _matches(row: AddressReference, loc: Location) -> bool:
        pairs = (
            ("country", loc.country),
            ("state", loc.state),
            ("county", loc.county),
            ("city", loc.city),
            ("postal_code", loc.postal_code),
        )
        return all(not v or str(getattr(row, k)).casefold() == str(v).casefold() for k, v in pairs)

    def _candidates(self, loc: Location):
        return [r for r in self.rows if self._matches(r, loc)]

    def generate(
        self, n: int, scope: LocationScope, seed: int = 0, mode: str = "street_synthetic"
    ) -> list[GeneratedAddress]:
        if mode not in {"geographic", "street_synthetic", "reference", "exact_reference"}:
            raise ValueError("unsupported address mode")
        rng = random.Random(seed)
        weights = scope.normalized_weights
        out = []
        for _ in range(n):
            wl = rng.choices(scope.include, weights=weights, k=1)[0]
            candidates = self._candidates(wl.location)
            candidates = [
                r for r in candidates if not any(self._matches(r, x) for x in scope.exclude)
            ]
            if not candidates:
                raise ValueError(f"No reference geography for {wl.location}")
            ref = rng.choice(candidates)
            if mode in {"reference", "exact_reference"}:
                street = ref.street
            elif mode == "geographic":
                street = f"{rng.randint(1, 9999)} Synthetic Way"
            else:
                parts = ref.street.split(" ", 1)
                suffix = parts[1] if len(parts) > 1 else ref.street
                street = f"{rng.randint(1, 9999)} {suffix}"
            # bounded coordinate jitter only for synthetic modes; keeps location geographically
            # near reference point.
            if mode in {"reference", "exact_reference"}:
                lat, lon = ref.latitude, ref.longitude
            else:
                lat = ref.latitude + rng.uniform(-0.002, 0.002)
                lon = ref.longitude + rng.uniform(-0.002, 0.002)
            out.append(
                GeneratedAddress(
                    street,
                    ref.city,
                    ref.county,
                    ref.state,
                    ref.postal_code,
                    ref.country,
                    lat,
                    lon,
                    ref.timezone,
                    mode,
                    ref.canonical_location_id,
                )
            )
        return out


class FastAddressPack(AddressPack):
    """High-throughput encoded columnar address generator."""

    def _eligible(self, scope):
        out = []
        weights = []
        for wl, w in zip(scope.include, scope.normalized_weights, strict=False):
            candidates = self._candidates(wl.location)
            candidates = [
                r for r in candidates if not any(self._matches(r, x) for x in scope.exclude)
            ]
            if not candidates:
                raise ValueError(f"No reference geography for {wl.location}")
            out.append(candidates)
            weights.append(w)
        return out, weights

    def generate_columns(self, n, scope, seed=0, mode="street_synthetic", encoded=False):
        import numpy as np

        if mode not in {"geographic", "street_synthetic", "reference", "exact_reference"}:
            raise ValueError("unsupported address mode")
        groups, weights = self._eligible(scope)
        refs = []
        group_codes = []
        off = 0
        for g in groups:
            group_codes.append(np.arange(off, off + len(g), dtype=np.int32))
            refs.extend(g)
            off += len(g)
        rng = np.random.default_rng(seed)
        # searchsorted avoids the relatively expensive general categorical sampler and scales
        # linearly.
        gi = np.searchsorted(
            np.cumsum(np.asarray(weights, dtype=float)), rng.random(n), side="right"
        )
        # Select a reference within each scope without allocating flatnonzero position arrays.
        starts = np.asarray([c[0] for c in group_codes], dtype=np.int32)
        sizes = np.asarray([len(c) for c in group_codes], dtype=np.uint64)
        h = np.arange(n, dtype=np.uint64) + np.uint64(seed) + np.uint64(0x9E3779B97F4A7C15)
        h = (h ^ (h >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        h = (h ^ (h >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        h ^= h >> np.uint64(31)
        refidx = starts[gi] + (h % sizes[gi]).astype(np.int32)
        latref = np.asarray([r.latitude for r in refs])
        lonref = np.asarray([r.longitude for r in refs])
        lat = latref[refidx].copy()
        lon = lonref[refidx].copy()
        if mode not in {"reference", "exact_reference"}:
            lat += rng.uniform(-0.002, 0.002, n)
            lon += rng.uniform(-0.002, 0.002, n)
        nums = rng.integers(1, 10000, size=n, dtype=np.int32)
        if encoded:
            # Dictionary-encoded semantic strings keep 10M+ row workloads compact; materialize
            # only at serialization boundary.
            dictionaries = {
                k: tuple(getattr(r, k) for r in refs)
                for k in (
                    "city",
                    "county",
                    "state",
                    "postal_code",
                    "country",
                    "timezone",
                    "canonical_location_id",
                    "street",
                )
            }
            return {
                "reference_code": refidx,
                "street_number": nums,
                "latitude": lat,
                "longitude": lon,
                "dictionaries": dictionaries,
                "mode": mode,
            }
        ref = np.asarray(refs, dtype=object)[refidx]

        def col(attr):
            return np.fromiter((getattr(r, attr) for r in ref), dtype=object, count=n)

        city = col("city")
        county = col("county")
        state = col("state")
        postal = col("postal_code")
        country = col("country")
        tz = col("timezone")
        rid = col("canonical_location_id")
        if mode in {"reference", "exact_reference"}:
            street = col("street")
        elif mode == "geographic":
            street = np.fromiter((f"{x} Synthetic Way" for x in nums), dtype=object, count=n)
        else:
            suffix = (r.street.split(" ", 1)[1] if " " in r.street else r.street for r in ref)
            street = np.fromiter(
                (f"{x} {s}" for x, s in zip(nums, suffix, strict=False)), dtype=object, count=n
            )
        return {
            "address_line_1": street,
            "city": city,
            "county": county,
            "state": state,
            "postal_code": postal,
            "country": country,
            "latitude": lat,
            "longitude": lon,
            "timezone": tz,
            "reference_id": rid,
        }

    @staticmethod
    def decode(encoded, field):
        import numpy as np

        attr = "canonical_location_id" if field == "reference_id" else field
        return np.asarray(encoded["dictionaries"][attr], dtype=object)[encoded["reference_code"]]
