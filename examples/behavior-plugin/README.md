# shape-example-behavior

An example Shape plugin that adds one behavior module (`library_loans`) through the
`shape.behaviors` entry-point group. It needs `sqllocks-shape` and `sqllocks-shape-behavior`.

```bash
pip install .
python -m shape.plugins.kit shape-example-behavior      # conformance
shape plugins list                                      # shows shape.behaviors:library_loans
shape behave run library_loans --population 1000 --years 2 --seed 1 -o out/
```

See `docs/plugins/behavior.md` (section 10) in the Shape repository.
