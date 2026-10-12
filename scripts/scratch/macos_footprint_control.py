"""Measure the real test and one temporary growth control on a macOS runner."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

ORDERS = {
    "24-48": (24_000_000, 48_000_000),
    "48-24": (48_000_000, 24_000_000),
    "24-24": (24_000_000, 24_000_000),
}
ALLOCATOR_DIAGNOSTICS = """def allocator_stats(pool):
    import tempfile
    with tempfile.TemporaryFile() as stream:
        saved = os.dup(2)
        try:
            os.dup2(stream.fileno(), 2)
            pool.print_stats()
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        stream.seek(0)
        return stream.read().decode('utf-8', errors='replace')
"""

TEST = "tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows"
ANCHOR = b"        \"profile(sys.argv[1], mode='bounded')\\n\"\n"
INJECTION = (
    b"        \"held = bytearray(int(__import__('pathlib').Path(sys.argv[1]).stem[1:]) * 8)\\n\"\n"
    # Touch every page: lazy zero pages must not make this a virtual-only allocation.
    b"        \"held[::4096] = b'x' * ((len(held) + 4095) // 4096)\\n\"\n"
)


def run_case(label: str, output: Path) -> dict:
    log = output / f"{label}.log"
    with log.open("w") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rP", TEST],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    text = log.read_text()
    print(text, flush=True)
    peaks = {}
    diagnostics = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(\d+) rows: peak physical footprint (\d+) bytes; (.+)", line)
        if match:
            rows, peak, stderr = match.groups()
            peaks[int(rows)] = int(peak)
            diagnostics[rows] = json.loads(stderr)
    (output / f"{label}-stderr.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    record = {"case": label, "pytest_exit": result.returncode, "footprints": peaks}
    (output / f"{label}-result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    return {**record, "assertion_failed": "assert abs(peaks[48_000_000]" in text}


def diagnostic_variant(original, order):
    """Change scan order in memory, preserving all assertions and fixture generation."""
    tree = ast.parse(inspect.getsource(original))
    function = tree.body[0]
    function.decorator_list = []
    guards = [ast.dump(node) for node in ast.walk(function) if isinstance(node, ast.Assert)]
    loop = next(node for node in function.body if isinstance(node, ast.For))
    loop.iter = ast.Tuple(elts=[ast.Constant(rows) for rows in order], ctx=ast.Load())
    code_node = next(
        node
        for node in function.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "code"
    )
    code = ast.literal_eval(code_node.value)
    code = code.replace(
        "pool = pa.default_memory_pool()",
        ALLOCATOR_DIAGNOSTICS + "pool = pa.default_memory_pool()",
        1,
    )
    code = code.replace(
        "print(json.dumps({'arrow_pool':",
        "stats = allocator_stats(csv_pool)\n"
        "print(json.dumps({'csv_allocator_stats': stats, 'arrow_pool':",
        1,
    )
    compile(code, "<matrix child with allocator diagnostics>", "exec")
    code_node.value = ast.Constant(code)
    assert guards == [ast.dump(node) for node in ast.walk(function) if isinstance(node, ast.Assert)]
    namespace = dict(original.__globals__)
    exec(compile(ast.fix_missing_locations(tree), "<scratch order probe>", "exec"), namespace)
    return namespace[function.name]


def run_probe(order_name: str, iteration: int, after_heavy: bool, output: Path) -> int:
    import pytest

    order = ORDERS[order_name]
    record = {
        "order": order_name,
        "iteration": iteration,
        "after_heavy": after_heavy,
        "samples": [],
        "original_guard": "not_reached",
    }

    def wrapper(original):
        variant = diagnostic_variant(original, order)

        def probe(tmp_path):
            run = subprocess.run

            def capture(*args, **kwargs):
                result = run(*args, **kwargs)
                command = args[0] if args else kwargs.get("args", [])
                if len(command) >= 4 and command[1] == "-c" and "mode='bounded'" in command[2]:
                    rows = int(Path(command[3]).stem[1:])
                    diagnostics = next(
                        json.loads(line)
                        for line in result.stderr.splitlines()
                        if line.startswith("{")
                    )
                    sample = {
                        "position": len(record["samples"]) + 1,
                        "rows": rows,
                        "footprint": int(result.stdout.split()[-1]),
                        "child_exit": result.returncode,
                        "diagnostics": diagnostics,
                    }
                    record["samples"].append(sample)
                    (output / f"child-{sample['position']}-stderr.txt").write_text(result.stderr)
                    print("MATRIX_SAMPLE " + json.dumps(sample), flush=True)
                return result

            subprocess.run = capture
            try:
                variant(tmp_path)
                record["original_guard"] = "passed"
            except AssertionError:
                record["original_guard"] = "failed"
                raise
            except KeyError as error:
                if order_name != "24-24" or error.args != (48_000_000,):
                    raise
                # The unchanged acceptance guard requires two different sizes. This is
                # a same-size diagnostic only; report both samples without inventing 48M.
                record["original_guard"] = "not_applicable_same_size_probe"
            finally:
                subprocess.run = run
                (output / "probe-partial.json").write_text(json.dumps(record, indent=2) + "\n")

        probe.__name__ = "test_scratch_order_probe"
        return pytest.mark.heavy(probe)

    class ProbePlugin:
        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, items):
            original = next(item for item in items if item.nodeid == TEST)
            probe = wrapper(original.obj)
            if after_heavy:
                # Same pytest process, after all selected heavy items: do not precondition
                # in another subprocess, which cannot transfer the parent's allocator state.
                items.append(
                    pytest.Function.from_parent(original.parent, name=probe.__name__, callobj=probe)
                )
            else:
                original.obj = probe

    selection = ["-q", "-rP"]
    if after_heavy:
        selection += [
            "-m",
            "heavy and not realtime",
            "tests/kernel",
            "tests/profile",
            "tests/streaming",
        ]
    else:
        selection.append(TEST)
    record["pytest_exit"] = int(pytest.main(selection, plugins=[ProbePlugin()]))
    samples = record["samples"]
    complete = len(samples) == 2 and tuple(s["rows"] for s in samples) == order
    if complete:
        first, second = (s["footprint"] for s in samples)
        record["absolute_difference_fraction"] = abs(second - first) / first
    record["protocol_complete"] = complete
    (output / "probe.json").write_text(json.dumps(record, indent=2) + "\n")
    print("MATRIX_RESULT " + json.dumps(record), flush=True)
    # An observed acceptance failure is evidence, not a reason to rerun or discard it.
    # The matrix aggregator propagates pytest failures after collecting all cases.
    return 0 if complete else 1


def run_matrix(output: Path) -> int:
    records = []
    cases = [(name, iteration, False) for iteration in range(1, 6) for name in ORDERS]
    cases.append(("24-48", 1, True))
    for index, (name, iteration, after_heavy) in enumerate(cases, 1):
        directory = output / f"{index:02d}-{name}-{'after-heavy' if after_heavy else iteration}"
        directory.mkdir()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--output",
            str(directory),
            "--probe-order",
            name,
            "--iteration",
            str(iteration),
        ]
        if after_heavy:
            command.append("--after-heavy")
        with (directory / "pytest.log").open("w") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
        print((directory / "pytest.log").read_text(), flush=True)
        record_path = directory / "probe.json"
        record = (
            json.loads(record_path.read_text())
            if record_path.exists()
            else {
                "order": name,
                "iteration": iteration,
                "after_heavy": after_heavy,
                "protocol_complete": False,
            }
        )
        record["runner_exit"] = result.returncode
        records.append(record)
        (output / "matrix.json").write_text(json.dumps(records, indent=2) + "\n")
    for record in records:
        print(
            "MATRIX_TABLE "
            + json.dumps(
                {
                    key: record.get(key)
                    for key in (
                        "order",
                        "iteration",
                        "after_heavy",
                        "pytest_exit",
                        "runner_exit",
                        "original_guard",
                        "absolute_difference_fraction",
                    )
                }
                | {"footprints": [sample["footprint"] for sample in record.get("samples", [])]}
            ),
            flush=True,
        )
    # Collect every predeclared case even after a failure, then propagate failed guards.
    # This is an evidence matrix, never a retry or a selection of favourable measurements.
    passed = all(
        r["protocol_complete"]
        and r["runner_exit"] == 0
        and r.get("pytest_exit") == 0
        and r.get("absolute_difference_fraction", 1) < 0.10
        for r in records
    )
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--probe-order", choices=ORDERS)
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--after-heavy", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("requires macOS; Linux RSS is not physical-footprint evidence")
    repo = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != repo:
        parser.error("run from the repository root")
    output = args.output.resolve()
    if output == repo or repo in output.parents:
        parser.error("output must be outside the checkout")
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("checkout must be clean before the control")
    output.mkdir(parents=True, exist_ok=True)
    if args.probe_order:
        return run_probe(args.probe_order, args.iteration, args.after_heavy, output)
    if args.matrix:
        return run_matrix(output)
    source = repo / "tests/profile/test_engine.py"
    original = source.read_bytes()
    assert original.count(ANCHOR) == 1
    assert b"return mac_usage(os.getpid())['ri_lifetime_max_phys_footprint']" in original
    try:
        real = run_case("real", output)
        source.write_bytes(original.replace(ANCHOR, INJECTION + ANCHOR))
        control = run_case("control", output)
    finally:
        source.write_bytes(original)
        assert source.read_bytes() == original
        status = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        (output / "checkout-status.txt").write_text(status)
        if status.strip():
            raise RuntimeError(f"checkout is dirty after restoration: {status}")
        print("Source restored byte-identical; checkout clean.", flush=True)
    valid = True
    for case in (real, control):
        peaks = case["footprints"]
        if set(peaks) != {24_000_000, 48_000_000} or peaks[24_000_000] <= 50 * 2**20:
            valid = False
    peaks = control["footprints"]
    detected_growth = (
        valid and abs(peaks[48_000_000] - peaks[24_000_000]) / peaks[24_000_000] >= 0.10
    )
    passed = (
        real["pytest_exit"] == 0
        and control["pytest_exit"] == 1
        and control["assertion_failed"]
        and detected_growth
    )
    summary = {"real": real, "control": control, "proof_passed": passed}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
