"""Inventory explicitly exported Python names without importing optional dependencies."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory() -> dict[str, list[str]]:
    """Return each shape module with a literal __all__ and its exported names."""
    result = {}
    for path in sorted((ROOT / "src/shape").rglob("*.py")):
        parts = path.relative_to(ROOT / "src").with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                try:
                    names = ast.literal_eval(node.value)
                except ValueError:
                    import importlib

                    names = importlib.import_module(module).__all__
                result[module] = list(names)
    return result


def missing_docstrings() -> list[str]:
    """Find exported local callables lacking a docstring; constants need no docstring."""
    import importlib
    import inspect

    missing = []
    for module, names in inventory().items():
        loaded = importlib.import_module(module)
        for name in names:
            obj = getattr(loaded, name)
            if (inspect.isfunction(obj) or inspect.isclass(obj)) and not inspect.getdoc(obj):
                missing.append(f"{module}.{name}")
    return missing


def reference_markdown() -> str:
    """Render the complete explicit export inventory through mkdocstrings."""
    lines = [
        "# Public Python reference",
        "",
        "Status: available (profiles, contracts and drift); experimental (other APIs).",
        "",
        "Generated from every `__all__` in `src/shape`. Constants are listed with their module; "
        "exported callables must have docstrings. Start with [the API guide](../API.md).",
        "",
    ]
    for module, names in inventory().items():
        lines += [
            f"## {module}",
            "",
            "Public names: " + ", ".join(f"`{n}`" for n in names) + ".",
            "",
        ]
        # Lazy top-level names are documented through their defining objects.
        if module == "shape":
            import inspect

            import shape

            for name in names:
                obj = getattr(shape, name)
                if inspect.ismodule(obj):
                    target = f"shape.api.{name}"
                else:
                    target = f"{obj.__module__}.{obj.__name__}"
                callable_obj = obj
                if inspect.ismodule(obj):
                    from shape import api

                    callable_obj = getattr(api, name)
                signature = inspect.signature(callable_obj)
                params = [
                    p.replace(annotation=inspect.Parameter.empty)
                    for p in signature.parameters.values()
                ]
                bare = str(
                    signature.replace(parameters=params, return_annotation=inspect.Signature.empty)
                )
                lines += [
                    f"### shape.{name} {{ #shape.{name} }}",
                    "",
                    "```python",
                    f"shape.{name}{bare}",
                    "```",
                    "",
                    f"::: {target}",
                    "",
                ]
        elif module in {"shape.demo", "shape.fidelity"}:
            import importlib
            import inspect

            loaded = importlib.import_module(module)
            for name in names:
                obj = getattr(loaded, name)
                target = f"{obj.__module__}.{obj.__name__}"
                lines += [f"### {module}.{name} {{ #{module}.{name} }}", "", f"::: {target}", ""]
        else:
            lines += [f"::: {module}", "    options:", "      members:"]
            lines += [f"        - {name}" for name in names]
            lines += [""]
    return "\n".join(lines)
