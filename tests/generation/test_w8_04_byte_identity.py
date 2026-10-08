"""W8-04 (#567): byte-identical CSV, JSON Lines and SQL output per Shape version on every platform.

1. The promise is documented (``docs/GENERATION_STABILITY.md``, "Byte-identical files").
2. The writers have no byte-level dependency on the platform: ``\\n`` line ends (Windows text I/O
   simulated with ``_pyio`` and ``os.linesep``), UTF-8 without BOM whatever the locale encoding,
   shortest round-trip floats, no local time zone, no locale, no unordered iteration.
3. The reference pools are frozen by ``pools/MANIFEST.json``.
4. The golden byte corpus (``tests/generation/golden_bytes/``) is regenerated in both kernel modes.
5. The corpus gives the same hashes under a non-UTF-8 locale, ``TZ=Pacific/Chatham``, another
   ``PYTHONHASHSEED`` and another thread count (this file runs in the CI matrix on Linux, Windows
   and macOS).
6. The run manifest records the writer format versions; ``shape.repro.file_hashes``.
"""

from __future__ import annotations

import _pyio
import ast
import codecs
import datetime as dt
import decimal
import hashlib
import importlib.util
import io
import json
import locale
import math
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from shape.builtins.sinks import CsvSink, JsonlSink, SqlSink, TsvSink
from shape.builtins.strategies import pool_manifest as pm
from shape.generation.output import write_result, writer_format_versions
from shape.kernel import dispatch
from shape.plugins.host import default_host
from shape.repro import file_hashes

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "GENERATION_STABILITY.md"
DIALECTS = ("tsql", "tsql-fabric-warehouse", "postgres", "mysql")

_spec = importlib.util.spec_from_file_location("golden_bytes", ROOT / "scripts" / "golden_bytes.py")
assert _spec is not None and _spec.loader is not None
gb = importlib.util.module_from_spec(_spec)
sys.modules["golden_bytes"] = gb
_spec.loader.exec_module(gb)


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    dispatch.reset()


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : end if end != -1 else len(text)]


# ---- 1. the promise -----------------------------------------------------------------------------


def test_the_promise_is_documented() -> None:
    text = DOC.read_text(encoding="utf-8")
    section = _section(text, "## Byte-identical files")
    for needle in (
        "CSV",
        "JSON Lines",
        "SQL",
        *DIALECTS,
        "Linux",
        "Windows",
        "macOS",
        "x86-64",
        "arm64",
        "SHAPE_KERNEL=rust",
        "SHAPE_KERNEL=python",
        "locale",
        "time zone",
        "PYTHONHASHSEED",
        "thread",
        "file-system",
        "writer options",
        "Shape version",
    ):
        assert needle in section, needle
    covered = _section(section, "### What is not covered")
    for needle in ("Parquet", "Delta", "Excel", "binary", "different Shape versions", "plugin"):
        assert needle in covered, needle
    assert "content promise" in covered


def test_the_page_no_longer_says_text_files_are_not_byte_stable() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "CSV, Parquet and the other file formats are not byte-stable" not in text
    assert text.count("## Byte-identical files") == 1


def test_the_page_documents_the_pools_the_corpus_and_the_version_guard() -> None:
    section = _section(DOC.read_text(encoding="utf-8"), "## Byte-identical files")
    for needle in (
        "pools/MANIFEST.json",
        "shape-pool-manifest",
        "python -m shape.builtins.strategies.pool_manifest --write",
        "tests/generation/golden_bytes/",
        "scripts/golden_bytes.py --update",
        "shape-golden-bytes",
        "shape.repro.file_hashes",
        "format_version",
        "reproducibility.writers",
        "generator version",
    ):
        assert needle in section, needle


# ---- 2. the writers -----------------------------------------------------------------------------

TEXTS = [
    "plain",
    "comma, inside",
    'quote " inside',
    "line\nbreak",
    "carriage\r\nreturn",
    "tab\tinside",
    "back\\slash",
    "O'Brien",
    "",
    "é ü ß ñ",
    "日本語",
    "emoji 😀",
    None,
]
FLOATS = [
    0.1,
    1 / 3,
    2 / 3,
    1e-7,
    1e-5,
    0.0001,
    1e16,
    1e22,
    123456789.0,
    1.0,
    -0.0,
    0.0,
    5e-324,
    2.2250738585072014e-308,
    1.7976931348623157e308,
    -2.5,
    math.pi,
]


