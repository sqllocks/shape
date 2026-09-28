from dataclasses import dataclass, field

from .numeric import NumericProfile
from .sketches import HyperLogLog, SpaceSaving


@dataclass
class TextProfile:
    count: int = 0
    null_count: int = 0
    lengths: NumericProfile = field(default_factory=NumericProfile)
    cardinality: HyperLogLog = field(default_factory=HyperLogLog)
    topk: SpaceSaving = field(default_factory=SpaceSaving)

    def update_value(self, v):
        self.count += 1
        if v is None:
            self.null_count += 1
            return self
        s = str(v)
        self.lengths.update_value(len(s))
        self.cardinality.update(s)
        self.topk.update(s)
        return self

    def update(self, values):
        for v in values:
            self.update_value(v)
        return self

    def merge(self, o):
        self.count += o.count
        self.null_count += o.null_count
        self.lengths.merge(o.lengths)
        self.cardinality.merge(o.cardinality)
        self.topk.merge(o.topk)
        return self
