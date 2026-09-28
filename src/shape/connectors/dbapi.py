"""PEP-249 style bounded database connector."""

from __future__ import annotations


class DBAPISource:
    def __init__(self, connection, query: str, params=(), batch_size: int = 10000):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.connection = connection
        self.query = query
        self.params = params
        self.batch_size = batch_size

    def rows(self):
        cur = self.connection.cursor()
        try:
            cur.execute(self.query, self.params)
            names = [d[0] for d in cur.description]
            while True:
                data = cur.fetchmany(self.batch_size)
                if not data:
                    break
                yield [dict(zip(names, row, strict=False)) for row in data]
        finally:
            cur.close()


class DBAPISink:
    def __init__(
        self, connection, statement: str, fields: tuple[str, ...], commit_every_batch: bool = True
    ):
        self.connection = connection
        self.statement = statement
        self.fields = fields
        self.commit_every_batch = commit_every_batch

    def write(self, batches):
        n = 0
        cur = self.connection.cursor()
        try:
            for batch in batches:
                vals = [tuple(r.get(f) for f in self.fields) for r in batch]
                cur.executemany(self.statement, vals)
                n += len(vals)
                if self.commit_every_batch:
                    self.connection.commit()
            if not self.commit_every_batch:
                self.connection.commit()
            return n
        except Exception:
            self.connection.rollback()
            raise
        finally:
            cur.close()