def _values_table() -> pa.Table:
    n = len(TEXTS)
    utc = dt.UTC
    return pa.table(
        {
            "id": pa.array(range(n), pa.int64()),
            "text": pa.array(TEXTS, pa.string()),
            "f": pa.array((FLOATS * 2)[:n], pa.float64()),
            "amount": pa.array(
                [decimal.Decimal(f"{i * 1234.5678 - 4000:.4f}") for i in range(n)],
                pa.decimal128(12, 4),
            ),
            "naive": pa.array(
                [dt.datetime(1969, 12, 31, 23, 30) + dt.timedelta(hours=7 * i) for i in range(n)],
                pa.timestamp("us"),
            ),
            "utc": pa.array(
                [
                    dt.datetime(2026, 3, 29, 0, 30, tzinfo=utc) + dt.timedelta(hours=i)
                    for i in range(n)
                ],
                pa.timestamp("us", tz="UTC"),
            ),
            "day": pa.array([dt.date(1900, 1, 1) + dt.timedelta(days=9000 * i) for i in range(n)]),
            "flag": pa.array([i % 3 == 0 for i in range(n)]),
        }
    )


def _sinks() -> list[tuple[str, Any, dict[str, Any]]]:
    out: list[tuple[str, Any, dict[str, Any]]] = [
        ("csv", CsvSink(), {}),
        ("tsv", TsvSink(), {}),
        ("jsonl", JsonlSink(), {}),
    ]
    out += [(f"sql-{d}", SqlSink(), {"sql_dialect": d, "primary_key": ["id"]}) for d in DIALECTS]
    return out


SINK_IDS = [name for name, _, _ in _sinks()]


def _write_all(folder: Path, table: pa.Table | None = None) -> dict[str, bytes]:
    t = table if table is not None else _values_table()
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, sink, options in _sinks():
        if "id" not in t.column_names:  # a key names a column of the table (HUNT2-io)
            options = {k: v for k, v in options.items() if k != "primary_key"}
        target = folder / f"t.{name}"
        sink.write(
            str(target), "t", iter(t.to_batches(max_chunksize=5)), schema=t.schema, **options
        )
        out[name] = target.read_bytes()
    return out


def simulate_windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text I/O as on Windows: ``\\r\\n`` for a text-mode ``\\n`` and the cp1252 code page for a
    file opened without an encoding, also when this interpreter runs in UTF-8 mode (the pure-Python
    ``io`` reads all of it at run time)."""
    monkeypatch.setattr(io, "open", _pyio.open)
    monkeypatch.setattr(os, "linesep", "\r\n")
    for module in (io, _pyio):
        monkeypatch.setattr(
            module, "text_encoding", lambda encoding, stacklevel=2: encoding or "locale"
        )
    monkeypatch.setattr(locale, "getencoding", lambda: "cp1252", raising=False)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252")


@pytest.fixture
def windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    simulate_windows_text_io(monkeypatch)


def test_the_windows_text_io_simulation_works(tmp_path: Path, windows_text_io: None) -> None:
    """The fixture is meaningful: a text write without ``newline`` gets CRLF and cp1252."""
    path = tmp_path / "x.txt"
    with path.open("w") as fh:
        fh.write("é\n")
    assert path.read_bytes() == "é\r\n".encode("cp1252")


@pytest.mark.parametrize("name", SINK_IDS)
def test_writers_write_lf_and_utf8_under_windows_text_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Fails before W8-04 for ``jsonl``: its file was opened without ``newline="\\n"``."""
    control = _write_all(tmp_path / "control")[name]
    simulate_windows_text_io(monkeypatch)
    assert _write_all(tmp_path / "windows")[name] == control


@pytest.mark.parametrize("name", SINK_IDS)
def test_files_are_utf8_without_a_bom(tmp_path: Path, name: str) -> None:
    data = _write_all(tmp_path)[name]
    assert not data.startswith(codecs.BOM_UTF8)
    text = data.decode("utf-8")
    assert "日本語" in text and "😀" in text and "é ü ß ñ" in text


@pytest.mark.parametrize("name", SINK_IDS)
def test_line_ends_are_lf(tmp_path: Path, name: str) -> None:
    table = _values_table().filter(pa.array([t is None or "\r" not in t for t in TEXTS]))
    data = _write_all(tmp_path, table)[name]
    assert b"\r" not in data and data.endswith(b"\n")


def test_writers_never_consult_the_locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    control = _write_all(tmp_path / "control")

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a writer consulted the locale")

    for name in (
        "localeconv",
        "getlocale",
        "getencoding",
        "getpreferredencoding",
        "setlocale",
        "format_string",
        "str",
        "currency",
        "atof",
        "atoi",
        "delocalize",
        "localize",
        "nl_langinfo",
    ):
        if hasattr(locale, name):
            monkeypatch.setattr(locale, name, refuse)
    assert _write_all(tmp_path / "patched") == control


