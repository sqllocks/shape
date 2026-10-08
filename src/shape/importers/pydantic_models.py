"""Pydantic v2 models to the importer model (W5-06), through ``model_json_schema()``.

Importing the named module runs the user's code, so :func:`import_pydantic` refuses unless the
caller passed ``allow_import`` (``--allow-import`` on the command line); nothing is imported before
that check. The model's JSON Schema is then read by the JSON Schema importer, so the rules are the
same (``Optional[int]`` is a nullable integer, a nested model a child table, a ``list`` of models a
child table, an ``Enum`` a ``choice``).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from shape.importers.core import ImpModel, ImportFormatError, Report
from shape.importers.documents import Document
from shape.importers.jsonschema import import_jsonschema


def split_target(target: str) -> tuple[str, str | None]:
    """``module.path:Model`` or ``path/to/file.py:Model`` to the module and the model name (or
    ``None`` for every model the module defines)."""
    module, sep, model = target.rpartition(":")
    if not sep or (len(module) == 1 and os.sep in target):  # a Windows drive, not a model name
        return target, None
    return module, model or None


def _load_module(module: str) -> ModuleType:
    if module.endswith(".py") or os.sep in module or "/" in module:
        path = Path(module)
        if not path.is_file():
            raise ImportFormatError("file not found", file=module)
        name = f"_shape_import_{abs(hash(str(path.resolve())))}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportFormatError("cannot be loaded as a Python module", file=module)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # pydantic resolves forward references through sys.modules
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            del sys.modules[name]
            raise ImportFormatError(
                f"importing the module failed: {type(exc).__name__}: {exc}", file=module
            ) from exc
        return mod
    sys.path.insert(0, os.getcwd())
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportFormatError(f"cannot import module {module!r}: {exc}", element=module) from exc
    except Exception as exc:
        raise ImportFormatError(
            f"importing {module!r} failed: {type(exc).__name__}: {exc}", element=module
        ) from exc
    finally:
        sys.path.remove(os.getcwd())


def _models(mod: ModuleType, base: type, name: str | None, target: str) -> list[type]:
    if name is not None:
        found = getattr(mod, name, None)
        if not (isinstance(found, type) and issubclass(found, base)):
            raise ImportFormatError(
                f"{name!r} is not a Pydantic model of {mod.__name__}", element=target
            )
        return [found]
    models = [
        v
        for v in vars(mod).values()
        if isinstance(v, type)
        and issubclass(v, base)
        and v is not base
        and v.__module__ == mod.__name__
    ]
    if not models:
        raise ImportFormatError("the module defines no Pydantic model", element=target)
    return models


def import_pydantic(target: str, report: Report, *, allow_import: bool = False) -> ImpModel:
    if not allow_import:
        raise ImportFormatError(
            "importing a Pydantic module runs its code; pass --allow-import to say you trust it "
            "(nothing was imported)",
            element=target,
        )
    try:
        import pydantic
    except ImportError as exc:
        raise ImportFormatError(
            "Pydantic is not installed (pip install 'pydantic>=2'); nothing to import with",
            element=target,
        ) from exc
    if not hasattr(pydantic.BaseModel, "model_json_schema"):
        raise ImportFormatError("Pydantic v1 is not supported; install v2", element=target)
    module, name = split_target(target)
    mod = _load_module(module)
    models = _models(mod, pydantic.BaseModel, name, target)
    defs: dict[str, Any] = {}
    for model in models:
        schema = model.model_json_schema()  # type: ignore[attr-defined]
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
        report.mapped(
            f"{mod.__name__}:{model.__name__}", "model", "JSON Schema from model_json_schema()"
        )
    doc = Document(target, {"title": Path(module).stem, "$defs": defs})
    return import_jsonschema(doc, report)
