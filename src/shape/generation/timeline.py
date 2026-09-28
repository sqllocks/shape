"""Version-aware Shape timelines and synthetic replay across historical Shapes."""

from __future__ import annotations

from dataclasses import dataclass

from .compiler import generate_from_shape
from .evolution import ShapePoint, interpolate


@dataclass(frozen=True, slots=True)
class VersionedShape:
    version: str
    at: float
    shape: dict


class ShapeTimeline:
    def __init__(self, versions):
        self.versions = tuple(sorted(versions, key=lambda x: x.at))
        if not self.versions:
            raise ValueError("timeline requires versions")
        if any(b.at <= a.at for a, b in zip(self.versions, self.versions[1:], strict=False)):
            raise ValueError("timestamps must increase")

    def shape_at(self, t, mode="interpolate"):
        if t <= self.versions[0].at:
            return self.versions[0].shape
        if t >= self.versions[-1].at:
            return self.versions[-1].shape
        for a, b in zip(self.versions, self.versions[1:], strict=False):
            if a.at <= t <= b.at:
                if mode == "previous":
                    return a.shape
                if mode == "next":
                    return b.shape
                if mode != "interpolate":
                    raise ValueError("mode")
                return interpolate(ShapePoint(a.at, a.shape), ShapePoint(b.at, b.shape), t)

    def generate_at(self, t, rows, seed=0, mode="interpolate"):
        return generate_from_shape(self.shape_at(t, mode), rows, seed)

    def generate_range(self, start, end, step, rows_per_step, seed=0, mode="interpolate"):
        if step <= 0 or end < start:
            raise ValueError("range")
        out = []
        i = 0
        t = start
        while t <= end:
            data, report = self.generate_at(t, rows_per_step, seed + i, mode)
            out.append((t, data, report))
            i += 1
            t += step
        return out

    def changes(self):
        from shape.drift import compare

        return tuple(
            {"from": a.version, "to": b.version, "at": b.at, "drift": compare(a.shape, b.shape)}
            for a, b in zip(self.versions, self.versions[1:], strict=False)
        )