def _normal(text: str) -> tuple[str, int]:
    """The significant digits of a decimal number and the power of ten of the first one."""
    text = text.strip().lstrip("+-")
    mantissa, _, exponent = text.lower().partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = (whole + fraction).lstrip("0")
    lead = len(whole + fraction) - len(digits)
    digits = digits.rstrip("0") or "0"
    power = len(whole) - 1 - lead + int(exponent or 0)
    return digits, power if digits != "0" else 0


def _float_cells(name: str, data: bytes) -> list[str]:
    text = data.decode("utf-8")
    if name in ("csv", "tsv"):
        delim = "," if name == "csv" else "\t"
        lines = text.splitlines()
        return [line.split(delim)[0] for line in lines[1:]]
    if name == "jsonl":
        return [re.search(r'"f":([^,}]+)', line).group(1) for line in text.splitlines()]  # type: ignore[union-attr]
    return re.findall(r"^\s+\((-?[0-9.e+-]+)\)", text, flags=re.M | re.I)


@pytest.mark.parametrize("name", SINK_IDS)
def test_floats_are_the_shortest_round_trip_digits(tmp_path: Path, name: str) -> None:
    table = pa.table({"f": pa.array(FLOATS, pa.float64())})
    data = _write_all(tmp_path, table)[name]
    cells = _float_cells(name, data)
    assert len(cells) == len(FLOATS), cells
    for value, cell in zip(FLOATS, cells, strict=True):
        back = float(cell)
        assert back == value and math.copysign(1, back) == math.copysign(1, value), (cell, value)
        assert _normal(cell) == _normal(repr(value)), (cell, repr(value))


def test_the_float_digit_helper() -> None:
    assert _normal("1e-7") == _normal("1e-07") == ("1", -7)
    assert _normal("123456789") == _normal("123456789.0") == ("123456789", 8)
    assert _normal("0.0001") == _normal("1e-04") == ("1", -4)
    assert _normal("-0") == _normal("0.0") == ("0", 0)
    assert _normal("1.5") != _normal("1.50001")


_CHILD = textwrap.dedent(
    """
    import hashlib, json, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[2])
    import test_w8_04_byte_identity as t
    files = t._write_all(Path(sys.argv[1]))
    print(json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in files.items()}))
    """
)

FOREIGN = {
    "TZ": "Pacific/Chatham",
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONUTF8": "0",
    "PYTHONCOERCECLOCALE": "0",
    "PYTHONHASHSEED": "4242",
    "SHAPE_THREADS": "1",
}


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in FOREIGN}
    return {**env, **extra}


@pytest.mark.parametrize(
    "extra",
    [
        {"TZ": "Pacific/Chatham"},
        {"TZ": "America/St_Johns", "PYTHONHASHSEED": "1"},
        FOREIGN,
    ],
    ids=["chatham", "st-johns-hashseed", "foreign-locale"],
)
def test_writers_ignore_time_zone_locale_and_hash_seed(
    tmp_path: Path, extra: dict[str, str]
) -> None:
    control = {k: hashlib.sha256(v).hexdigest() for k, v in _write_all(tmp_path / "c").items()}
    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path / "child"), str(Path(__file__).parent)],
        env=_child_env(extra),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == control


def test_timestamps_with_a_zone_are_written_in_that_zone_not_the_local_one(tmp_path: Path) -> None:
    data = _write_all(tmp_path)
    assert "2026-03-29T00:30:00+00:00" in data["jsonl"].decode()
    assert "2026-03-29 00:30:00" in data["sql-postgres"].decode()
    assert "1969-12-31T23:30:00" in data["jsonl"].decode()
    assert "1969-12-31 23:30:00" in data["csv"].decode()


def test_written_bytes_do_not_depend_on_writer_threads(tmp_path: Path) -> None:
    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema

    doc = gb.load_spec("golden-types")
    doc["tables"]["second"] = {**doc["tables"]["values"], "name": "second"}
    schema = GenSchema.from_dict(doc)
    result = Engine(schema, seed=42, row_counts={t: 50 for t in schema.tables}).generate()
    seen = []
    for workers in (1, 4):
        out = tmp_path / str(workers)
        files = []
        for fmt, options, _ in gb.OUTPUTS:
            files += write_result(result, fmt, out / fmt, max_workers=workers, **options)
        seen.append({p.relative_to(out).as_posix(): p.read_bytes() for p in files})
    assert seen[0] == seen[1]


