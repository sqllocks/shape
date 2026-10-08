"""Shape-as-Code file IO. YAML is optional; JSON is always supported."""

from __future__ import annotations

import json
from pathlib import Path

from .model import ShapeContract


def load_contract(path):
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # noqa: F401
        except ImportError as e:
            raise RuntimeError("YAML support requires the dev/yaml extra") from e
        from shape.security.yamlsafe import safe_load_yaml

        obj = safe_load_yaml(raw)
    else:
        obj = json.loads(raw)
    return ShapeContract.from_dict(obj)


def save_contract(contract, path):
    p = Path(path)
    obj = contract.to_dict()
    if p.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as e:
            raise RuntimeError("YAML support requires PyYAML") from e
        p.write_text(yaml.safe_dump(obj, sort_keys=False), encoding="utf-8", newline="\n")
    else:
        p.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
