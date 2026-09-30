"""Bounded local file connectors."""

import json
from pathlib import Path


class JSONLSource:
    def __init__(self, path, batch_size=10000):
        self.path = Path(path)
        self.batch_size = batch_size

    def rows(self):
        with self.path.open("r", encoding="utf-8") as f:
            batch = []
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("JSONL records must be objects")
                batch.append(obj)
                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch


class JSONLSink:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, batches):
        n = 0
        with self.path.open("w", encoding="utf-8") as f:
            for batch in batches:
                for row in batch:
                    f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
                    n += 1
        return n