_UNORDERED_CALLS = {"set", "frozenset", "listdir", "scandir", "iterdir", "glob", "rglob", "walk"}


def unordered_iterations(source: str) -> list[int]:
    """The lines of ``source`` that iterate a set or a directory listing without ``sorted``."""

    def unordered(node: ast.AST) -> bool:
        if isinstance(node, (ast.Set, ast.SetComp)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            return name in _UNORDERED_CALLS
        return False

    found = []
    for node in ast.walk(ast.parse(source)):
        iters: list[ast.AST] = []
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iters.append(node.iter)
        elif isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.DictComp, ast.SetComp)):
            iters += [g.iter for g in node.generators]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("list", "tuple"):
            iters += node.args
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "join":
            iters += node.args
        found += [n.lineno for n in iters if unordered(n)]
    return sorted(found)


def test_the_unordered_iteration_check_finds_what_it_should() -> None:
    assert unordered_iterations("for x in set(y):\n    pass\n") == [1]
    assert unordered_iterations("a = [p for p in d.iterdir()]\n") == [1]
    assert unordered_iterations("a = ','.join({'b', 'c'})\n") == [1]
    assert unordered_iterations("for x in sorted(set(y)):\n    pass\n") == []
    assert unordered_iterations("a = [p for p in sorted(d.glob('*'))]\n") == []


@pytest.mark.parametrize(
    "module",
    [
        "src/shape/builtins/sinks/files.py",
        "src/shape/builtins/sinks/sql.py",
        "src/shape/builtins/strategies/pool_manifest.py",
        "src/shape/repro.py",
        "scripts/golden_bytes.py",
    ],
)
def test_writers_never_iterate_an_unordered_collection(module: str) -> None:
    assert unordered_iterations((ROOT / module).read_text(encoding="utf-8")) == []


def test_provenance_sidecar_is_lf_under_windows_text_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails before W8-04: the sidecar a run writes beside its files was written without
    ``newline="\\n"``, so ``file_hashes`` of a run differed on Windows."""
    from shape.io.provenance import PROVENANCE_FILE, write_provenance

    (tmp_path / "a.csv").write_bytes(b"x\n1\n")
    simulate_windows_text_io(monkeypatch)
    write_provenance(tmp_path, [(tmp_path / "a.csv", 1)], seed=42)
    assert b"\r" not in (tmp_path / PROVENANCE_FILE).read_bytes()


# ---- 3. frozen reference pools -------------------------------------------------------------------


def test_the_pool_manifest_is_a_versioned_format() -> None:
    raw = json.loads(pm.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert raw["format"] == pm.FORMAT == "shape-pool-manifest"
    assert raw["version"] == pm.VERSION == 1 and type(raw["version"]) is int
    assert pm.MANIFEST_PATH == ROOT / "src/shape/builtins/strategies/pools/MANIFEST.json"


def test_every_shipped_pool_matches_the_manifest() -> None:
    assert pm.verify() == []


def test_the_manifest_lists_every_pool_and_locale_value_list() -> None:
    doc = pm.load()
    listed = set(doc["files"])
    pools = {f"pools/{p.name}" for p in (pm.PACKAGE_DIR / "pools").glob("*.txt")}
    locales = {
        f"locales/{p.name}"
        for p in (pm.PACKAGE_DIR / "locales").iterdir()
        if p.name != "MANIFEST.json"
    }
    assert pools and locales and listed == pools | locales
    for name, entry in doc["files"].items():
        data = (pm.PACKAGE_DIR / name).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest() and entry["bytes"] == len(data)


def test_drawn_by_names_the_strategies_that_read_each_pool() -> None:
    files = pm.load()["files"]
    for name in files:
        drawn = set(files[name]["drawn_by"])
        if name.startswith("locales/"):
            assert drawn == {"locale"}, name
        else:
            assert {"native", "faker"} <= drawn, name
    assert "locale" in files["pools/first_names.txt"]["drawn_by"]
    assert "locale" in files["pools/last_names.txt"]["drawn_by"]
    assert "address" in files["pools/street_names.txt"]["drawn_by"]
    assert "address" in files["pools/street_suffixes.txt"]["drawn_by"]
    current = pm.strategy_versions()
    for entry in files.values():
        for strategy, version in entry["drawn_by"].items():
            assert current[strategy] == version


def test_the_providers_read_only_listed_pools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pool a ``native`` provider reads is in the manifest (a new pool must be listed)."""
    from shape.builtins.strategies import providers

    read: list[str] = []
    original = providers.pool.__wrapped__

    def spy(name: str) -> pa.Array:
        read.append(name)
        return original(name)

    monkeypatch.setattr(providers, "pool", spy)
    gb._result(gb.load_spec("golden-providers"))
    listed = set(pm.load()["files"])
    assert read and {f"pools/{n}.txt" for n in read} <= listed


