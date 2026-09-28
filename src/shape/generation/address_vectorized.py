"""Compiled-reference coherent address generation with partition-stable counter RNG."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompiledAddressAsset:
    city: object
    state: object
    county: object
    postal_code: object
    latitude: object
    longitude: object
    street_names: object
    weights: object | None = None
    version: str = "local"

    @classmethod
    def from_records(cls, records, street_names, version="local", weights=None):
        import numpy as np

        return cls(
            *(
                np.asarray([r[k] for r in records])
                for k in ("city", "state", "county", "postal_code", "latitude", "longitude")
            ),
            np.asarray(street_names),
            None if weights is None else np.asarray(weights, dtype=np.float64),
            version,
        )


def _u64(indices, seed, stream):
    import numpy as np

    x = (
        indices.astype(np.uint64, copy=False)
        + np.uint64(seed)
        + np.uint64(stream) * np.uint64(0x9E3779B1)
    )
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def generate_addresses(asset: CompiledAddressAsset, n: int, seed: int = 0, start: int = 0):
    import numpy as np

    if n < 0 or start < 0:
        raise ValueError("n/start")
    if len(asset.city) == 0 or len(asset.street_names) == 0:
        raise ValueError("empty address asset")
    idx = np.arange(start, start + n, dtype=np.uint64)
    hgeo = _u64(idx, seed, 1)
    if asset.weights is None:
        geo_idx = (hgeo % np.uint64(len(asset.city))).astype(np.int64)
    else:
        w = np.asarray(asset.weights, dtype=np.float64)
        if len(w) != len(asset.city) or np.any(w < 0) or not w.sum() > 0:
            raise ValueError("invalid address weights")
        c = np.cumsum(w / w.sum())
        u = hgeo.astype(np.float64) / float(2**64)
        geo_idx = np.searchsorted(c, u, side="right").clip(0, len(c) - 1)
    street_idx = (_u64(idx, seed, 2) % np.uint64(len(asset.street_names))).astype(np.int64)
    number = (_u64(idx, seed, 3) % np.uint64(9999) + 1).astype(np.int32)
    return {
        "street_number": number,
        "street_name": asset.street_names[street_idx],
        "city": asset.city[geo_idx],
        "state": asset.state[geo_idx],
        "county": asset.county[geo_idx],
        "postal_code": asset.postal_code[geo_idx],
        "latitude": asset.latitude[geo_idx],
        "longitude": asset.longitude[geo_idx],
    }
