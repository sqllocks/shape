"""Bounded local file connectors."""

import csv
import json
from pathlib import Path


class CSVSource:
    def __init__(self, path, batch_size=10000):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.path = Path(path)
        self.batch_size = batch_size

    def rows(self):
        with self.path.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            batch = []
            for row in r:
                batch.append(dict(row))
                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch

    def record_batches(self):
        try:
            import pyarrow as pa
        except ImportError as e:
            raise RuntimeError("pyarrow required for record_batches()") from e
        for rows in self.rows():
            yield pa.RecordBatch.from_pylist(rows)


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
