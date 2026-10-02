"""One bounded, safe YAML loader for every file an outside party can write (P7-04).

``yaml.safe_load`` builds no Python objects, but it shares anchored nodes, so a small document
of nested aliases ("billion laughs") is cheap to load and enormous once anything copies it
(``json.dumps``, ``deepcopy``). This loader composes the document first and measures its
*expanded* size (every alias counted as often as it is used); it refuses a document over
``MAX_BYTES`` or over ``MAX_NODES`` expanded nodes, a recursive alias, and turns a
``RecursionError`` into a plain ``ValueError``.
"""

from __future__ import annotations

from typing import Any

MAX_BYTES = 4 * 1024 * 1024
MAX_NODES = 2_000_000


def _expanded_size(node: Any, memo: dict[int, int], active: set[int]) -> int:
    import yaml  # type: ignore[import-untyped]

    key = id(node)
    if key in memo:
        return memo[key]
    if key in active:
        raise ValueError("YAML document has a recursive alias")
    active.add(key)
    size = 1
    if isinstance(node, yaml.SequenceNode):
        size += sum(_expanded_size(c, memo, active) for c in node.value)
    elif isinstance(node, yaml.MappingNode):
        size += sum(
            _expanded_size(k, memo, active) + _expanded_size(v, memo, active) for k, v in node.value
        )
    active.discard(key)
    memo[key] = size
    if size > MAX_NODES:
        raise ValueError(f"YAML document expands to more than {MAX_NODES} nodes (alias bomb)")
    return size


def safe_load_yaml(text: str) -> Any:
    """The parsed document; ``ValueError`` for anything that is not a bounded, safe document.
    (``yaml.YAMLError`` is not a ``ValueError``: it passes through for the caller to wrap.)"""
    import yaml

    if len(text) > MAX_BYTES:
        raise ValueError(f"YAML document larger than {MAX_BYTES} bytes")
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is not None:
            _expanded_size(root, {}, set())
        return yaml.safe_load(text)
    except RecursionError as exc:
        raise ValueError("YAML document is nested too deeply") from exc
