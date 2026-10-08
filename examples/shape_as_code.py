"""Shape as code: profile rows, save the profile, load it back and generate from it.

Run it from any folder: ``python examples/shape_as_code.py`` (it writes ``customer.shape``
in the current folder).
"""

import shape

rows = [{"id": 1, "age": 42}, {"id": 2, "age": 37}, {"id": 3, "age": 42}]
profile = shape.profile(rows, name="customer")  # a list of row dicts, a file, a DataFrame...
shape.save(profile, "customer.shape")
loaded = shape.load("customer.shape")
print(loaded.summary())

result = shape.generate(loaded, 1000, seed=42)  # 1000 synthetic rows with the same shape
print({name: table.num_rows for name, table in result.tables.items()})
