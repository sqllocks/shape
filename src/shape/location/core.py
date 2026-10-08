"""Location as Code: canonical scopes, sets, weights, includes and excludes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from shape.errors import ShapeError


class LocationError(ShapeError):
    pass


class AmbiguousLocationError(LocationError):
    pass


class UnknownLocationError(LocationError):
    pass


@dataclass(frozen=True, slots=True)
class Location:
    country: str = "US"
    state: str | None = None
    county: str | None = None
    city: str | None = None
    postal_code: str | None = None
    canonical_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    aliases: tuple[str, ...] = ()

    @classmethod
    def zip(cls, postal_code: str, country: str = "US") -> Location:
        return cls(country=country, postal_code=str(postal_code))

    @classmethod
    def city_scope(cls, city: str, state: str, country: str = "US") -> Location:
        return cls(country=country, state=state, city=city)

    @classmethod
    def county_scope(cls, county: str, state: str, country: str = "US") -> Location:
        return cls(country=country, state=state, county=county)

    @classmethod
    def state_scope(cls, state: str, country: str = "US") -> Location:
        return cls(country=country, state=state)


@dataclass(frozen=True, slots=True)
class WeightedLocation:
    location: Location
    weight: float = 1.0

    def __post_init__(self):
        if self.weight <= 0:
            raise ValueError("weight must be positive")


@dataclass(frozen=True, slots=True)
class LocationScope:
    include: tuple[WeightedLocation, ...]
    exclude: tuple[Location, ...] = ()
    name: str | None = None
    reference_version: str | None = None

    def __post_init__(self):
        if not self.include:
            raise ValueError("LocationScope requires at least one included location")

    @classmethod
    def one(cls, location: Location) -> LocationScope:
        return cls((WeightedLocation(location),))

    @classmethod
    def any(cls, *locations: Location) -> LocationScope:
        return cls(tuple(WeightedLocation(x) for x in locations))

    @classmethod
    def weighted(cls, items: Iterable[tuple[Location, float]]) -> LocationScope:
        return cls(tuple(WeightedLocation(x, w) for x, w in items))

    @property
    def normalized_weights(self) -> tuple[float, ...]:
        total = sum(x.weight for x in self.include)
        return tuple(x.weight / total for x in self.include)


class LocationResolver:
    """Offline resolver over a versioned reference asset."""

    def __init__(self, locations: Iterable[Location], version: str = "local"):
        self.locations = tuple(locations)
        self.version = version

    @staticmethod
    def _norm(x: str | None) -> str | None:
        return x.strip().casefold() if x else None

    def resolve(self, query: Location) -> Location:
        def match(c: Location) -> bool:
            for attr in ("country", "state", "county", "city", "postal_code"):
                q = getattr(query, attr)
                v = getattr(c, attr)
                if q and self._norm(q) != self._norm(v):
                    return False
            return True

        hits = [x for x in self.locations if match(x)]
        if not hits:
            raise UnknownLocationError(f"No canonical location matches {query}")
        ids = {x.canonical_id or repr(x) for x in hits}
        if len(ids) > 1:
            raise AmbiguousLocationError(
                f"Location is ambiguous: {query}; add state/country/postal context"
            )
        return hits[0]

    def resolve_scope(self, scope: LocationScope) -> LocationScope:
        return LocationScope(
            tuple(WeightedLocation(self.resolve(x.location), x.weight) for x in scope.include),
            tuple(self.resolve(x) for x in scope.exclude),
            scope.name,
            self.version,
        )


def location_from_spec(spec) -> Location:
    """Parse an app/agent-friendly location specification."""
    if isinstance(spec, Location):
        return spec
    if isinstance(spec, str):
        s = spec.strip()
        if s.isdigit() and len(s) in (5, 9):
            return Location.zip(s[:5])
        if len(s) == 2 and s.isalpha():
            return Location.state_scope(s.upper())
        parts = [x.strip() for x in s.split(",")]
        if len(parts) == 2:
            return Location.city_scope(parts[0], parts[1])
        raise ValueError("string location must be a ZIP, a state code ('WA') or 'City, ST'")
    if not isinstance(spec, dict):
        raise TypeError("location spec")
    if "zip" in spec or "postal_code" in spec:
        return Location.zip(
            str(spec.get("zip", spec.get("postal_code"))), spec.get("country", "US")
        )
    if "city" in spec:
        return Location(
            country=spec.get("country", "US"), state=spec.get("state"), city=spec["city"]
        )
    if "county" in spec:
        return Location(
            country=spec.get("country", "US"), state=spec.get("state"), county=spec["county"]
        )
    if "state" in spec:
        return Location.state_scope(spec["state"], spec.get("country", "US"))
    if "country" in spec:
        return Location(country=spec["country"])
    raise ValueError("location spec requires zip, city, county, state or country")


def scope_from_specs(specs, weights=None, exclude=()):
    locs = tuple(location_from_spec(x) for x in specs)
    if weights is None:
        return LocationScope(
            tuple(WeightedLocation(x) for x in locs), tuple(location_from_spec(x) for x in exclude)
        )
    if len(weights) != len(locs):
        raise ValueError("weights")
    return LocationScope.weighted(zip(locs, weights, strict=False)).__class__(
        tuple(WeightedLocation(x, w) for x, w in zip(locs, weights, strict=False)),
        tuple(location_from_spec(x) for x in exclude),
    )