def _copy_pools(tmp_path: Path) -> Path:
    for folder in pm.POOL_DIRS:
        (tmp_path / folder).mkdir()
        for p in (pm.PACKAGE_DIR / folder).iterdir():
            if p.is_file():
                (tmp_path / folder / p.name).write_bytes(p.read_bytes())
    return tmp_path


def test_a_changed_pool_fails_and_says_what_to_do(tmp_path: Path) -> None:
    root = _copy_pools(tmp_path)
    path = root / "pools" / "first_names.txt"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n", 1))  # one byte more
    problems = pm.verify(root=root)
    assert len(problems) == 1
    assert problems[0].startswith("pools/first_names.txt: its bytes differ")
    assert "raise the generator version of faker, locale, native" in problems[0]


def test_a_missing_or_unlisted_pool_fails(tmp_path: Path) -> None:
    root = _copy_pools(tmp_path)
    (root / "pools" / "us_states.txt").unlink()
    (root / "pools" / "new_pool.txt").write_text("a\n", encoding="utf-8")
    problems = pm.verify(root=root)
    assert "pools/new_pool.txt: shipped but not listed in the pool manifest" in problems
    assert "pools/us_states.txt: listed in the pool manifest but not shipped" in problems
    assert len(problems) == 2


def test_a_raised_generator_version_needs_the_manifest_updated() -> None:
    versions = {**pm.strategy_versions(), "native": 2}
    problems = pm.verify(versions=versions)
    assert problems and all("native version 1, but native is at version 2" in p for p in problems)
    assert len(problems) == len(pm.load()["files"]) - 7  # every pool but the 7 locale files


def test_drawn_by_must_name_a_strategy() -> None:
    doc = pm.load()
    doc["files"]["pools/us_states.txt"]["drawn_by"]["nonesuch"] = 1
    assert pm.verify(doc) == [
        "pools/us_states.txt: drawn_by names nonesuch, which is not a strategy"
    ]


def test_build_reproduces_the_committed_manifest() -> None:
    assert pm.dump(pm.build(pm.load())) == pm.MANIFEST_PATH.read_text(encoding="utf-8")


def test_the_manifest_reader_is_compatible(tmp_path: Path) -> None:
    good = pm.load()
    with_extra = {**good, "note": "a later field", "files": dict(good["files"])}
    assert pm.parse(with_extra)["files"] == good["files"]  # unknown fields are ignored
    newer = {**good, "version": pm.VERSION + 1}
    with pytest.raises(pm.PoolManifestError, match="newer Shape"):
        pm.parse(newer)
    for bad, message in (
        ({**good, "format": "other"}, "not a pool manifest"),
        ({k: v for k, v in good.items() if k != "format"}, "not a pool manifest"),
        ({**good, "version": True}, "no integer version"),
        ({**good, "version": "1"}, "no integer version"),
        ({**good, "files": []}, "no files map"),
        ({**good, "files": {"pools/x.txt": {"drawn_by": {}}}}, "has no sha256"),
        (
            {**good, "files": {"pools/x.txt": {"sha256": "0", "drawn_by": {"native": 0}}}},
            "drawn_by",
        ),
        ([], "not a pool manifest"),
    ):
        with pytest.raises(pm.PoolManifestError, match=message):
            pm.parse(bad)
    broken = tmp_path / "MANIFEST.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(pm.PoolManifestError, match="not a readable pool manifest"):
        pm.load(broken)


def test_the_pool_manifest_command_checks(capsys: pytest.CaptureFixture[str]) -> None:
    assert pm.main([]) == 0
    assert capsys.readouterr().out == ""


# ---- 4. the golden byte corpus ------------------------------------------------------------------


def test_the_corpus_is_a_versioned_format() -> None:
    hashes, lines = gb.load_corpus()
    assert hashes["format"] == "shape-golden-bytes" and hashes["version"] == 1
    assert lines["format"] == "shape-golden-bytes-lines" and lines["version"] == 1
    assert (hashes["seed"], hashes["rows"]) == (gb.SEED, gb.ROWS) == (42, 100)
    assert set(hashes["files"]) == set(lines["files"])
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes["files"].values())


