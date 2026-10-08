"""The golden byte corpus (``tests/generation/golden_bytes/``): the SHA-256 of every CSV, TSV,
JSON Lines and SQL (every dialect) file Shape writes for a fixed set of specs at seed 42 and
:data:`ROWS` rows per table (``docs/GENERATION_STABILITY.md``, "Byte-identical files").

The specs are the pinned spec of every built-in strategy and distribution family
(``tests/generation/pinned/``) and the specs in ``golden_bytes/specs/`` (every ``native`` provider,
so every reference pool, and every column type with the values a writer must escape or format).
CSV and TSV have no nested values, so a table with a struct or list column is written in the other
formats only.

``hashes.json`` (``format: "shape-golden-bytes"``, integer ``version``) holds the digest of each
file; ``lines.json`` (``format: "shape-golden-bytes-lines"``) a short digest of each line, so a
difference is reported as the first line that differs.

    python scripts/golden_bytes.py            # compare a fresh run with the corpus (exit 1 if not)
    python scripts/golden_bytes.py --update   # rewrite the corpus

The run uses the kernel ``SHAPE_KERNEL`` selects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PINNED = ROOT / "tests" / "generation" / "pinned"
CORPUS = ROOT / "tests" / "generation" / "golden_bytes"
SPECS = CORPUS / "specs"
HASHES = CORPUS / "hashes.json"
LINES = CORPUS / "lines.json"
FORMAT = "shape-golden-bytes"
LINES_FORMAT = "shape-golden-bytes-lines"
VERSION = 1
SEED = 42
ROWS = 100
DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")
# (sink format, its options, the file name suffix) for every file written per table.
OUTPUTS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("csv", {}, "csv"),
    ("tsv", {}, "tsv"),
    ("jsonl", {}, "jsonl"),
    *(("sql", {"sql_dialect": d}, f"{d}.sql") for d in DIALECTS),
)
LINE_DIGEST = 8  # hex characters of each line's digest in lines.json


class CorpusError(ValueError):
    """A corpus file this script cannot read."""


def spec_names() -> list[str]:
    """Every spec of the corpus: the pinned stems, then ``golden-<name>`` for each own spec."""
    pinned = [p.stem for p in sorted(PINNED.glob("*.json")) if p.name != "expected.json"]
    own = [f"golden-{p.stem}" for p in sorted(SPECS.glob("*.json"))]
    return pinned + own


def load_spec(name: str) -> dict[str, Any]:
    path = SPECS / f"{name[len('golden-') :]}.json" if name.startswith("golden-") else None
    path = path or PINNED / f"{name}.json"
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


@contextmanager
def _datasets() -> Iterator[None]:
    """The reference datasets the pinned specs read (``tests/generation/pinned_support.py``)."""
    sys.path.insert(0, str(PINNED.parent))
    try:
        import pinned_support  # type: ignore[import-not-found]
    finally:
        sys.path.remove(str(PINNED.parent))
    with pinned_support.datasets():
        yield


def _result(doc: Mapping[str, Any]) -> Any:
    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema

    schema = GenSchema.from_dict(dict(doc))
    return Engine(schema, seed=SEED, row_counts={t: ROWS for t in schema.tables}).generate()


def _writable(result: Any, fmt: str) -> Any:
    """``result`` without the tables ``fmt`` cannot write: CSV and TSV have no nested values (a
    struct or list column), so a table that has one is not written as CSV or TSV."""
    import pyarrow as pa  # type: ignore[import-untyped]

    if fmt not in ("csv", "tsv"):
        return result
    names = [
        n
        for n in result.generation_order
        if not any(pa.types.is_nested(f.type) for f in result.tables[n].schema)
    ]
    return replace(
        result,
        tables={n: result.tables[n] for n in names},
        generation_order=names,
        row_counts={n: result.tables[n].num_rows for n in names},
    )


def write_all(out: Path, names: list[str] | None = None) -> dict[str, bytes]:
    """Write every spec of the corpus (or ``names``) in every output under ``out``; the bytes of
    each file, keyed ``<spec>/<table>.<suffix>``."""
    from shape.generation.output import write_result

    files: dict[str, bytes] = {}
    with _datasets():
        for name in names if names is not None else spec_names():
            result = _result(load_spec(name))
            for fmt, options, suffix in OUTPUTS:
                written = _writable(result, fmt)
                if not written.tables:
                    continue
                folder = out / name / suffix
                for path in write_result(written, fmt, folder, max_workers=1, **options):
                    files[f"{name}/{path.stem}.{suffix}"] = path.read_bytes()
    return dict(sorted(files.items()))


def line_digests(data: bytes) -> str:
    """A short digest of every line of ``data`` (split at ``\\n``), separated by spaces."""
    return " ".join(hashlib.sha256(line).hexdigest()[:LINE_DIGEST] for line in data.split(b"\n"))


def corpus_of(files: Mapping[str, bytes]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``hashes.json`` and ``lines.json`` for ``files``."""
    import shape

    hashes = {
        "format": FORMAT,
        "version": VERSION,
        "seed": SEED,
        "rows": ROWS,
        "shape_version": shape.__version__,
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
    }
    lines = {
        "format": LINES_FORMAT,
        "version": VERSION,
        "files": {k: line_digests(v) for k, v in files.items()},
    }
    return hashes, lines


