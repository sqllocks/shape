"""Writes the golden results of every contract fixture (run once, on the code before W7-03)."""
import json, sys, warnings
from pathlib import Path
import shape
warnings.simplefilter("ignore")
root = Path(sys.argv[1])
profile = shape.load(root / "tests/fixtures/profiles/pre_w3_07.shape")
files = sorted((root / "tests/fixtures/contracts").glob("*.json")) + sorted((root / "demo/contracts").glob("*.json")) + sorted((root / "docs/bridge/vectors/fixtures").glob("contract*.json"))
for f in files:
    try:
        text = json.dumps(shape.check(profile, f).to_dict(), indent=2)
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
    name = f.parent.name + "__" + f.stem
    (root / "tests/fixtures/contracts/golden" / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    print(name, text[:60].replace("\n", " "))
