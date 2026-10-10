"""PEP-249 style bounded database connector."""

from __future__ import annotations


class DBAPISource:
    """Read a parameterized DB-API query in bounded batches."""

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
            if cur.description is None:
                raise ValueError(
                    f"the statement returns no rows (no result set): {self.query!r}; "
                    "DBAPISource reads a SELECT"
                )
            names = [d[0] for d in cur.description]
            repeated = sorted({n for n in names if names.count(n) > 1})
            if repeated:  # a dict row would keep only the last of them
                raise ValueError(
                    f"the result has several columns named {', '.join(map(repr, repeated))}: "
                    "give each one an alias (SELECT a.id AS a_id, b.id AS b_id ...)"
                )
            while True:
                data = cur.fetchmany(self.batch_size)
                if not data:
                    break
                yield [dict(zip(names, row, strict=False)) for row in data]
        finally:
            cur.close()


class DBAPISink:
    """Write rows through a parameterized DB-API statement."""

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
