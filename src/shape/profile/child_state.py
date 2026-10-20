"""Bounded child sketches accompanying the existing kernel state, including checkpoints."""

from __future__ import annotations

import base64
import json
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel

from .children import arrays, child_path, field_path, lengths, validate_depth

_MAGIC = b"shape-children-v1\n"


class ProfileState:
    """Kernel state plus one bounded sketch per child path; no input batches retained."""

    def __init__(self, schema: Any, mode: str, max_depth: int = 8, path: str = "$") -> None:
        validate_depth(max_depth)
        self.schema = schema
        self.mode = mode
        self.max_depth = max_depth
        self.path = path
        self.base = get_kernel().ProfileState(schema, mode)
        self.children: dict[str, list[ProfileState]] = {}
        self.lengths: dict[str, Any] = {}
        if not max_depth:
            return
        for f in schema:
            owner = field_path(path, f.name) if path == "$" else path
            empty = pa.array([], type=f.type)
            children = arrays(empty)
            if children:
                self.children[f.name] = [
                    ProfileState(
                        pa.schema([pa.field(n, a.type)]),
                        mode,
                        max_depth - 1,
                        child_path(owner, n, struct),
                    )
                    for n, a, struct in children
                ]
            if lengths(empty) is not None:
                self.lengths[f.name] = get_kernel().ProfileState(
                    pa.schema([("length", pa.int64())]), mode
                )

    @property
    def rows(self) -> int:
        return int(self.base.rows)

    def update(self, batch: Any) -> None:
        self.base.update(batch)
        for i, f in enumerate(self.schema):
            arr = batch.column(i)
            for state, (name, values, _) in zip(
                self.children.get(f.name, []),
                arrays(arr) if f.name in self.children else [],
                strict=True,
            ):
                state.update(pa.record_batch([values], names=[name]))
            if f.name in self.lengths:
                values = lengths(arr)
                assert values is not None
                values = values.cast(pa.int64())
                self.lengths[f.name].update(pa.record_batch([values], names=["length"]))

    def finalize(self, top_n: int = 500) -> dict[str, Any]:
        result: dict[str, Any] = self.base.finalize(top_n)
        for f, col in zip(self.schema, result["columns"], strict=True):
            states = self.children.get(f.name)
            if states is not None:
                col["children"] = []
                for state in states:
                    child = state.finalize(top_n)["columns"][0]
                    child["arrow_type"] = str(state.schema.field(0).type)
                    child["path"] = state.path
                    child["row_count"] = state.rows
                    if pa.types.is_struct(f.type):
                        child["present"] = (
                            (state.rows - child["null_count"]) / state.rows if state.rows else None
                        )
                    col["children"].append(child)
            if f.name in self.lengths:
                col["length"] = self.lengths[f.name].finalize(top_n)["columns"][0]
                col["length"]["row_count"] = int(self.lengths[f.name].rows)
        return result

    def merge(self, other: ProfileState) -> None:
        self.base.merge(other.base)
        if not other.max_depth:
            self.max_depth = 0
            self.children.clear()
            self.lengths.clear()
        for name, states in self.children.items():
            for a, b in zip(states, other.children[name], strict=True):
                a.merge(b)
        for name, state in self.lengths.items():
            state.merge(other.lengths[name])

    def _snapshot(self) -> dict[str, Any]:
        def encode(state: Any) -> str:
            return base64.b64encode(state.snapshot()).decode("ascii")

        return {
            "max_depth": self.max_depth,
            "base": encode(self.base),
            "children": {k: [s._snapshot() for s in v] for k, v in self.children.items()},
            "lengths": {k: encode(v) for k, v in self.lengths.items()},
        }

    def snapshot(self) -> bytes:
        return _MAGIC + json.dumps(self._snapshot()).encode()

    def _restore(self, document: dict[str, Any]) -> None:
        def restore(schema: Any, text: str) -> Any:
            return get_kernel().ProfileState.from_snapshot(
                schema, base64.b64decode(text, validate=True)
            )

        self.base = restore(self.schema, document["base"])
        for name, states in self.children.items():
            for state, doc in zip(states, document["children"][name], strict=True):
                state._restore(doc)
        for name in self.lengths:
            self.lengths[name] = restore(
                pa.schema([("length", pa.int64())]), document["lengths"][name]
            )

    @classmethod
    def from_snapshot(cls, schema: Any, raw: bytes) -> Any:
        if not raw.startswith(_MAGIC):
            # Old checkpoints contain only the opaque kernel state. Their existing
            # statistics remain readable; they cannot invent historical child rows.
            state = cls(schema, "bounded", 0)
            state.base = get_kernel().ProfileState.from_snapshot(schema, raw)
            return state
        doc = json.loads(raw[len(_MAGIC) :])
        state = cls(schema, "bounded", doc["max_depth"])
        state._restore(doc)
        return state