def test_the_corpus_covers_every_strategy_distribution_writer_and_dialect() -> None:
    from shape.builtins.distributions.families import FAMILIES
    from shape.builtins.strategies.providers import PROVIDERS, SPEC_PROVIDERS
    from shape.generation import spec_keys
    from shape.generation.spec_schema import strategy_names

    names = gb.spec_names()
    used: set[str] = set()
    families: set[str] = set()
    providers: set[str] = set()
    for name in names:
        for table in gb.load_spec(name)["tables"].values():
            for column in table["columns"].values():
                gen = column["generator"]
                used.add(gen["strategy"])
                if gen["strategy"] == "distribution":
                    families.add(gen.get("distribution", "uniform"))
                if gen["strategy"] == "native":
                    providers.add(gen.get("provider", "word"))
    assert set(strategy_names()) <= used
    assert {n for n in spec_keys.FAMILY_KEYS if n in FAMILIES} <= families
    assert set(PROVIDERS) | set(SPEC_PROVIDERS) <= providers
    files = gb.load_corpus()[0]["files"]
    suffixes = {key.split("/", 1)[1].split(".", 1)[1] for key in files}
    assert suffixes == {"csv", "tsv", "jsonl", *(f"{d}.sql" for d in DIALECTS)}
    assert {key.split("/", 1)[0] for key in files} == set(names)


def test_the_golden_bytes_are_unchanged(kernel: str, tmp_path: Path) -> None:
    hashes, lines = gb.load_corpus()
    reports = gb.differences(hashes, lines, gb.write_all(tmp_path))
    assert reports == [], "\n".join(reports)


def test_a_difference_names_the_first_differing_line(tmp_path: Path) -> None:
    hashes, lines = gb.load_corpus()
    files = gb.write_all(tmp_path, ["golden-types"])
    key = "golden-types/values.csv"
    rows = files[key].split(b"\n")
    rows[3] = b"changed,line"
    changed = {**files, key: b"\n".join(rows)}
    subset = {k: v for k, v in hashes["files"].items() if k.startswith("golden-types/")}
    reports = gb.differences({**hashes, "files": subset}, lines, changed)
    assert len(reports) == 1
    assert reports[0].startswith(f"{key}: differs from the corpus at line 4\n")
    assert "written now: 'changed,line'" in reports[0]
    digest = lines["files"][key].split()[3]
    assert f"corpus line digest: {digest}" in reports[0]


def test_a_longer_shorter_missing_or_extra_file_is_reported(tmp_path: Path) -> None:
    hashes, lines = gb.load_corpus()
    files = gb.write_all(tmp_path, ["golden-types"])
    subset = {k: v for k, v in hashes["files"].items() if k.startswith("golden-types/")}
    corpus = {**hashes, "files": subset}
    key = "golden-types/values.jsonl"
    longer = gb.differences(corpus, lines, {**files, key: files[key] + b"extra\n"})
    count = len(lines["files"][key].split())
    assert f"at line {count}\n" in longer[0] and "written now: 'extra'" in longer[0]
    shorter = gb.differences(corpus, lines, {**files, key: files[key][:-1]})
    assert "<end of file>" in shorter[0] or "written now" in shorter[0]
    missing = dict(files)
    del missing[key]
    assert gb.differences(corpus, lines, missing) == [f"{key}: in the corpus but not written"]
    extra = {**files, "golden-types/new.csv": b"x\n"}
    assert gb.differences(corpus, lines, extra) == [
        "golden-types/new.csv: written but not in the corpus"
    ]
    assert gb.differences(corpus, lines, files) == []


def test_a_difference_after_a_release_says_to_rewrite_the_corpus(tmp_path: Path) -> None:
    hashes, lines = gb.load_corpus()
    files = gb.write_all(tmp_path, ["golden-types"])
    subset = {k: v for k, v in hashes["files"].items() if k.startswith("golden-types/")}
    key = "golden-types/values.csv"
    corpus = {**hashes, "files": subset, "shape_version": "0.0.1"}
    reports = gb.differences(corpus, lines, {**files, key: b"x"})
    assert reports[-1].startswith("note: the corpus was written by Shape 0.0.1")
    assert "python scripts/golden_bytes.py --update" in reports[-1]
    assert gb.differences(corpus, lines, files) == []  # no difference, no note


def test_line_digests_split_at_lf_only() -> None:
    assert gb.line_digests(b"a\r\nb\n") == " ".join(
        hashlib.sha256(x).hexdigest()[: gb.LINE_DIGEST] for x in (b"a\r", b"b", b"")
    )
    assert gb.line_digests(b"") == hashlib.sha256(b"").hexdigest()[: gb.LINE_DIGEST]


