class Quotas:
    def __init__(self, limits=None):
        self.limits = limits or {}
        self.used = {}

    def consume(self, key, n=1):
        new = self.used.get(key, 0) + n
        if key in self.limits and new > self.limits[key]:
            raise RuntimeError("quota exceeded")
        self.used[key] = new
        return new
