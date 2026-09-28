from dataclasses import dataclass

from shape.generation.evolution import ShapePoint, interpolate


@dataclass(frozen=True, slots=True)
class ShapeSeries:
    points: tuple[ShapePoint, ...]

    def __post_init__(self):
        if not self.points or any(
            b.at <= a.at for a, b in zip(self.points, self.points[1:], strict=False)
        ):
            raise ValueError("points must strictly increase")

    def at(self, t):
        if t <= self.points[0].at:
            return self.points[0].shape
        if t >= self.points[-1].at:
            return self.points[-1].shape
        for a, b in zip(self.points, self.points[1:], strict=False):
            if a.at <= t <= b.at:
                return interpolate(a, b, t)

    def replay(self, start, end, step):
        if step <= 0:
            raise ValueError("step")
        t = start
        while t <= end:
            yield ShapePoint(t, self.at(t))
            t += step
