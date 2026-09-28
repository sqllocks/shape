from dataclasses import dataclass


@dataclass
class DatetimeProfile:
    count: int = 0
    null_count: int = 0
    minimum: object = None
    maximum: object = None

    def update(self, values):
        for v in values:
            self.count += 1
            if v is None:
                self.null_count += 1
                continue
            self.minimum = v if self.minimum is None or v < self.minimum else self.minimum
            self.maximum = v if self.maximum is None or v > self.maximum else self.maximum
        return self

    def merge(self, o):
        self.count += o.count
        self.null_count += o.null_count
        if o.minimum is not None:
            self.minimum = o.minimum if self.minimum is None else min(self.minimum, o.minimum)
        if o.maximum is not None:
            self.maximum = o.maximum if self.maximum is None else max(self.maximum, o.maximum)
        return self
