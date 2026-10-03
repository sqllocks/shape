import sys
import threading
import traceback

sys.path.insert(0, "tests/scale")
from scale_schemas import plain_doc  # noqa: E402

from shape.generation import output as O  # noqa: E402
from shape.generation.engine import Engine  # noqa: E402
from shape.generation.schema import GenSchema  # noqa: E402
from shape_databases import PostgresSink  # noqa: E402
from shape_databases.testing import FakeServer  # noqa: E402

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}
seen: list[str] = []


def wrap(name):
    orig = getattr(PostgresSink, name)

    def f(self, *a, **k):
        try:
            return orig(self, *a, **k)
        except Exception:
            seen.append(f"[{name}]\n" + traceback.format_exc())
            raise

    setattr(PostgresSink, name, f)


for n in ("load", "_prepare", "_open"):
    wrap(n)
for i in range(400):
    server = FakeServer("postgres")
    PostgresSink.default_connect = lambda self, server=server, **p: server.connect(**p)
    eng = Engine(GenSchema.from_dict(plain_doc(ROWS)))
    t = threading.Thread(
        target=lambda: O.write_targets(
            eng, ["postgresql://shape@db.example/shape"], O.TargetOptions()
        ),
        daemon=True,
    )
    t.start()
    t.join(30)
    if seen or t.is_alive():
        print("iteration", i, "hung" if t.is_alive() else "")
        print(seen[0] if seen else "no exception captured")
        break
print("done", len(seen))
