"""Bounded Shape-aware ETL."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ETLResult:
    rows_in: int
    rows_out: int
    batches: int
    metadata: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, transforms=(), pre_validate=None, post_validate=None):
        self.transforms = tuple(transforms)
        self.pre_validate = pre_validate
        self.post_validate = post_validate

    def run(self, batches, sink):
        rin = rout = nb = 0

        def transformed():
            nonlocal rin, rout, nb
            for batch in batches:
                nb += 1
                rin += len(batch)
                if self.pre_validate:
                    self.pre_validate(batch)
                out = batch
                for fn in self.transforms:
                    out = fn(out)
                if self.post_validate:
                    self.post_validate(out)
                rout += len(out)
                yield out

        sink.write(transformed())
        return ETLResult(rin, rout, nb)
