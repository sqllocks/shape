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
MAX_FLOW_DEPTH = 100


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


def _check_flow_depth(text: str) -> None:
    """Refuse flow collections (``[`` and ``{``) nested deeper than ``MAX_FLOW_DEPTH``.

    PyYAML's scanner revisits every open flow collection for each token it reads, so ``[`` x
    10 000 costs seconds (quadratic) before the composer's recursion limit refuses it; a linear
    pass over the text bounds that cost. Quoted scalars and comments are skipped; an unbalanced
    bracket inside a block scalar is counted, which only matters past ``MAX_FLOW_DEPTH``."""
    depth = 0
    quote = ""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'" and (i == 0 or text[i - 1] in " \t\n,[{:-?"):
            quote = c  # a quote opens a scalar only at its start (``don't`` is a plain scalar)
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n"):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif c in "[{":
            depth += 1
            if depth > MAX_FLOW_DEPTH:
                raise ValueError("YAML document is nested too deeply")
        elif c in "]}" and depth:
            depth -= 1
        i += 1


def safe_load_yaml(text: str) -> Any:
    """The parsed document; ``ValueError`` for anything that is not a bounded, safe document.
    (``yaml.YAMLError`` is not a ``ValueError``: it passes through for the caller to wrap.)"""
    import yaml

    if len(text) > MAX_BYTES:
        raise ValueError(f"YAML document larger than {MAX_BYTES} bytes")
    _check_flow_depth(text)
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if root is not None:
            _expanded_size(root, {}, set())
        return yaml.safe_load(text)
    except RecursionError as exc:
        raise ValueError("YAML document is nested too deeply") from exc