def test_the_corpus_reader_is_compatible(tmp_path: Path) -> None:
    hashes, lines = gb.load_corpus()

    def load(doc: Any) -> Any:
        h = tmp_path / "h.json"
        h.write_text(json.dumps(doc), encoding="utf-8")
        return gb.load_corpus(h, gb.LINES)

    assert load({**hashes, "later": 1})[0]["files"] == hashes["files"]
    for bad, message in (
        ({**hashes, "version": 2}, "newer Shape"),
        ({**hashes, "version": True}, "no integer version"),
        ({**hashes, "format": "shape-golden-bytes-lines"}, "not a golden byte corpus"),
        ({**hashes, "files": []}, "no files map"),
    ):
        with pytest.raises(gb.CorpusError, match=message):
            load(bad)
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(gb.CorpusError, match="not readable"):
        gb.load_corpus(tmp_path / "bad.json", gb.LINES)


def test_update_rewrites_the_corpus_and_check_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gb, "HASHES", tmp_path / "hashes.json")
    monkeypatch.setattr(gb, "LINES", tmp_path / "lines.json")
    monkeypatch.setattr(gb, "ROOT", tmp_path)
    assert gb.main(["--update"]) == 0
    committed = json.loads((ROOT / "tests/generation/golden_bytes/hashes.json").read_text("utf-8"))
    assert json.loads((tmp_path / "hashes.json").read_text("utf-8")) == committed
    assert (tmp_path / "lines.json").read_bytes() == (
        ROOT / "tests/generation/golden_bytes/lines.json"
    ).read_bytes()
    assert gb.main([]) == 0
    assert "418 files match the golden byte corpus" in capsys.readouterr().out
    doc = json.loads((tmp_path / "hashes.json").read_text("utf-8"))
    key = sorted(doc["files"])[0]
    doc["files"][key] = "0" * 64
    (tmp_path / "hashes.json").write_text(json.dumps(doc), encoding="utf-8")
    assert gb.main([]) == 1
    assert f"{key}: differs from the corpus" in capsys.readouterr().out


# ---- 5. the cross-platform run ------------------------------------------------------------------


def _foreign_env() -> dict[str, str]:
    """A non-UTF-8 locale where the platform has one, ``TZ=Pacific/Chatham``, another hash seed
    and one generation thread. On POSIX the ``C`` locale with UTF-8 mode and locale coercion off is
    ASCII; on Windows the ANSI code page is the locale encoding once UTF-8 mode is off."""
    return _child_env(FOREIGN)


def test_the_foreign_environment_is_not_utf8() -> None:
    probe = "import locale, time; print(locale.getpreferredencoding(False)); print(time.tzname)"
    done = subprocess.run(
        [sys.executable, "-c", probe],
        env=_foreign_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    encoding, zones = done.stdout.strip().splitlines()
    if sys.platform.startswith("linux"):
        assert codecs.lookup(encoding).name != "utf-8"
        assert "+1245" in zones  # Chatham Standard Time


@pytest.mark.parametrize("child_kernel", ["python", "rust"])
def test_the_corpus_matches_in_a_foreign_environment(child_kernel: str) -> None:
    if child_kernel == "rust":
        pytest.importorskip("shape._kernel")
    env = {**_foreign_env(), "SHAPE_KERNEL": child_kernel}
    if child_kernel == "rust":
        env["SHAPE_THREADS"] = "3"
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "golden_bytes.py")],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "418 files match the golden byte corpus" in done.stdout


# ---- 6. the version guard -----------------------------------------------------------------------


def test_the_byte_stable_sinks_declare_a_format_version() -> None:
    host = default_host()
    for name in ("csv", "tsv", "jsonl"):
        assert host.get("shape.sinks", name).format_version == 1
    # 2: a PostgreSQL literal with a backslash is an E'' literal (#285, SEC-high)
    assert host.get("shape.sinks", "sql").format_version == 2
    for name in ("parquet", "ipc", "excel", "delta"):
        assert getattr(host.get("shape.sinks", name), "format_version", None) is None


def test_writer_format_versions() -> None:
    assert writer_format_versions(["sql", "csv", "parquet", "summary", "csv"]) == {
        "csv": 1,
        "sql": 2,
    }
    assert writer_format_versions([]) == {}
    assert list(writer_format_versions(["tsv", "jsonl", "csv"])) == ["csv", "jsonl", "tsv"]
    with pytest.raises(ValueError, match="unknown format"):
        writer_format_versions(["xml"])