def _check(doc: Any, fmt: str, where: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("format") != fmt:
        raise CorpusError(f"{where} is not a golden byte corpus file (format is not {fmt!r})")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CorpusError(f"{where} has no integer version")
    if version > VERSION:
        raise CorpusError(
            f"{where} is version {version}, written by a newer Shape; this script reads up to "
            f"version {VERSION}"
        )
    if not isinstance(doc.get("files"), dict):
        raise CorpusError(f"{where} has no files map")
    return doc


def load_corpus(
    hashes: Path = HASHES, lines: Path = LINES
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The committed corpus, checked (format, a version this script reads, a files map)."""
    out = []
    for path, fmt in ((hashes, FORMAT), (lines, LINES_FORMAT)):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorpusError(f"{path} is not readable: {exc}") from exc
        out.append(_check(doc, fmt, str(path)))
    return out[0], out[1]


def differences(
    hashes: Mapping[str, Any], lines: Mapping[str, Any], files: Mapping[str, bytes]
) -> list[str]:
    """One report per file that differs from the corpus: a missing or unexpected file, or the
    first line that differs (its number, the line written now and the committed line's digest)."""
    import shape

    want: Mapping[str, str] = hashes["files"]
    reports = [f"{k}: in the corpus but not written" for k in sorted(set(want) - set(files))]
    reports += [f"{k}: written but not in the corpus" for k in sorted(set(files) - set(want))]
    for key in sorted(set(want) & set(files)):
        data = files[key]
        if hashlib.sha256(data).hexdigest() == want[key]:
            continue
        committed = str(lines["files"].get(key, "")).split()
        got = data.split(b"\n")
        number = next(
            (
                i
                for i, line in enumerate(got)
                if i >= len(committed)
                or hashlib.sha256(line).hexdigest()[:LINE_DIGEST] != committed[i]
            ),
            len(got),
        )
        text = got[number].decode("utf-8", "replace") if number < len(got) else "<end of file>"
        expected = committed[number] if number < len(committed) else "<end of file>"
        reports.append(
            f"{key}: differs from the corpus at line {number + 1}\n"
            f"    written now: {text!r}\n"
            f"    corpus line digest: {expected} (the corpus has {len(committed)} lines, the "
            f"file {len(got)})"
        )
    if reports and hashes.get("shape_version") != shape.__version__:
        reports.append(
            f"note: the corpus was written by Shape {hashes.get('shape_version')}, this is "
            f"{shape.__version__}; SQL files name the version, so a release rewrites the corpus "
            "(python scripts/golden_bytes.py --update)"
        )
    return reports


def _dump(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="rewrite the corpus")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        files = write_all(Path(tmp))
    if args.update:
        hashes, lines = corpus_of(files)
        HASHES.write_text(_dump(hashes), encoding="utf-8", newline="\n")
        LINES.write_text(_dump(lines), encoding="utf-8", newline="\n")
        print(f"wrote {len(files)} file digests to {HASHES.relative_to(ROOT).as_posix()}")
        return 0
    hashes, lines = load_corpus(HASHES, LINES)
    reports = differences(hashes, lines, files)
    for report in reports:
        print(report)
    if reports:
        return 1
    print(f"{len(files)} files match the golden byte corpus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
