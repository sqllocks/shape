# shape-example-plugin

A complete Shape plugin in one small package: a **source** (`lines://path`), a **detector**
(`iban`) and a **command** (`shape hello`). It is the template to copy when you write your own,
and the plugin Shape's own tests install from outside the source tree.

```
src/shape_example_plugin/__init__.py   LinesSource, IbanDetector, HelloCommand, SHAPE_API
pyproject.toml                         entry points in shape.sources / shape.detectors / shape.commands
tests/test_conformance.py              the plugin passes the conformance kit
```

```bash
pip install -e .          # installs the plugin next to Shape
shape plugins list        # shows lines, iban and hello next to the built-ins
shape hello --name Ada    # hello, Ada
pytest                    # runs the conformance kit against the plugin
python -m shape.plugins.kit shape-example-plugin   # checks every entry point as installed
```

Nothing in Shape's core changes: the plugin is found through its entry points. Read
`docs/plugins/authoring.md` for how to write each kind of plugin.