def test_record_writers_merges_sorts_and_round_trips(tmp_path: Path) -> None:
    from shape.scenario.manifest import ManifestBuilder

    builder = ManifestBuilder()
    builder.start(None, None, "d", "small", 42)
    builder.record_writers({"sql": 1})
    builder.record_writers({"csv": 1})
    builder.record_writers({})
    manifest = builder.finish()
    assert manifest.reproducibility["writers"] == {"csv": 1, "sql": 1}
    assert list(manifest.reproducibility["writers"]) == ["csv", "sql"]
    path = tmp_path / "m_manifest.json"
    ManifestBuilder.to_file(manifest, path)
    back = ManifestBuilder.from_file(path)
    assert back.reproducibility["writers"] == {"csv": 1, "sql": 1}
    raw = json.loads(path.read_text("utf-8"))
    assert raw["version"] == 1  # additive field: the manifest version is unchanged
    del raw["reproducibility"]["writers"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert "writers" not in ManifestBuilder.from_file(path).reproducibility


@pytest.fixture(scope="module")
def retail() -> Any:
    pytest.importorskip("shape_domains")
    pytest.importorskip("yaml")
    from shape.generation.domains import load_domain

    return load_domain("retail")


def _pack_run(tmp_path: Path, retail: Any, fmt: str, out: str) -> Any:
    from shape.scenario import PackLoader, PackRunner
    from tests.scenario.conftest import PACK, write

    pack = PackLoader().load(write(tmp_path / f"{out}.yaml", PACK.format(fmt=fmt)))
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / out)
    assert result.is_success, result.errors
    return result


@pytest.mark.parametrize(("fmt", "writers"), [("csv", {"csv": 1}), ("json", {"jsonl": 1})])
def test_the_run_manifest_records_the_writer_versions(
    tmp_path: Path, retail: Any, fmt: str, writers: dict[str, int]
) -> None:
    result = _pack_run(tmp_path, retail, fmt, "out")
    raw = json.loads(Path(result.files_written[-1]).read_text("utf-8"))
    assert raw["reproducibility"]["writers"] == writers


def test_a_parquet_run_records_no_byte_stable_writer(tmp_path: Path, retail: Any) -> None:
    result = _pack_run(tmp_path, retail, "parquet", "out")
    raw = json.loads(Path(result.files_written[-1]).read_text("utf-8"))
    assert raw["reproducibility"]["writers"] == {}


def test_file_hashes_of_two_runs_match_and_leave_out_the_manifest(
    tmp_path: Path, retail: Any
) -> None:
    _pack_run(tmp_path, retail, "csv", "one")
    _pack_run(tmp_path, retail, "csv", "two")
    first, second = file_hashes(tmp_path / "one"), file_hashes(tmp_path / "two")
    assert first == second
    assert set(first) == {
        "Files/landing/customer.csv",
        "Files/landing/order.csv",
        "Files/landing/_shape_provenance.json",
    }
    assert any(p.name.endswith("_manifest.json") for p in (tmp_path / "one").iterdir())
    for path, digest in first.items():
        data = (tmp_path / "one" / path).read_bytes()
        assert digest == hashlib.sha256(data).hexdigest()


def test_file_hashes_boundaries(tmp_path: Path) -> None:
    assert file_hashes(tmp_path) == {}
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "z.csv").write_bytes(b"z\n")
    (tmp_path / "a.sql").write_bytes(b"")
    (tmp_path / "notes_manifest.json").write_text('{"format": "mine"}', encoding="utf-8")
    (tmp_path / "broken_manifest.json").write_text("{", encoding="utf-8")
    (tmp_path / "r_manifest.json").write_text(
        json.dumps({"format": "shape-run-manifest", "version": 1}), encoding="utf-8"
    )
    got = file_hashes(str(tmp_path))
    assert list(got) == ["a.sql", "b/z.csv", "broken_manifest.json", "notes_manifest.json"]
    assert got["a.sql"] == hashlib.sha256(b"").hexdigest()
    assert got["b/z.csv"] == hashlib.sha256(b"z\n").hexdigest()


def test_file_hashes_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        file_hashes(tmp_path / "missing")
    (tmp_path / "f").write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        file_hashes(tmp_path / "f")


def test_file_hashes_is_public_and_documented() -> None:
    import shape.repro

    assert shape.repro.file_hashes is file_hashes
    assert file_hashes.__doc__ and "SHA-256" in file_hashes.__doc__
    assert "file_hashes" in (ROOT / "docs" / "REPRODUCIBILITY.md").read_text("utf-8")
