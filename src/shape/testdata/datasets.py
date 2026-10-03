"""Datasets for tests: generate a domain or schema once and hand tables to many tests.

:class:`DatasetCache` keys a generated result by ``(spec digest, scale, seed)``, where the digest is
a hash of the generation schema itself (so a changed domain or schema file is a different key). It
generates in memory and writes nothing. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.errors import ShapeError

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

    from shape.generation.schema import GenSchema


class ShapeDataset(Mapping[str, "pa.Table"]):
    """The generated tables of one dataset, by name, as Arrow tables. ``as_pandas()`` converts
    them when pandas is installed."""

    def __init__(
        self, tables: Mapping[str, pa.Table], *, digest: str, scale: str, seed: int
    ) -> None:
        self._tables = dict(tables)
        self.digest = digest
        self.scale = scale
        self.seed = seed

    def __getitem__(self, name: str) -> pa.Table:
        try:
            return self._tables[name]
        except KeyError:
            raise KeyError(f"no table {name!r}; the tables are {', '.join(self._tables)}") from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._tables)

    def __len__(self) -> int:
        return len(self._tables)

    @property
    def tables(self) -> dict[str, pa.Table]:
        return dict(self._tables)

    def as_pandas(self, table: str | None = None) -> Any:
        """One table as a ``DataFrame`` (``table`` given), or a dict of them. Needs pandas."""
        if importlib.util.find_spec("pandas") is None:
            raise ShapeError("as_pandas() needs pandas: pip install 'sqllocks-shape[pandas]'")
        if table is not None:
            return self[table].to_pandas()
        return {name: t.to_pandas() for name, t in self._tables.items()}


def load_schema(spec: Any) -> GenSchema:
    """The generation schema of a domain name, a schema file, a document or a schema object."""
    from shape.generation.schema import GenSchema

    if isinstance(spec, GenSchema):
        return spec
    if isinstance(spec, Mapping):
        return GenSchema.from_dict(dict(spec))
    if isinstance(spec, (str, Path)):
        if Path(spec).is_file():
            return GenSchema.from_dict(json.loads(Path(spec).read_text(encoding="utf-8")))
        from shape.generation.domains import load_domain

        return load_domain(str(spec)).schema
    raise ShapeError(f"a dataset spec is a domain name, a schema file or a schema, not {spec!r}")


def spec_digest(schema: GenSchema) -> str:
    """A hash of the schema document: the same schema gives the same digest."""
    text = json.dumps(schema.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DatasetCache:
    """Generated results by ``(spec digest, scale, seed)``. ``hits`` and ``misses`` count the
    lookups, for tests of the cache."""

    def __init__(self) -> None:
        self._results: dict[tuple[str, str, int], Mapping[str, pa.Table]] = {}
        self.hits = 0
        self.misses = 0

    def dataset(
        self,
        spec: Any,
        *,
        scale: str = "tiny",
        seed: int | None = None,
        tables: list[str] | None = None,
        override_seed: int | None = None,
    ) -> ShapeDataset:
        """The dataset for ``spec``. ``override_seed`` (the ``--shape-seed`` option) wins over
        ``seed``; with neither, the schema's own seed is used. ``tables`` keeps only those, in the
        order given; a name the schema lacks is a :class:`~shape.errors.ShapeError`."""
        from shape.generation.engine import Engine
        from shape.scenario.library.scale import resolve_scale

        schema = load_schema(spec)
        used = override_seed if override_seed is not None else seed
        used = schema.model.seed if used is None else int(used)
        if tables is not None:
            unknown = [t for t in tables if t not in schema.tables]
            if unknown:
                raise ShapeError(
                    f"no table {', '.join(unknown)} in the dataset; "
                    f"the tables are {', '.join(schema.tables)}"
                )
        digest = spec_digest(schema)
        key = (digest, scale, used)
        if key in self._results:
            self.hits += 1
        else:
            self.misses += 1
            preset, rows = resolve_scale(schema, scale)
            result = Engine(schema, scale=preset, seed=used, row_counts=rows).generate()
            self._results[key] = dict(result.tables)
        everything = self._results[key]
        chosen = {t: everything[t] for t in tables} if tables is not None else dict(everything)
        return ShapeDataset(chosen, digest=digest, scale=scale, seed=used)
