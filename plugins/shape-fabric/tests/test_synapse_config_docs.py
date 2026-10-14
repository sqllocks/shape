"""Keep the Synapse option table in step with the consumed configuration keys."""

import ast
import inspect
import re
from pathlib import Path

import pytest
from shape_fabric import sinks

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]

# sinks.py:213 passes filesystem to SynapseWriter; _storage.py:59-60 returns the
# injected object, and :71/:75 call its .open()/.rm(). CLI strings/integers cannot supply
# that object: this is Python/test dependency injection, not --sink-config.
INTERNAL_OPTIONS = {"filesystem"}


def _read_keys(node: ast.AST, mapping: str) -> set[str]:
    keys = set()
    literal_variables = {
        part.target.id: set(ast.literal_eval(part.iter))
        for part in ast.walk(node)
        if isinstance(part, ast.comprehension)
        and isinstance(part.target, ast.Name)
        and isinstance(part.iter, (ast.Tuple, ast.List))
    }
    for call in ast.walk(node):
        if (
            isinstance(call, ast.Subscript)
            and isinstance(call.ctx, ast.Load)
            and isinstance(call.value, ast.Name)
            and call.value.id == mapping
        ):
            assert isinstance(call.slice, ast.Constant), "option keys must be explicit"
            keys.add(call.slice.value)
        if (
            isinstance(call, ast.Compare)
            and isinstance(call.left, ast.Constant)
            and len(call.ops) == 1
            and isinstance(call.ops[0], (ast.In, ast.NotIn))
            and isinstance(call.comparators[0], ast.Name)
            and call.comparators[0].id == mapping
        ):
            keys.add(call.left.value)
        if not isinstance(call, ast.Call):
            continue
        if (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == mapping
            and call.func.attr in {"pop", "get", "setdefault", "__getitem__", "__contains__"}
        ):
            key = call.args[0]
            if isinstance(key, ast.Name) and key.id in literal_variables:
                keys.update(literal_variables[key.id])
            else:
                assert isinstance(key, ast.Constant), "option keys must be explicit"
                keys.add(key.value)
        elif (
            isinstance(call.func, ast.Name)
            and call.func.id == "_take"
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == mapping
        ):
            assert isinstance(call.args[1], (ast.Tuple, ast.List)), "option keys must be explicit"
            keys.update(ast.literal_eval(call.args[1]))
    return keys


def _assert_documented_keys(source_text: str, page: str) -> None:
    source = ast.parse(source_text)
    sink = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "SynapseSink")
    prefix = next(
        n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "_prefixed_table"
    )
    accepted = _read_keys(sink, "opts") | _read_keys(prefix, "options")
    assert INTERNAL_OPTIONS <= accepted, "remove exclusions for keys no longer read by code"

    section = page.split("## `synapse://`:", 1)[1].split("\n## ", 1)[0]
    documented = set()
    for line in section.splitlines():
        if line.startswith("| `") and not line.startswith("| `synapse://"):
            documented.update(re.findall(r"`([a-z_][a-z0-9_]*)`", line.split("|")[1]))
    public = accepted - INTERNAL_OPTIONS
    assert documented == public, (
        f"Undocumented: {sorted(public - documented)}; "
        f"documented but not consumed: {sorted(documented - public)}"
    )


def test_synapse_documented_sink_config_keys_match_code():
    _assert_documented_keys(
        inspect.getsource(sinks), (ROOT / "docs/plugins/fabric-writers.md").read_text()
    )


@pytest.mark.parametrize(
    "read",
    [
        "opts.pop('new_option', None)",
        "opts.get('new_option')",
        "opts['new_option']",
        "opts.setdefault('new_option', None)",
        "opts.__getitem__('new_option')",
        "opts.__contains__('new_option')",
        "'new_option' in opts",
    ],
)
def test_added_sink_option_requires_documentation(read):
    source = inspect.getsource(sinks).replace(
        'given = opts.pop("connection_string", None)',
        f'{read}\n        given = opts.pop("connection_string", None)',
    )
    with pytest.raises(AssertionError, match="Undocumented.*new_option"):
        _assert_documented_keys(source, (ROOT / "docs/plugins/fabric-writers.md").read_text())


def test_removed_sink_option_requires_documentation_removal():
    source = inspect.getsource(sinks).replace(
        'commit_rows = opts.pop("commit_rows", None)', "commit_rows = None"
    )
    with pytest.raises(AssertionError, match="documented but not consumed.*commit_rows"):
        _assert_documented_keys(source, (ROOT / "docs/plugins/fabric-writers.md").read_text())


def test_removed_internal_option_requires_exclusion_removal():
    source = inspect.getsource(sinks).replace('opts.pop("filesystem", None)', "None")
    with pytest.raises(AssertionError, match="remove exclusions"):
        _assert_documented_keys(source, (ROOT / "docs/plugins/fabric-writers.md").read_text())


@pytest.mark.parametrize("remove", [False, True])
def test_changed_documented_options_require_matching_code(remove):
    page = (ROOT / "docs/plugins/fabric-writers.md").read_text()
    line = next(line for line in page.splitlines() if line.startswith("| `table_prefix` |"))
    page = page.replace(line, "" if remove else line + "\n| `new_option` | undocumented code |")
    with pytest.raises(
        AssertionError, match="Undocumented.*table_prefix" if remove else "new_option"
    ):
        _assert_documented_keys(inspect.getsource(sinks), page)
