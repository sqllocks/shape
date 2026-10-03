"""Load, validate, edit and save a generation spec (``docs/GENERATION_SPEC.md``).

A :class:`SpecDocument` holds the spec as the JSON document it is: every key stays where it
was, including the ones this version does not know (``x-*`` keys, ``$comment`` and fields a
newer 1.x adds), so an edit never loses them. A spec that was loaded from text and not
changed is written back byte for byte; an edited one is written with the same key order.
JSON has no comments, so ``$comment`` keys are the place for them.

Validation reports every problem with the JSON Pointer of the place (``/tables/orders/columns/
total/generator/lw``) and, for a spec loaded from text, the line and column. It checks the
published schema (``generation-spec-v1.schema.json``) and then what only the whole spec can
tell (an FK to a table that is not there, a primary key column that does not exist).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from bisect import bisect_right
from dataclasses import dataclass
from json import JSONDecodeError
from json.decoder import scanstring  # type: ignore[attr-defined]
from pathlib import Path
from typing import Any

from shape import schemacheck
from shape.errors import ShapeSchemaError
from shape.generation import spec_keys
from shape.generation.schema import GenSchema, GenSchemaError, schema_problems
from shape.generation.spec_schema import published_schema
from shape.security.names import is_safe_name

Position = tuple[int, int]


@dataclass(frozen=True, slots=True)
class SpecProblem:
    """One finding. ``pointer`` is a JSON Pointer into the spec, ``path`` the same place with
    dots, ``line`` and ``column`` (from 1) the place in the text when the spec was loaded from
    text and has not been edited since."""

    level: str
    pointer: str
    message: str
    line: int | None = None
    column: int | None = None

    @property
    def path(self) -> str:
        return ".".join(_tokens(self.pointer)) if self.pointer else "$"

    def __str__(self) -> str:
        where = f"line {self.line}, column {self.column}: " if self.line is not None else ""
        flag = "warning: " if self.level == "warning" else ""
        return f"{where}{self.pointer or '/'}: {flag}{self.message}"


class SpecError(ShapeSchemaError):
    """A spec cannot be read, or has errors; ``problems`` lists them with their places."""

    def __init__(self, problems: list[SpecProblem]) -> None:
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


# ---- JSON Pointer --------------------------------------------------------------------------


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"not a JSON pointer (it must start with '/'): {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "".join("/" + _escape(str(p)) for p in parts)


def _child(node: Any, token: str, pointer: str) -> Any:
    if isinstance(node, dict):
        if token in node:
            return node[token]
    elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
        return node[int(token)]
    raise KeyError(pointer)


# ---- positions in the text -----------------------------------------------------------------


class _Scan:
    """Where every key and array item of a well-formed JSON text starts, by JSON Pointer."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
        self.positions: dict[str, Position] = {}
        self.duplicates: list[tuple[str, str, Position]] = []
        self._ws()
        self._value("", self.at(self.i))

    def at(self, index: int) -> Position:
        line = bisect_right(self.starts, index)
        return line, index - self.starts[line - 1] + 1

    def _ws(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t\r\n":
            self.i += 1

    def _value(self, pointer: str, where: Position) -> None:
        self.positions.setdefault(pointer, where)
        c = self.text[self.i]
        if c == "{":
            self._object(pointer)
        elif c == "[":
            self._array(pointer)
        elif c == '"':
            self.i = scanstring(self.text, self.i + 1)[1]
        else:
            while self.i < len(self.text) and self.text[self.i] not in ",}] \t\r\n":
                self.i += 1

    def _object(self, pointer: str) -> None:
        self.i += 1
        seen: set[str] = set()
        self._ws()
        while self.text[self.i] != "}":
            where = self.at(self.i)
            key, self.i = scanstring(self.text, self.i + 1)
            if key in seen:
                self.duplicates.append((pointer, key, where))
            seen.add(key)
            self._ws()
            self.i += 1  # the colon
            self._ws()
            child = f"{pointer}/{_escape(key)}"
            self.positions[child] = where  # a member is located at its key
            self._value(child, where)
            self._ws()
            if self.text[self.i] == ",":
                self.i += 1
                self._ws()
        self.i += 1

    def _array(self, pointer: str) -> None:
        self.i += 1
        index = 0
        self._ws()
        while self.text[self.i] != "]":
            self._value(f"{pointer}/{index}", self.at(self.i))
            index += 1
            self._ws()
            if self.text[self.i] == ",":
                self.i += 1
                self._ws()
        self.i += 1


def _parse(text: str) -> tuple[dict[str, Any], dict[str, Position]]:
    try:
        parsed = json.loads(text)
    except JSONDecodeError as exc:
        raise SpecError([SpecProblem("error", "", exc.msg, exc.lineno, exc.colno)]) from exc
    if not isinstance(parsed, dict):
        raise SpecError([SpecProblem("error", "", "a generation spec must be an object", 1, 1)])
    scan = _Scan(text)
    if scan.duplicates:
        pointer, key, (line, col) = scan.duplicates[0]
        raise SpecError(
            [SpecProblem("error", f"{pointer}/{_escape(key)}", f"duplicate key {key!r}", line, col)]
        )
    return parsed, scan.positions


# ---- the document --------------------------------------------------------------------------


class SpecDocument:
    """A generation spec you can validate, edit and save. See the module docstring."""

    def __init__(
        self,
        doc: dict[str, Any],
        *,
        text: str | None = None,
        positions: dict[str, Position] | None = None,
    ) -> None:
        self._doc = doc
        self._text = text
        self._positions = positions
        self._loaded = json.dumps(doc) if text is not None else None

    # ---- construction and output ----

    @classmethod
    def from_dict(cls, doc: Any) -> SpecDocument:
        """A document from a spec already parsed (it is copied; no positions are known)."""
        if not isinstance(doc, dict):
            raise SpecError([SpecProblem("error", "", "a generation spec must be an object")])
        return cls(copy.deepcopy(doc))

    @classmethod
    def loads(cls, text: str) -> SpecDocument:
        """A document from JSON text; a syntax error or a duplicate key is a :class:`SpecError`
        with the line and column."""
        doc, positions = _parse(text)
        return cls(doc, text=text, positions=positions)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> SpecDocument:
        try:
            text = Path(path).read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SpecError([SpecProblem("error", "", f"cannot read {path}: {exc}")]) from exc
        return cls.loads(text)

    def to_dict(self) -> dict[str, Any]:
        """A deep copy of the spec, every field included."""
        return copy.deepcopy(self._doc)

    def dumps(self) -> str:
        """The JSON text: the original when nothing changed, else the document with its key
        order kept (two-space indent, a final newline)."""
        if self._text is not None and json.dumps(self._doc) == self._loaded:
            return self._text
        return json.dumps(self._doc, indent=2, ensure_ascii=False) + "\n"

    def save(self, path: str | os.PathLike[str]) -> None:
        """Write the spec to ``path`` (to a temporary file first, then renamed over it)."""
        target = Path(path)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(self.dumps())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def to_schema(self) -> GenSchema:
        """The typed :class:`~shape.generation.schema.GenSchema` the engine runs (it keeps only
        the fields this version reads)."""
        self.raise_for_errors()
        return GenSchema.from_dict(self._doc)

    # ---- generic access by JSON Pointer ----

    def _walk(self, tokens: list[str], pointer: str) -> Any:
        node: Any = self._doc
        for token in tokens:
            node = _child(node, token, pointer)
        return node

    def get(self, pointer: str) -> Any:
        """The value at ``pointer`` (the live value, not a copy); ``KeyError`` if absent."""
        return self._walk(_tokens(pointer), pointer)

    def set(self, pointer: str, value: Any) -> None:
        """Set the value at ``pointer``: the parent must exist; a missing key is added, and ``-``
        appends to a list."""
        tokens = _tokens(pointer)
        if not tokens:
            raise ValueError("cannot replace the whole document")
        parent = self._walk(tokens[:-1], pointer)
        last = tokens[-1]
        if isinstance(parent, dict):
            parent[last] = copy.deepcopy(value)
        elif isinstance(parent, list) and last == "-":
            parent.append(copy.deepcopy(value))
        elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent):
            parent[int(last)] = copy.deepcopy(value)
        else:
            raise KeyError(pointer)
        self._positions = None

    def remove(self, pointer: str) -> None:
        """Remove the key or list item at ``pointer``; ``KeyError`` if absent."""
        tokens = _tokens(pointer)
        if not tokens:
            raise ValueError("cannot remove the whole document")
        parent = self._walk(tokens[:-1], pointer)
        _child(parent, tokens[-1], pointer)
        if isinstance(parent, dict):
            del parent[tokens[-1]]
        else:
            del parent[int(tokens[-1])]
        self._positions = None

    # ---- tables and columns ----

    def _table(self, table: str) -> dict[str, Any]:
        tables = self._doc.get("tables")
        if not isinstance(tables, dict) or table not in tables:
            raise KeyError(f"no table {table!r}")
        found: dict[str, Any] = tables[table]
        return found

    def _columns(self, table: str) -> dict[str, Any]:
        columns: dict[str, Any] = self._table(table).setdefault("columns", {})
        return columns

    @property
    def table_names(self) -> list[str]:
        tables = self._doc.get("tables")
        return list(tables) if isinstance(tables, dict) else []

    def column_names(self, table: str) -> list[str]:
        return list(self._columns(table))

    def add_table(
        self,
        name: str,
        *,
        primary_key: list[str] | tuple[str, ...] = (),
        description: str = "",
        **extra: Any,
    ) -> None:
        """Add an empty table at the end; ``extra`` keys (``x-*``) are stored on it."""
        tables = self._doc.setdefault("tables", {})
        if name in tables:
            raise KeyError(f"table {name!r} already exists")
        table: dict[str, Any] = {"name": name, "primary_key": list(primary_key), "columns": {}}
        if description:
            table["description"] = description
        table.update(copy.deepcopy(extra))
        tables[name] = table
        self._positions = None

    def remove_table(self, name: str) -> None:
        self._table(name)
        del self._doc["tables"][name]
        self._positions = None

    def add_column(
        self,
        table: str,
        name: str,
        type: str,  # noqa: A002 - the spec's own key
        generator: dict[str, Any],
        **properties: Any,
    ) -> None:
        """Add a column at the end; ``properties`` are the column's other keys (``nullable``,
        ``null_rate``, ``precision``, ... and ``x-*``)."""
        columns = self._columns(table)
        if name in columns:
            raise KeyError(f"column {table}.{name} already exists")
        columns[name] = {
            "name": name,
            "type": type,
            "generator": copy.deepcopy(generator),
            **copy.deepcopy(properties),
        }
        self._positions = None

    def remove_column(self, table: str, name: str) -> None:
        columns = self._columns(table)
        if name not in columns:
            raise KeyError(f"no column {table}.{name}")
        del columns[name]
        self._positions = None

    def set_generator(self, table: str, column: str, generator: dict[str, Any]) -> None:
        """Replace one column's generator; the column's other keys are untouched."""
        columns = self._columns(table)
        if column not in columns:
            raise KeyError(f"no column {table}.{column}")
        columns[column]["generator"] = copy.deepcopy(generator)
        self._positions = None

    # ---- validation ----

    def validate(self) -> list[SpecProblem]:
        """Every problem, errors first in document order: the published schema (unknown keys,
        wrong types, missing keys, with a hint for a typo) and then the semantic checks of
        :meth:`GenSchema.validate`."""
        found = self._schema_problems()
        if not schema_problems(self._doc):
            found += self._semantic_problems()
        return [self._located(p) for p in found]

    def raise_for_errors(self) -> None:
        errors = [p for p in self.validate() if p.level == "error"]
        if errors:
            raise SpecError(errors)

    def _located(self, p: SpecProblem) -> SpecProblem:
        if not self._positions:
            return p
        pointer = p.pointer
        while pointer not in self._positions and pointer:
            pointer = pointer.rsplit("/", 1)[0]
        line, col = self._positions.get(pointer, (None, None))
        return SpecProblem(p.level, p.pointer, p.message, line, col)

    def _schema_problems(self) -> list[SpecProblem]:
        out: list[SpecProblem] = []
        for item in schemacheck.problems(self._doc, published_schema()):
            parts = (*item.path, item.key) if item.key is not None else item.path
            message = item.message
            if item.key is not None:
                message = self._hint(item.path, item.key) or message
            out.append(SpecProblem("error", _pointer(parts), message))
        return out

    def _hint(self, path: tuple[str | int, ...], key: str) -> str | None:
        """The message of ``spec_keys`` for an unknown generator key (it names a column
        property or the closest key)."""
        nested = len(path) >= 1 and path[-1] == "params"
        generator_path = path[:-1] if nested else path
        if not generator_path or generator_path[-1] != "generator":
            return None
        try:
            generator = self._walk([str(p) for p in generator_path], "")
        except KeyError:
            return None
        if not isinstance(generator, dict):
            return None
        wanted = f"params.{key}" if nested else key
        for found, message in spec_keys.unknown_keys(str(generator.get("strategy", "")), generator):
            if found == wanted:
                return message
        return None

    def _semantic_problems(self) -> list[SpecProblem]:
        unsafe = [
            SpecProblem(
                "error",
                _pointer(("tables", name)),
                "a table name must be a plain name, not a path",
            )
            for name in self.table_names
            if not is_safe_name(name)
        ]
        if unsafe:
            return unsafe
        try:
            schema = GenSchema.from_dict(self._doc)
        except GenSchemaError as exc:
            return [SpecProblem("error", "", str(exc))]
        out: list[SpecProblem] = []
        for issue in schema.validate():
            # the schema already reports these two, with the line of the key
            if "ignores" in issue.message or "expects key" in issue.message:
                continue
            out.append(SpecProblem(issue.level, self._pointer_of(issue.location), issue.message))
        return out

    def _pointer_of(self, location: str) -> str:
        """The pointer of a dotted ``GenSchema`` location (``tables.t.columns.c``); a name with
        a dot is matched against the document, and a relationship or rule by its ``name``."""
        parts = location.split(".")
        node: Any = self._doc
        pointer: list[str | int] = []
        i = 0
        while i < len(parts):
            matched = False
            for j in range(len(parts), i, -1):
                key = ".".join(parts[i:j])
                if isinstance(node, dict) and key in node:
                    node, i, matched = node[key], j, True
                    pointer.append(key)
                    break
                if isinstance(node, list):
                    index = next(
                        (
                            n
                            for n, item in enumerate(node)
                            if isinstance(item, dict) and item.get("name") == key
                        ),
                        None,
                    )
                    if index is not None:
                        node, i, matched = node[index], j, True
                        pointer.append(index)
                        break
            if not matched:
                break
        return _pointer(tuple(pointer))


def validate_text(text: str) -> list[SpecProblem]:
    """The problems of a spec given as text, with lines and columns; a text that is not a JSON
    object gives the one syntax problem."""
    try:
        return SpecDocument.loads(text).validate()
    except SpecError as exc:
        return exc.problems
